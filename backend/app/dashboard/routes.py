"""Read-only organization views and local replay artifacts for the demo."""

import json
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from app.domain.tables import actions, payments
from app.ingestion.service import DomainError, require_org, scope

STATIC = Path(__file__).with_name("static")
router = APIRouter(tags=["dashboard"])
REPORTS = {
    "detection": "detection/detection.json",
    "diagnosis": "diagnosis/diagnosis.json",
    "experiment": "experiment/results.json",
}


def report_root() -> Path:
    return Path(os.environ.get("PAYREVIVE_DEMO_REPORTS", "work/demo-reports"))


@router.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(
        STATIC / "index.html",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/dashboard/assets/{filename}", include_in_schema=False)
def asset(filename: Literal["dashboard.css", "dashboard.js"]):
    return FileResponse(STATIC / filename)


@router.get("/v1/organizations/{org_id}/dashboard")
def overview(
    org_id: UUID,
    request: Request,
    status: Literal["all", "pending", "failed", "succeeded"] = "all",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    engine = request.app.state.engine
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        require_org(connection, org_id)
        totals = connection.execute(
            sa.select(
                payments.c.status,
                sa.func.count().label("count"),
                sa.func.sum(payments.c.amount_minor).label("amount_minor"),
            )
            .where(scope(payments, org_id))
            .group_by(payments.c.status)
        ).mappings()
        summary = {
            row["status"]: {"count": row["count"], "amount_minor": str(row["amount_minor"])}
            for row in totals
        }
        query = sa.select(
            payments.c.id,
            payments.c.reference,
            payments.c.status,
            payments.c.amount_minor,
            payments.c.created_at,
        ).where(scope(payments, org_id))
        if status != "all":
            query = query.where(payments.c.status == status)
        rows = [
            {**row, "amount_minor": str(row["amount_minor"])}
            for row in connection.execute(
                query.order_by(payments.c.created_at.desc(), payments.c.id)
                .offset(offset)
                .limit(limit + 1)
            ).mappings()
        ]
        pending = [
            dict(row)
            for row in connection.execute(
                sa.select(
                    actions.c.id, actions.c.payment_id, actions.c.due_at, payments.c.reference
                )
                .join(
                    payments,
                    sa.and_(
                        actions.c.org_id == payments.c.org_id, actions.c.payment_id == payments.c.id
                    ),
                )
                .where(scope(actions, org_id), actions.c.status == "awaiting_approval")
                .order_by(actions.c.created_at.desc(), actions.c.id)
                .limit(50)
            ).mappings()
        ]
    return {
        "summary": summary,
        "payments": rows[:limit],
        "has_more": len(rows) > limit,
        "pending_approvals": pending,
    }


@router.get("/dashboard/reports/{kind}")
def report(kind: Literal["detection", "diagnosis", "experiment"]):
    try:
        body = json.loads((report_root() / REPORTS[kind]).read_bytes())
        if not isinstance(body, dict) or body.get("synthetic_only") is not True:
            raise ValueError("Expected a synthetic result")
    except FileNotFoundError as error:
        raise DomainError(404, "Demo report has not been generated") from error
    except (OSError, ValueError) as error:
        raise DomainError(503, "Demo report is unavailable") from error
    # Keep large raw seed rows out of dashboard responses.
    if kind == "experiment":
        return {
            key: body[key]
            for key in (
                "version",
                "synthetic_only",
                "config",
                "rules_version",
                "contrasts",
            )
            if key in body
        }
    return body
