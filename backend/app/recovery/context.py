"""All state-changing recovery paths lock customer, then payment, then action."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from app.compliance.engine import EligibilityContext
from app.domain.tables import ACTIVE_STATES, actions, attempts, customers, events, payments
from app.ingestion.service import DomainError, scope


def lock_payment(connection, org_id: UUID, payment_id: UUID):
    row = (
        connection.execute(
            sa.select(payments).where(scope(payments, org_id), payments.c.id == payment_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DomainError(404, "Payment not found in organization")
    customer = None
    if row["customer_id"] is not None:
        customer = (
            connection.execute(
                sa.select(customers)
                .where(scope(customers, org_id), customers.c.id == row["customer_id"])
                .with_for_update()
            )
            .mappings()
            .one()
        )
    payment = (
        connection.execute(
            sa.select(payments)
            .where(scope(payments, org_id), payments.c.id == payment_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    return dict(payment), dict(customer) if customer else None


def read_context(connection, payment, customer, now: datetime, excluding_action=None):
    org_id, payment_id = payment["org_id"], payment["id"]
    latest = (
        connection.execute(
            sa.select(attempts)
            .where(scope(attempts, org_id), attempts.c.payment_id == payment_id)
            .order_by(attempts.c.number.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    evidence = None
    if latest:
        evidence = (
            connection.execute(
                sa.select(events)
                .where(
                    scope(events, org_id),
                    events.c.attempt_id == latest["id"],
                    events.c.status == "failed",
                )
                .order_by(events.c.occurred_at.desc(), events.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
    first_at = (
        connection.scalar(
            sa.select(sa.func.min(events.c.occurred_at)).where(
                scope(events, org_id), events.c.payment_id == payment_id
            )
        )
        or now
    )
    last_at = (
        connection.scalar(
            sa.select(sa.func.max(events.c.occurred_at)).where(
                scope(events, org_id), events.c.payment_id == payment_id
            )
        )
        or now
    )
    terminal = bool(
        connection.scalar(
            sa.select(events.c.id)
            .where(
                scope(events, org_id),
                events.c.payment_id == payment_id,
                events.c.reason.in_(["risk_block", "expired_method"]),
            )
            .limit(1)
        )
    )
    active_query = sa.select(actions.c.id).where(
        scope(actions, org_id),
        actions.c.payment_id == payment_id,
        actions.c.status.in_(ACTIVE_STATES),
    )
    if excluding_action is not None:
        active_query = active_query.where(actions.c.id != excluding_action)
    active = connection.scalar(active_query.limit(1))
    context = EligibilityContext(
        payment_status=payment["status"],
        amount_minor=payment["amount_minor"],
        customer_present=customer is not None,
        consent=bool(customer and customer["retry_consent"]),
        attempt_number=latest["number"] if latest else 0,
        last_attempt_at=last_at,
        first_attempt_at=first_at,
        failure_reason=evidence["reason"] if evidence else None,
        terminal_evidence=terminal,
        other_active_action=active is not None,
    )
    return context, dict(evidence) if evidence else None, active
