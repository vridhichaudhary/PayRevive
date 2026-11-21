"""Organization-scoped incident exposure and case investigation reads."""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime

from app.dashboard.routes import REPORTS, report, report_root
from app.domain.tables import actions, feature_snapshots, payments
from app.ingestion.service import DomainError, get_payment, require_org, scope

router = APIRouter(prefix="/v1/organizations/{org_id}/workbench", tags=["recovery workbench"])


def incidents_for(org_id: UUID):
    path = report_root() / str(org_id) / REPORTS["detection"]
    if path.is_file():
        import json

        try:
            data = json.loads(path.read_bytes())
            if data.get("synthetic_only") is not True:
                raise ValueError("Expected synthetic report")
        except (OSError, ValueError) as error:
            raise DomainError(503, "Invalid incident report") from error
    else:
        data = report("detection")
    data["generated_at"] = datetime.fromtimestamp(
        path.stat().st_mtime
        if path.is_file()
        else (report_root() / REPORTS["detection"]).stat().st_mtime,
        UTC,
    )
    provenance = data.get("provenance", {})
    if provenance.get("generator_config", {}).get("org_id") != str(org_id):
        raise DomainError(404, "No incident report for this organization")
    return data


@router.get("/incidents")
def incidents(org_id: UUID, request: Request):
    with request.app.state.engine.connect() as connection:
        require_org(connection, org_id)
    data = incidents_for(org_id)
    rows = data["evaluation"]["methods"]["rolling"]["incidents"]
    return {
        "source": data["provenance"],
        "generated_at": data["generated_at"],
        "kind": "synthetic incident replay",
        "incidents": rows,
    }


@router.get("/cases")
def cases(
    org_id: UUID,
    request: Request,
    search: str = Query("", max_length=100),
    status: str = Query("all", pattern="^(all|pending|failed|succeeded)$"),
    action_status: str = Query(
        "all",
        pattern="^(all|awaiting_approval|scheduled|unknown|succeeded|failed|cancelled|superseded|executing)$",
    ),
    incident_id: str | None = Query(None, max_length=100),
    since: AwareDatetime | None = None,
    until: AwareDatetime | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    if since and until and since >= until:
        raise DomainError(422, "End must follow start")
    condition = [scope(payments, org_id)]
    if search:
        condition.append(payments.c.reference.contains(search, autoescape=True))
    if status != "all":
        condition.append(payments.c.status == status)
    if action_status != "all":
        condition.append(
            sa.exists(
                sa.select(actions.c.id).where(
                    scope(actions, org_id),
                    actions.c.payment_id == payments.c.id,
                    actions.c.status == action_status,
                )
            )
        )
    features = [scope(feature_snapshots, org_id), feature_snapshots.c.payment_id == payments.c.id]
    if since:
        features.append(feature_snapshots.c.observed_at >= since)
    if until:
        features.append(feature_snapshots.c.observed_at < until)
    if incident_id:
        data = incidents_for(org_id)
        incident = next(
            (
                row
                for row in data["evaluation"]["methods"]["rolling"]["incidents"]
                if row["incident_id"] == incident_id
            ),
            None,
        )
        if incident is None:
            raise DomainError(404, "Incident not found")
        features += [
            feature_snapshots.c.dataset_id == data["provenance"]["dataset_id"],
            feature_snapshots.c.source_manifest_sha256
            == data["provenance"]["source_manifest_sha256"],
            feature_snapshots.c.observed_at >= datetime.fromisoformat(incident["starts_at"]),
            feature_snapshots.c.observed_at < datetime.fromisoformat(incident["ends_at"]),
            feature_snapshots.c["values"][incident["scope"]].astext == incident["target"],
        ]
    if incident_id or since or until:
        condition.append(sa.exists(sa.select(feature_snapshots.c.id).where(*features)))
    with request.app.state.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as c:
        require_org(c, org_id)
        total, amount = c.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(payments.c.amount_minor), 0),
            ).where(*condition)
        ).one()
        rows = [
            dict(row)
            for row in c.execute(
                sa.select(payments)
                .where(*condition)
                .order_by(payments.c.created_at.desc(), payments.c.id)
                .offset(offset)
                .limit(limit)
            ).mappings()
        ]
    return {
        "cases": [{**row, "amount_minor": str(row["amount_minor"])} for row in rows],
        "count": total,
        "amount_minor": str(amount),
        "has_more": offset + len(rows) < total,
        "as_of": datetime.now(UTC),
        "match": "exposure" if incident_id else None,
    }


@router.get("/cases/{payment_id}")
def case(org_id: UUID, payment_id: UUID, request: Request):
    data = get_payment(request.app.state.engine, org_id, payment_id)
    data["amount_minor"] = str(data["amount_minor"])
    for collection in ("recovery_outcomes",):
        for row in data[collection]:
            for key in ("gross_recovered_minor", "cost_minor"):
                row[key] = str(row[key])
    timeline = []
    for name, time in (
        ("events", "occurred_at"),
        ("recovery_decisions", "created_at"),
        ("recovery_actions", "created_at"),
        ("recovery_outcomes", "created_at"),
        ("audit", "created_at"),
    ):
        for row in data[name]:
            timeline.append({"type": name, "at": row[time], "record": row})
    data["timeline"] = sorted(timeline, key=lambda row: (row["at"], str(row["record"]["id"])))
    return data
