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


# Online consent is explicit. Legacy payments remain unlinked and cannot be retried.
customers = sa.Table(
    "customers",
    metadata,
    identity(),
    organization(),
    sa.Column("reference", sa.String(100), nullable=False),
    sa.Column("retry_consent", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
    timestamp(),
    sa.UniqueConstraint("org_id", "reference"),
    sa.UniqueConstraint("org_id", "id"),
    sa.CheckConstraint("revision > 0", name="positive_customer_revision"),
)
payments.append_column(sa.Column("customer_id", sa.Uuid, nullable=True))
payments.append_constraint(
    sa.ForeignKeyConstraint(
        ["org_id", "customer_id"], ["customers.org_id", "customers.id"], name="fk_payment_customer"
    )
)
events.append_constraint(
    sa.UniqueConstraint("org_id", "payment_id", "id", name="uq_event_payment_id")
)
preferences = sa.Table(
    "customer_preference_events",
    metadata,
    identity(),
    organization(),
    sa.Column("customer_id", sa.Uuid, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("retry_consent", sa.Boolean, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(["org_id", "customer_id"], ["customers.org_id", "customers.id"]),
    sa.UniqueConstraint("org_id", "customer_id", "revision"),
)
decisions = sa.Table(
    "recovery_decisions",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("evidence_event_id", sa.Uuid),
    sa.Column("context_hash", sa.String(64), nullable=False),
    sa.Column("policy_version", sa.String(80), nullable=False),
    sa.Column("policy_snapshot", JSONB, nullable=False),
    sa.Column("proposal", JSONB, nullable=False),
    sa.Column("eligibility", JSONB, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(["org_id", "payment_id"], ["payments.org_id", "payments.id"]),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "evidence_event_id"],
        ["payment_events.org_id", "payment_events.payment_id", "payment_events.id"],
    ),
    sa.UniqueConstraint("org_id", "payment_id", "context_hash"),
    sa.UniqueConstraint("org_id", "payment_id", "id"),
)
actions = sa.Table(
    "recovery_actions",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("decision_id", sa.Uuid, nullable=False),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("approved_at", sa.DateTime(timezone=True)),
    sa.Column("approved_by", sa.String(40)),
    sa.Column("customer_revision", sa.Integer, nullable=False),
    sa.Column("attempt_number", sa.Integer),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("next_reconcile_at", sa.DateTime(timezone=True)),
    sa.Column("reconcile_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("requires_review", sa.Boolean, nullable=False, server_default=sa.false()),
    timestamp(),
    sa.CheckConstraint(
        "status IN ('awaiting_approval','scheduled','executing','unknown',"
        "'succeeded','failed','cancelled','superseded')",
        name="recovery_action_status",
    ),
    sa.CheckConstraint(
        "attempt_number IS NULL OR attempt_number > 0", name="reserved_attempt_positive"
    ),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "decision_id"],
        ["recovery_decisions.org_id", "recovery_decisions.payment_id", "recovery_decisions.id"],
    ),
    sa.UniqueConstraint("org_id", "payment_id", "id"),
    sa.UniqueConstraint("org_id", "decision_id"),
)
ACTIVE_STATES = ("awaiting_approval", "scheduled", "executing", "unknown")
sa.Index(
    "uq_active_recovery_payment",
    actions.c.org_id,
    actions.c.payment_id,
    unique=True,
    postgresql_where=actions.c.status.in_(ACTIVE_STATES),
)
sa.Index("ix_actions_due", actions.c.due_at, postgresql_where=actions.c.status == "scheduled")
operations = sa.Table(
    "simulator_operations",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("receipt", JSONB, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "id"],
        ["recovery_actions.org_id", "recovery_actions.payment_id", "recovery_actions.id"],
    ),
    sa.UniqueConstraint("org_id", "id"),
)
outcomes = sa.Table(
    "recovery_outcomes",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("action_id", sa.Uuid, nullable=False),
    sa.Column("result", sa.String(16), nullable=False),
    sa.Column("gross_recovered_minor", sa.BigInteger, nullable=False),
    sa.Column("cost_minor", sa.BigInteger, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "action_id"],
        ["recovery_actions.org_id", "recovery_actions.payment_id", "recovery_actions.id"],
    ),
    sa.ForeignKeyConstraint(
        ["org_id", "action_id"], ["simulator_operations.org_id", "simulator_operations.id"]
    ),
    sa.UniqueConstraint("org_id", "action_id"),
    sa.CheckConstraint("result IN ('failed','succeeded')", name="recovery_result"),
    sa.CheckConstraint(
        "gross_recovered_minor >= 0 AND cost_minor >= 0", name="nonnegative_outcome_money"
    ),
)
outbox.append_column(sa.Column("processed_at", sa.DateTime(timezone=True)))
sa.Index(
    "ix_outbox_unprocessed", outbox.c.created_at, postgresql_where=outbox.c.processed_at.is_(None)
)


feature_snapshots = sa.Table(
    "observation_features",
    metadata,
    identity(),
    organization(),
    sa.Column("payment_id", sa.Uuid, nullable=False),
    sa.Column("event_id", sa.Uuid, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("schema_version", sa.String(40), nullable=False),
    sa.Column("dataset_id", sa.String(16), nullable=False),
    sa.Column("source_manifest_sha256", sa.String(64), nullable=False),
    sa.Column("generator_version", sa.String(20), nullable=False),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("values", JSONB, nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["org_id", "payment_id", "event_id"],
        ["payment_events.org_id", "payment_events.payment_id", "payment_events.id"],
    ),
    sa.UniqueConstraint("org_id", "event_id"),
)
sa.Index(
    "ix_features_dataset_time",
    feature_snapshots.c.org_id,
    feature_snapshots.c.dataset_id,
    feature_snapshots.c.observed_at,
)
