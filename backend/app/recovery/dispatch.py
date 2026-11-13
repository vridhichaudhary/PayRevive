"""PostgreSQL owns pending work; Redis is a replaceable at-least-once transport."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from app.domain.tables import actions, events, outbox, payments
from app.ingestion.service import DomainError, append_audit, scope
from app.recovery.context import lock_payment
from app.recovery.planning import recommend


def consume_event(engine, org_id: UUID, event_id: UUID):
    with engine.connect() as connection:
        event = (
            connection.execute(
                sa.select(events).where(scope(events, org_id), events.c.id == event_id)
            )
            .mappings()
            .one_or_none()
        )
        if event is None:
            raise DomainError(404, "Event not found in organization")
        customer_id = connection.scalar(
            sa.select(payments.c.customer_id).where(
                scope(payments, org_id), payments.c.id == event["payment_id"]
            )
        )
    if customer_id is not None:
        if event["status"] == "failed":
            recommend(engine, org_id, event["payment_id"])
        elif event["status"] == "succeeded":
            with engine.begin() as connection:
                payment, _ = lock_payment(connection, org_id, event["payment_id"])
                if payment["status"] == "succeeded":
                    ids = connection.execute(
                        actions.update()
                        .where(
                            scope(actions, org_id),
                            actions.c.payment_id == payment["id"],
                            actions.c.status.in_(["scheduled", "awaiting_approval"]),
                        )
                        .values(status="superseded")
                        .returning(actions.c.id)
                    ).scalars()
                    for action_id in ids:
                        append_audit(
                            connection,
                            org_id,
                            payment["id"],
                            "recovery.superseded",
                            {"action_id": str(action_id), "reason": "payment_succeeded"},
                        )
    # A crash before this update simply replays an idempotent recommendation/cancellation.
    with engine.begin() as connection:
        connection.execute(
            outbox.update()
            .where(
                scope(outbox, org_id),
                outbox.c.event_id == event_id,
                outbox.c.processed_at.is_(None),
            )
            .values(processed_at=datetime.now(UTC))
        )


def dispatch(engine, publisher, now=None, batch_size=50, org_id=None):
    now = now or datetime.now(UTC)
    sent = 0
    with engine.begin() as connection:
        pending = connection.execute(
            sa.select(outbox)
            .where(
                sa.true() if org_id is None else outbox.c.org_id == org_id,
                outbox.c.processed_at.is_(None),
                sa.or_(
                    outbox.c.published_at.is_(None),
                    outbox.c.published_at < now - timedelta(seconds=30),
                ),
            )
            .order_by(outbox.c.created_at, outbox.c.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).mappings()
        for row in pending:
            publisher("payrevive.recovery_event", [str(row["org_id"]), str(row["event_id"])])
            connection.execute(
                outbox.update()
                .where(scope(outbox, row["org_id"]), outbox.c.id == row["id"])
                .values(published_at=now)
            )
            sent += 1
    with engine.connect() as connection:
        due = list(
            connection.execute(
                sa.select(actions)
                .where(
                    sa.true() if org_id is None else actions.c.org_id == org_id,
                    sa.or_(
                        sa.and_(actions.c.status == "scheduled", actions.c.due_at <= now),
                        sa.and_(
                            actions.c.status.in_(["executing", "unknown"]),
                            actions.c.next_reconcile_at <= now,
                            actions.c.requires_review.is_(False),
                        ),
                    ),
                )
                .order_by(actions.c.created_at, actions.c.id)
                .limit(batch_size)
            ).mappings()
        )
    for action in due:
        task = (
            "payrevive.recovery_execute"
            if action["status"] == "scheduled"
            else "payrevive.recovery_reconcile"
        )
        publisher(task, [str(action["org_id"]), str(action["id"])])
        sent += 1
    return sent
