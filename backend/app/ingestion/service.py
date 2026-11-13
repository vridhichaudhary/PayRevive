import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from app.domain.state import next_attempt_state
from app.domain.tables import (
    actions,
    attempts,
    audit,
    customers,
    decisions,
    events,
    organizations,
    outbox,
    outcomes,
    payments,
)
from app.ingestion.schemas import EventInput, PaymentInput


class DomainError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def scope(table: sa.Table, org_id: UUID):
    return table.c.org_id == org_id


def require_org(connection: Connection, org_id: UUID) -> None:
    if not connection.execute(
        sa.select(organizations.c.id).where(organizations.c.id == org_id)
    ).first():
        raise DomainError(404, "Organization not found")


def append_audit(
    connection: Connection,
    org_id: UUID,
    payment_id: UUID,
    kind: str,
    payload: dict[str, Any],
    actor: str = "local_simulator",
) -> None:
    connection.execute(
        audit.insert().values(
            id=uuid4(),
            org_id=org_id,
            payment_id=payment_id,
            kind=kind,
            actor=actor,
            payload=payload,
        )
    )


def create_payment(engine: Engine, org_id: UUID, data: PaymentInput) -> tuple[dict[str, Any], bool]:
    with engine.begin() as connection:
        require_org(connection, org_id)
        if data.customer_id is not None and not connection.scalar(
            sa.select(customers.c.id).where(
                scope(customers, org_id), customers.c.id == data.customer_id
            )
        ):
            raise DomainError(404, "Customer not found in organization")
        payment_id = connection.execute(
            insert(payments)
            .values(id=uuid4(), org_id=org_id, **data.model_dump())
            .on_conflict_do_nothing(index_elements=[payments.c.org_id, payments.c.reference])
            .returning(payments.c.id)
        ).scalar_one_or_none()
        row = (
            connection.execute(
                sa.select(payments).where(
                    scope(payments, org_id), payments.c.reference == data.reference
                )
            )
            .mappings()
            .one()
        )
        if (
            row["amount_minor"] != data.amount_minor
            or row["currency"] != data.currency
            or row["customer_id"] != data.customer_id
        ):
            raise DomainError(409, "Payment reference already has different immutable details")
        if payment_id:
            append_audit(connection, org_id, payment_id, "payment.created", {})
        return dict(row), payment_id is None


def ingest_event(
    engine: Engine, org_id: UUID, payment_id: UUID, data: EventInput
) -> dict[str, Any]:
    canonical = json.dumps(
        {"payment_id": str(payment_id), **data.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    key = json.dumps([str(org_id), data.provider, data.account, data.source_event_id])
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    with engine.begin() as connection:
        # A transaction advisory lock serializes a source identity even across payments.
        # Hash collisions only serialize unrelated work. The unique constraint is authoritative.
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        payment = (
            connection.execute(
                sa.select(payments)
                .where(scope(payments, org_id), payments.c.id == payment_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if payment is None:
            raise DomainError(404, "Payment not found in organization")
        existing = (
            connection.execute(
                sa.select(events).where(
                    scope(events, org_id),
                    events.c.provider == data.provider,
                    events.c.account == data.account,
                    events.c.source_event_id == data.source_event_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise DomainError(409, "Event identity reused with different content")
            return {"event_id": existing["id"], "duplicate": True}
        reserved = connection.scalar(
            sa.select(actions.c.attempt_number).where(
                scope(actions, org_id),
                actions.c.payment_id == payment_id,
                actions.c.status.in_(("executing", "unknown")),
            )
        )
        if reserved is not None and data.attempt_number >= reserved:
            raise DomainError(409, "Attempt number reserved by an unresolved recovery operation")
        attempt = (
            connection.execute(
                sa.select(attempts).where(
                    scope(attempts, org_id),
                    attempts.c.payment_id == payment_id,
                    attempts.c.number == data.attempt_number,
                )
            )
            .mappings()
            .one_or_none()
        )
        if attempt is None:
            # Missing earlier deliveries are valid; don't fabricate attempts 1..N-1.
            attempt_id = uuid4()
            connection.execute(
                attempts.insert().values(
                    id=attempt_id,
                    org_id=org_id,
                    payment_id=payment_id,
                    number=data.attempt_number,
                    status=data.status,
                    state_at=data.occurred_at,
                )
            )
        else:
            attempt_id = attempt["id"]
            status, state_at = next_attempt_state(
                attempt["status"], attempt["state_at"], data.status, data.occurred_at
            )
            connection.execute(
                attempts.update()
                .where(scope(attempts, org_id), attempts.c.id == attempt_id)
                .values(status=status, state_at=state_at)
            )
        event_id = uuid4()
        connection.execute(
            events.insert().values(
                id=event_id,
                org_id=org_id,
                payment_id=payment_id,
                attempt_id=attempt_id,
                provider=data.provider,
                account=data.account,
                source_event_id=data.source_event_id,
                fingerprint=fingerprint,
                status=data.status,
                reason=data.reason,
                occurred_at=data.occurred_at,
            )
        )
        latest = connection.execute(
            sa.select(attempts.c.status)
            .where(scope(attempts, org_id), attempts.c.payment_id == payment_id)
            .order_by(attempts.c.number.desc())
            .limit(1)
        ).scalar_one()
        status = (
            "succeeded"
            if (payment["status"] == "succeeded" or data.status == "succeeded")
            else latest
        )
        connection.execute(
            payments.update()
            .where(scope(payments, org_id), payments.c.id == payment_id)
            .values(status=status)
        )
        payload = {"org_id": str(org_id), "payment_id": str(payment_id), "event_id": str(event_id)}
        append_audit(connection, org_id, payment_id, "payment.event_received", payload)
        connection.execute(
            outbox.insert().values(
                id=uuid4(),
                org_id=org_id,
                event_id=event_id,
                topic="payment.event_received",
                payload=payload,
            )
        )
        return {"event_id": event_id, "duplicate": False}


def get_payment(engine: Engine, org_id: UUID, payment_id: UUID) -> dict[str, Any]:
    # A repeatable-read snapshot keeps the timeline consistent with the returned current state.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            row = (
                connection.execute(
                    sa.select(payments).where(scope(payments, org_id), payments.c.id == payment_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError(404, "Payment not found in organization")
            result = dict(row)
            for name, table, order in [
                ("attempts", attempts, attempts.c.number),
                ("events", events, events.c.occurred_at),
                ("audit", audit, audit.c.created_at),
                ("recovery_decisions", decisions, decisions.c.created_at),
                ("recovery_actions", actions, actions.c.created_at),
                ("recovery_outcomes", outcomes, outcomes.c.created_at),
            ]:
                result[name] = [
                    dict(item)
                    for item in connection.execute(
                        sa.select(table)
                        .where(scope(table, org_id), table.c.payment_id == payment_id)
                        .order_by(order, table.c.id)
                    ).mappings()
                ]
            return result
