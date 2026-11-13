import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.compliance.engine import assess
from app.config import get_settings
from app.decision.policy import propose
from app.domain.tables import actions, decisions
from app.ingestion.service import DomainError, append_audit, scope
from app.recovery.context import lock_payment, read_context


def current_rules():
    return get_settings().recovery_rules


def recommend(engine: Engine, org_id: UUID, payment_id: UUID, now: datetime | None = None):
    now = now or datetime.now(UTC)
    rules = current_rules()
    if rules is None:
        raise DomainError(503, "Eligibility rules unavailable")
    with engine.begin() as connection:
        payment, customer = lock_payment(connection, org_id, payment_id)
        context, evidence, active = read_context(connection, payment, customer, now)
        if active:
            existing_action = (
                connection.execute(
                    sa.select(actions).where(scope(actions, org_id), actions.c.id == active)
                )
                .mappings()
                .one()
            )
            existing_decision = (
                connection.execute(
                    sa.select(decisions).where(
                        scope(decisions, org_id), decisions.c.id == existing_action["decision_id"]
                    )
                )
                .mappings()
                .one()
            )
            if (
                existing_decision["evidence_event_id"] == (evidence["id"] if evidence else None)
                and existing_decision["policy_version"] == rules.version
                and customer is not None
                and existing_action["customer_revision"] == customer["revision"]
            ):
                return {"decision": dict(existing_decision), "action": dict(existing_action)}
        proposal = propose(
            evidence["reason"] if evidence else None,
            evidence["occurred_at"] if evidence else now,
            now,
            rules,
        )
        eligibility = assess(context, proposal, rules, now)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "policy": rules.version,
                    "evidence": str(evidence["id"]) if evidence else None,
                    "revision": customer["revision"] if customer else None,
                    "status": payment["status"],
                    "attempt": context.attempt_number,
                    "active": str(active) if active else None,
                    "age_expired": eligibility.reason == "recovery_window_expired",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = (
            connection.execute(
                sa.select(decisions).where(
                    scope(decisions, org_id),
                    decisions.c.payment_id == payment_id,
                    decisions.c.context_hash == fingerprint,
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            action = (
                connection.execute(
                    sa.select(actions).where(
                        scope(actions, org_id), actions.c.decision_id == existing["id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            return {"decision": dict(existing), "action": dict(action) if action else None}
        decision_id = uuid4()
        decision = {
            "id": decision_id,
            "org_id": org_id,
            "payment_id": payment_id,
            "evidence_event_id": evidence["id"] if evidence else None,
            "context_hash": fingerprint,
            "policy_version": rules.version,
            "policy_snapshot": rules.model_dump(),
            "proposal": proposal.model_dump(mode="json"),
            "eligibility": asdict(eligibility),
        }
        connection.execute(decisions.insert().values(**decision))
        action = None
        if eligibility.allowed:
            action = {
                "id": uuid4(),
                "org_id": org_id,
                "payment_id": payment_id,
                "decision_id": decision_id,
                "customer_revision": customer["revision"],
                "status": "awaiting_approval" if eligibility.approval_required else "scheduled",
                "due_at": proposal.due_at,
            }
            connection.execute(actions.insert().values(**action))
        append_audit(
            connection,
            org_id,
            payment_id,
            "recovery.recommended",
            {
                "decision_id": str(decision_id),
                "action_id": str(action["id"]) if action else None,
                "policy_version": rules.version,
                "proposal": proposal.kind,
                "eligibility": asdict(eligibility),
            },
        )
        return {"decision": decision, "action": action}
