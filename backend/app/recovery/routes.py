from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import StrictBool

from app.domain.tables import actions, customers, preferences
from app.ingestion.schemas import Identifier, InputModel
from app.ingestion.service import DomainError, append_audit, require_org, scope
from app.recovery import execution, planning
from app.recovery.customers import create_customer, set_consent

router = APIRouter(prefix="/v1/organizations/{org_id}", tags=["simulated recovery"])


class CustomerInput(InputModel):
    reference: Identifier
    retry_consent: StrictBool = False


class ConsentInput(InputModel):
    retry_consent: StrictBool


@router.post("/customers")
def create(org_id: UUID, body: CustomerInput, request: Request):
    row, duplicate = create_customer(
        request.app.state.engine, org_id, body.reference, body.retry_consent
    )
    return JSONResponse(status_code=200 if duplicate else 201, content=jsonable_encoder(row))


@router.get("/customers/{customer_id}")
def customer(org_id: UUID, customer_id: UUID, request: Request):
    with request.app.state.engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(customers).where(scope(customers, org_id), customers.c.id == customer_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(404, "Customer not found in organization")
        history = connection.execute(
            sa.select(preferences)
            .where(scope(preferences, org_id), preferences.c.customer_id == customer_id)
            .order_by(preferences.c.revision)
        ).mappings()
        return {**dict(row), "preference_history": [dict(item) for item in history]}


@router.put("/customers/{customer_id}/consent")
def consent(org_id: UUID, customer_id: UUID, body: ConsentInput, request: Request):
    return set_consent(request.app.state.engine, org_id, customer_id, body.retry_consent)


@router.get("/recovery/policy")
def policy(org_id: UUID, request: Request):
    with request.app.state.engine.connect() as connection:
        require_org(connection, org_id)
    rules = planning.current_rules()
    if rules is None:
        raise DomainError(503, "Eligibility rules unavailable")
    return {"simulation_only": True, "version": rules.version, "rules": rules.model_dump()}


@router.post("/payments/{payment_id}/recommend")
def recommend(org_id: UUID, payment_id: UUID, request: Request):
    return planning.recommend(request.app.state.engine, org_id, payment_id)


@router.get("/recovery/actions")
def list_actions(org_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100)):
    with request.app.state.engine.connect() as connection:
        rows = connection.execute(
            sa.select(actions)
            .where(scope(actions, org_id))
            .order_by(actions.c.created_at.desc(), actions.c.id)
            .limit(limit)
        ).mappings()
        return [dict(row) for row in rows]


@router.get("/recovery/actions/{action_id}")
def detail(org_id: UUID, action_id: UUID, request: Request):
    return execution.read_action(request.app.state.engine, org_id, action_id)


@router.post("/recovery/actions/{action_id}/approve")
def approve(org_id: UUID, action_id: UUID, request: Request):
    return execution.approve(request.app.state.engine, org_id, action_id)


@router.post("/recovery/actions/{action_id}/execute")
def execute(org_id: UUID, action_id: UUID, request: Request):
    return execution.execute(request.app.state.engine, org_id, action_id)


@router.post("/recovery/actions/{action_id}/reconcile")
def reconcile(org_id: UUID, action_id: UUID, request: Request):
    return execution.reconcile(request.app.state.engine, org_id, action_id)


@router.post("/recovery/actions/{action_id}/cancel")
def cancel(org_id: UUID, action_id: UUID, request: Request):
    engine = request.app.state.engine
    with engine.begin() as connection:
        payment, _, action, _ = execution.locked_action(connection, org_id, action_id)
        if action["status"] in {"executing", "unknown"}:
            raise DomainError(409, "Dispatched operations must be reconciled, not cancelled")
        if action["status"] in {"scheduled", "awaiting_approval"}:
            connection.execute(
                actions.update()
                .where(scope(actions, org_id), actions.c.id == action_id)
                .values(status="cancelled")
            )
            append_audit(
                connection,
                org_id,
                payment["id"],
                "recovery.cancelled",
                {"action_id": str(action_id), "reason": "operator_cancelled"},
            )
    return execution.read_action(engine, org_id, action_id)
