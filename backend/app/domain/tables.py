"""Explicit SQLAlchemy Core schema. Organization IDs participate in foreign keys."""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

DEMO_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
metadata = sa.MetaData()
# Include the foundation table so future autogeneration never proposes dropping it.
application_metadata = sa.Table(
    "application_metadata",
    metadata,
    sa.Column("key", sa.String(80), primary_key=True),
    sa.Column("value", sa.String(200), nullable=False),
)


def identity():
    return sa.Column("id", sa.Uuid, primary_key=True)


def organization():
    return sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id"), nullable=False)


def timestamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


organizations = sa.Table(
    "organizations",
    metadata,
    identity(),
    sa.Column("name", sa.String(100), nullable=False),
    timestamp(),
)
payments = sa.Table(
    "payments",
    metadata,
    identity(),
    organization(),
    sa.Column("reference", sa.String(100), nullable=False),
    sa.Column("amount_minor", sa.BigInteger, nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    timestamp(),
    sa.CheckConstraint("amount_minor > 0", name="positive_payment_amount"),
    sa.CheckConstraint("currency = 'INR'", name="inr_only"),
    sa.CheckConstraint("status IN ('pending','failed','succeeded')", name="payment_status"),
    sa.UniqueConstraint("org_id", "reference"),
    sa.UniqueConstraint("org_id", "id"),
)
attempts = sa.Table(
    "payment_attempts",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("number", sa.Integer, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("state_at", sa.DateTime(timezone=True), nullable=False),
    timestamp(),
    sa.CheckConstraint("number > 0", name="positive_attempt_number"),
    sa.CheckConstraint("status IN ('pending','failed','succeeded')", name="attempt_status"),
    sa.ForeignKeyConstraint(["org_id", "payment_id"], ["payments.org_id", "payments.id"]),
    sa.UniqueConstraint("org_id", "payment_id", "number"),
    sa.UniqueConstraint("org_id", "payment_id", "id"),
)
events = sa.Table(
    "payment_events",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("attempt_id", sa.Uuid, nullable=False),
    sa.Column("provider", sa.String(20), nullable=False),
    sa.Column("account", sa.String(100), nullable=False),
    sa.Column("source_event_id", sa.String(100), nullable=False),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("reason", sa.String(40)),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    timestamp("received_at"),
    sa.CheckConstraint("provider = 'simulator'", name="simulator_only"),
    sa.CheckConstraint("status IN ('pending','failed','succeeded')", name="event_status"),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "attempt_id"],
        ["payment_attempts.org_id", "payment_attempts.payment_id", "payment_attempts.id"],
    ),
    sa.UniqueConstraint("org_id", "provider", "account", "source_event_id"),
    sa.UniqueConstraint("org_id", "id"),
)
audit = sa.Table(
    "audit_events",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.String(60), nullable=False),
    sa.Column("actor", sa.String(40), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(["org_id", "payment_id"], ["payments.org_id", "payments.id"]),
)
outbox = sa.Table(
    "outbox_messages",
    metadata,
    identity(),
    organization(),
    sa.Column("event_id", sa.Uuid, nullable=False),
    sa.Column("topic", sa.String(60), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    timestamp(),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(["org_id", "event_id"], ["payment_events.org_id", "payment_events.id"]),
    sa.UniqueConstraint("org_id", "event_id", "topic"),
)
sa.Index("ix_events_payment_time", events.c.org_id, events.c.payment_id, events.c.occurred_at)
sa.Index("ix_audit_payment_time", audit.c.org_id, audit.c.payment_id, audit.c.created_at)
sa.Index("ix_outbox_pending", outbox.c.created_at, postgresql_where=outbox.c.published_at.is_(None))
