from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from app.domain.tables import actions, customers, payments, preferences
from app.ingestion.service import DomainError, append_audit, require_org, scope


def create_customer(engine: Engine, org_id: UUID, reference: str, consent: bool = False):
    with engine.begin() as connection:
        require_org(connection, org_id)
        created = connection.execute(
            insert(customers)
            .values(id=uuid4(), org_id=org_id, reference=reference, retry_consent=consent)
            .on_conflict_do_nothing(index_elements=[customers.c.org_id, customers.c.reference])
            .returning(customers.c.id)
        ).scalar_one_or_none()
        row = (
            connection.execute(
                sa.select(customers).where(
                    scope(customers, org_id), customers.c.reference == reference
                )
            )
            .mappings()
            .one()
        )
        if created:
            connection.execute(
                preferences.insert().values(
                    id=uuid4(),
                    org_id=org_id,
                    customer_id=created,
                    revision=1,
                    retry_consent=consent,
                )
            )
        return dict(row), created is None


def set_consent(engine: Engine, org_id: UUID, customer_id: UUID, consent: bool):
    with engine.begin() as connection:
        row = (
            connection.execute(
                sa.select(customers)
                .where(scope(customers, org_id), customers.c.id == customer_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(404, "Customer not found in organization")
        if row["retry_consent"] == consent:
            return dict(row)
        revision = row["revision"] + 1
        connection.execute(
            customers.update()
            .where(scope(customers, org_id), customers.c.id == customer_id)
            .values(retry_consent=consent, revision=revision)
        )
        connection.execute(
            preferences.insert().values(
                id=uuid4(),
                org_id=org_id,
                customer_id=customer_id,
                revision=revision,
                retry_consent=consent,
            )
        )
        owned = connection.execute(
            sa.select(payments)
            .where(scope(payments, org_id), payments.c.customer_id == customer_id)
            .order_by(payments.c.id)
            .with_for_update()
        ).mappings()
        for payment in owned:
            append_audit(
                connection,
                org_id,
                payment["id"],
                "customer.preference_changed",
                {"customer_id": str(customer_id), "revision": revision, "retry_consent": consent},
            )
            if not consent:
                cancelled = connection.execute(
                    actions.update()
                    .where(
                        scope(actions, org_id),
                        actions.c.payment_id == payment["id"],
                        actions.c.status.in_(["scheduled", "awaiting_approval"]),
                    )
                    .values(status="cancelled")
                    .returning(actions.c.id)
                ).scalars()
                for action_id in cancelled:
                    append_audit(
                        connection,
                        org_id,
                        payment["id"],
                        "recovery.cancelled",
                        {"action_id": str(action_id), "reason": "customer_opted_out"},
                    )
        return {**dict(row), "retry_consent": consent, "revision": revision}
