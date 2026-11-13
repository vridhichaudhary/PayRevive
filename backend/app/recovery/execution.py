"""Claim, call, finalize. No database row lock is held across the processor call."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.compliance.engine import assess
from app.decision.policy import Proposal
from app.domain.tables import (
    actions,
    attempts,
    decisions,
    events,
    operations,
    outbox,
    outcomes,
    payments,
)
from app.execution.simulator import SimulatorGateway, request_hash
from app.ingestion.service import DomainError, append_audit, scope
from app.recovery import planning
from app.recovery.context import lock_payment, read_context

LEASE_SECONDS = 60
MAX_RECONCILIATIONS = 5
TERMINAL = {"succeeded", "failed", "cancelled", "superseded"}


def read_action(engine: Engine, org_id: UUID, action_id: UUID) -> dict:
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(actions).where(scope(actions, org_id), actions.c.id == action_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(404, "Action not found in organization")
        return dict(row)


def locked_action(connection, org_id, action_id):
    pointer = connection.execute(
        sa.select(actions.c.payment_id).where(scope(actions, org_id), actions.c.id == action_id)
    ).scalar_one_or_none()
    if pointer is None:
        raise DomainError(404, "Action not found in organization")
    payment, customer = lock_payment(connection, org_id, pointer)
    action = (
        connection.execute(
            sa.select(actions)
            .where(scope(actions, org_id), actions.c.id == action_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    decision = (
        connection.execute(
            sa.select(decisions).where(
                scope(decisions, org_id), decisions.c.id == action["decision_id"]
            )
        )
        .mappings()
        .one()
    )
    return payment, customer, dict(action), dict(decision)


def _stop(connection, action, reason):
    status = (
        "superseded"
        if reason in {"payment_not_failed", "stale_evidence", "policy_changed"}
        else "cancelled"
    )
    connection.execute(
        actions.update()
        .where(scope(actions, action["org_id"]), actions.c.id == action["id"])
        .values(status=status, next_reconcile_at=None)
    )
    append_audit(
        connection,
        action["org_id"],
        action["payment_id"],
        "recovery." + status,
        {"action_id": str(action["id"]), "reason": reason},
    )


def _eligibility(connection, payment, customer, action, decision, now, *, executing):
    rules = planning.current_rules()
    if rules is None:
        return None, "eligibility_unavailable"
    if decision["policy_version"] != rules.version:
        return None, "policy_changed"
    context, evidence, _ = read_context(
        connection, payment, customer, now, excluding_action=action["id"]
    )
    if (evidence["id"] if evidence else None) != decision["evidence_event_id"]:
        return None, "stale_evidence"
    if customer is None or customer["revision"] != action["customer_revision"]:
        return None, "missing_consent"
    result = assess(
        context,
        Proposal.model_validate(decision["proposal"]),
        rules,
        now,
        executing=executing,
        approved=action["approved_at"] is not None,
    )
    return context, result.reason if not result.allowed else None


def approve(engine, org_id, action_id, now=None):
    now = now or datetime.now(UTC)
    with engine.begin() as connection:
        payment, customer, action, decision = locked_action(connection, org_id, action_id)
        if action["status"] != "awaiting_approval":
            return action
        _, reason = _eligibility(
            connection, payment, customer, action, decision, now, executing=False
        )
        if reason == "eligibility_unavailable":
            raise DomainError(503, "Eligibility rules unavailable")
        if reason:
            _stop(connection, action, reason)
        else:
            connection.execute(
                actions.update()
                .where(scope(actions, org_id), actions.c.id == action_id)
                .values(status="scheduled", approved_at=now, approved_by="local_operator")
            )
            append_audit(
                connection,
                org_id,
                payment["id"],
                "recovery.approved",
                {"action_id": str(action_id), "policy_version": decision["policy_version"]},
                actor="local_operator",
            )
    return read_action(engine, org_id, action_id)


def _request(payment, action, decision):
    proposal = Proposal.model_validate(decision["proposal"])
    return {
        "action_id": action["id"],
        "org_id": action["org_id"],
        "payment_id": payment["id"],
        "amount_minor": payment["amount_minor"],
        "attempt_number": action["attempt_number"],
        "reason": proposal.reason,
        "cost_minor": proposal.cost_minor,
    }


def _claim(engine, org_id, action_id, now, *, resume=False):
    with engine.begin() as connection:
        payment, customer, action, decision = locked_action(connection, org_id, action_id)
        if action["status"] in TERMINAL or action["status"] == "awaiting_approval":
            return None
        if resume and action["status"] in {"executing", "unknown"}:
            receipt = connection.scalar(
                sa.select(operations.c.receipt).where(
                    scope(operations, org_id), operations.c.id == action_id
                )
            )
            if receipt is not None:
                return {"recorded_receipt": receipt}
        if not resume and action["status"] != "scheduled":
            return None
        if resume and (
            action["status"] not in {"executing", "unknown"}
            or (action["status"] == "executing" and action["lease_expires_at"] > now)
        ):
            return None
        if action["requires_review"]:
            return None
        context, reason = _eligibility(
            connection, payment, customer, action, decision, now, executing=True
        )
        if reason == "eligibility_unavailable":
            raise DomainError(503, "Eligibility rules unavailable")
        if reason in {"not_due", "approval_required"}:
            return None
        if reason:
            _stop(connection, action, reason)
            return None
        count = action["reconcile_count"] + (1 if resume else 0)
        if count >= MAX_RECONCILIATIONS:
            connection.execute(
                actions.update()
                .where(scope(actions, org_id), actions.c.id == action_id)
                .values(status="unknown", requires_review=True, next_reconcile_at=None)
            )
            append_audit(
                connection,
                org_id,
                payment["id"],
                "recovery.review_required",
                {"action_id": str(action_id)},
            )
            return None
        number = action["attempt_number"] if resume else context.attempt_number + 1
        action.update(
            status="executing",
            attempt_number=number,
            started_at=now,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            next_reconcile_at=now + timedelta(seconds=LEASE_SECONDS),
            reconcile_count=count,
        )
        connection.execute(
            actions.update()
            .where(scope(actions, org_id), actions.c.id == action_id)
            .values(
                **{
                    key: action[key]
                    for key in [
                        "status",
                        "attempt_number",
                        "started_at",
                        "lease_expires_at",
                        "next_reconcile_at",
                        "reconcile_count",
                    ]
                }
            )
        )
        append_audit(
            connection,
            org_id,
            payment["id"],
            "recovery.claimed",
            {"action_id": str(action_id), "attempt_number": number, "resumed": resume},
        )
        return _request(payment, action, decision)


def _unknown(engine, org_id, action_id, now, reason):
    with engine.begin() as connection:
        payment, _, action, _ = locked_action(connection, org_id, action_id)
        if action["status"] in TERMINAL:
            return
        count = action["reconcile_count"] + 1
        review = count >= MAX_RECONCILIATIONS
        connection.execute(
            actions.update()
            .where(scope(actions, org_id), actions.c.id == action_id)
            .values(
                status="unknown",
                reconcile_count=count,
                requires_review=review,
                next_reconcile_at=None
                if review
                else now + timedelta(seconds=min(5 * 2**count, 300)),
            )
        )
        append_audit(
            connection,
            org_id,
            payment["id"],
            "recovery.outcome_unknown",
            {"action_id": str(action_id), "reason": reason, "requires_review": review},
        )


def _finalize(engine, org_id, action_id, receipt, now):
    with engine.begin() as connection:
        payment, _, action, decision = locked_action(connection, org_id, action_id)
        if action["status"] in TERMINAL:
            return
        request = _request(payment, action, decision)
        if (
            receipt["request_hash"] != request_hash(request)
            or receipt["operation_id"] != str(action_id)
            or receipt["org_id"] != str(org_id)
            or receipt["payment_id"] != str(payment["id"])
            or receipt["amount_minor"] != payment["amount_minor"]
            or receipt["cost_minor"] != request["cost_minor"]
            or receipt["status"] not in {"failed", "succeeded"}
            or receipt["reason"]
            != (None if receipt["status"] == "succeeded" else request["reason"])
            or receipt["simulator_version"] != "simulator-v1"
        ):
            raise DomainError(409, "Receipt conflicts with authorized operation")
        occurred_at = datetime.fromisoformat(receipt["occurred_at"])
        if occurred_at.tzinfo is None or occurred_at > now:
            raise DomainError(409, "Receipt has an invalid observation time")
        attempt_id, event_id = uuid4(), uuid4()
        connection.execute(
            attempts.insert().values(
                id=attempt_id,
                org_id=org_id,
                payment_id=payment["id"],
                number=action["attempt_number"],
                status=receipt["status"],
                state_at=occurred_at,
            )
        )
        connection.execute(
            events.insert().values(
                id=event_id,
                org_id=org_id,
                payment_id=payment["id"],
                attempt_id=attempt_id,
                provider="simulator",
                account="payrevive_recovery",
                source_event_id=f"rcv_{action_id}",
                fingerprint=receipt["request_hash"],
                status=receipt["status"],
                reason=receipt["reason"],
                occurred_at=occurred_at,
            )
        )
        status = "succeeded" if payment["status"] == "succeeded" else receipt["status"]
        connection.execute(
            payments.update()
            .where(scope(payments, org_id), payments.c.id == payment["id"])
            .values(status=status)
        )
        gross = (
            payment["amount_minor"]
            if receipt["status"] == "succeeded" and (payment["status"] != "succeeded")
            else 0
        )
        connection.execute(
            outcomes.insert().values(
                id=uuid4(),
                org_id=org_id,
                payment_id=payment["id"],
                action_id=action_id,
                result=receipt["status"],
                gross_recovered_minor=gross,
                cost_minor=receipt["cost_minor"],
            )
        )
        payload = {
            "org_id": str(org_id),
            "payment_id": str(payment["id"]),
            "event_id": str(event_id),
        }
        connection.execute(
            outbox.insert().values(
                id=uuid4(),
                org_id=org_id,
                event_id=event_id,
                topic="payment.event_received",
                payload=payload,
            )
        )
        connection.execute(
            actions.update()
            .where(scope(actions, org_id), actions.c.id == action_id)
            .values(status=receipt["status"], next_reconcile_at=None, requires_review=False)
        )
        append_audit(
            connection,
            org_id,
            payment["id"],
            "recovery.outcome_recorded",
            {
                "action_id": str(action_id),
                "event_id": str(event_id),
                "result": receipt["status"],
                "gross_recovered_minor": gross,
                "cost_minor": receipt["cost_minor"],
                "incremental_value": "not_estimated",
            },
        )


def _drive(engine, org_id, action_id, request, gateway, now):
    try:
        receipt = gateway.execute(request, now)
        _finalize(engine, org_id, action_id, receipt, now)
    except Exception:
        # Never turn a transport/storage exception into a failed financial attempt.
        _unknown(engine, org_id, action_id, now, "processor_or_recording_unavailable")
    return read_action(engine, org_id, action_id)


def execute(engine: Engine, org_id: UUID, action_id: UUID, *, gateway=None, now=None):
    now = now or datetime.now(UTC)
    request = _claim(engine, org_id, action_id, now)
    if request is None:
        return read_action(engine, org_id, action_id)
    return _drive(engine, org_id, action_id, request, gateway or SimulatorGateway(engine), now)


def reconcile(engine: Engine, org_id: UUID, action_id: UUID, *, gateway=None, now=None):
    now, gateway = now or datetime.now(UTC), gateway or SimulatorGateway(engine)
    action = read_action(engine, org_id, action_id)
    if action["status"] not in {"executing", "unknown"}:
        return action
    try:
        receipt = gateway.lookup(org_id, action_id)
        if receipt is not None:
            _finalize(engine, org_id, action_id, receipt, now)
            return read_action(engine, org_id, action_id)
    except Exception:
        _unknown(engine, org_id, action_id, now, "reconciliation_unavailable")
        return read_action(engine, org_id, action_id)
    if not gateway.absence_is_authoritative:
        _unknown(engine, org_id, action_id, now, "absence_not_authoritative")
        return read_action(engine, org_id, action_id)
    # Only the local simulator can prove no operation exists. Never generalize this to a sandbox.
    request = _claim(engine, org_id, action_id, now, resume=True)
    if request is None:
        return read_action(engine, org_id, action_id)
    if "recorded_receipt" in request:
        _finalize(engine, org_id, action_id, request["recorded_receipt"], now)
        return read_action(engine, org_id, action_id)
    return _drive(engine, org_id, action_id, request, gateway, now)
