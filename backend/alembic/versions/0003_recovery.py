"""Explicit consent, durable decisions/actions, and simulator operation receipts.
Frozen PostgreSQL DDL: this migration never imports mutable application metadata.
"""

from alembic import op

revision = "0003_recovery"
down_revision = "0002_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE payment_events ADD CONSTRAINT uq_event_payment_id UNIQUE
    (org_id,payment_id,id)
    """)
    op.execute("""
    CREATE TABLE customers (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    reference VARCHAR(100) NOT NULL,
    retry_consent BOOLEAN DEFAULT false NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (org_id, reference),
    UNIQUE (org_id, id),
    CONSTRAINT positive_customer_revision CHECK (revision > 0),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    ALTER TABLE payments ADD COLUMN customer_id UUID, ADD CONSTRAINT fk_payment_customer
    FOREIGN KEY (org_id, customer_id) REFERENCES customers (org_id, id)
    """)
    op.execute("""
    CREATE TABLE customer_preference_events (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    retry_consent BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(org_id, customer_id) REFERENCES customers (org_id, id),
    UNIQUE (org_id, customer_id, revision),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE recovery_decisions (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    payment_id UUID NOT NULL,
    evidence_event_id UUID,
    context_hash VARCHAR(64) NOT NULL,
    policy_version VARCHAR(80) NOT NULL,
    policy_snapshot JSONB NOT NULL,
    proposal JSONB NOT NULL,
    eligibility JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(org_id, payment_id) REFERENCES payments (org_id, id),
    FOREIGN KEY(org_id, payment_id, evidence_event_id) REFERENCES payment_events (org_id,
    payment_id, id),
    UNIQUE (org_id, payment_id, context_hash),
    UNIQUE (org_id, payment_id, id),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE recovery_actions (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    payment_id UUID NOT NULL,
    decision_id UUID NOT NULL,
    status VARCHAR(24) NOT NULL,
    due_at TIMESTAMP WITH TIME ZONE NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    approved_by VARCHAR(40),
    customer_revision INTEGER NOT NULL,
    attempt_number INTEGER,
    started_at TIMESTAMP WITH TIME ZONE,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    next_reconcile_at TIMESTAMP WITH TIME ZONE,
    reconcile_count INTEGER DEFAULT '0' NOT NULL,
    requires_review BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT recovery_action_status CHECK (status IN
    ('awaiting_approval','scheduled','executing','unknown','succeeded','failed','cancelled','superseded')),
    CONSTRAINT reserved_attempt_positive CHECK (attempt_number IS NULL OR attempt_number > 0),
    FOREIGN KEY(org_id, payment_id, decision_id) REFERENCES recovery_decisions (org_id,
    payment_id, id),
    UNIQUE (org_id, payment_id, id),
    UNIQUE (org_id, decision_id),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE simulator_operations (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    payment_id UUID NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    receipt JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(org_id, payment_id, id) REFERENCES recovery_actions (org_id, payment_id, id),
    UNIQUE (org_id, id),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE recovery_outcomes (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    payment_id UUID NOT NULL,
    action_id UUID NOT NULL,
    result VARCHAR(16) NOT NULL,
    gross_recovered_minor BIGINT NOT NULL,
    cost_minor BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(org_id, payment_id, action_id) REFERENCES recovery_actions (org_id,
    payment_id, id),
    FOREIGN KEY(org_id, action_id) REFERENCES simulator_operations (org_id, id),
    UNIQUE (org_id, action_id),
    CONSTRAINT recovery_result CHECK (result IN ('failed','succeeded')),
    CONSTRAINT nonnegative_outcome_money CHECK (gross_recovered_minor >= 0 AND cost_minor >=
    0),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE UNIQUE INDEX uq_active_recovery_payment ON recovery_actions (org_id, payment_id)
    WHERE status IN ('awaiting_approval', 'scheduled', 'executing', 'unknown')
    """)
    op.execute("""
    CREATE INDEX ix_actions_due ON recovery_actions (due_at) WHERE status = 'scheduled'
    """)
    op.execute("""
    ALTER TABLE outbox_messages ADD COLUMN processed_at TIMESTAMPTZ
    """)
    op.execute("""
    CREATE INDEX ix_outbox_unprocessed ON outbox_messages (created_at) WHERE processed_at IS
    NULL
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON customer_preference_events FOR
    EACH ROW EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON customer_preference_events FOR EACH
    STATEMENT EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON recovery_decisions FOR EACH ROW
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON recovery_decisions FOR EACH STATEMENT
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON simulator_operations FOR EACH ROW
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON simulator_operations FOR EACH
    STATEMENT EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON recovery_outcomes FOR EACH ROW
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON recovery_outcomes FOR EACH STATEMENT
    EXECUTE FUNCTION reject_history_change()
    """)


def downgrade() -> None:
    op.drop_index("ix_outbox_unprocessed", table_name="outbox_messages")
    op.drop_column("outbox_messages", "processed_at")
    op.drop_constraint("fk_payment_customer", "payments", type_="foreignkey")
    op.drop_column("payments", "customer_id")
    op.drop_table("recovery_outcomes")
    op.drop_table("simulator_operations")
    op.drop_table("recovery_actions")
    op.drop_table("recovery_decisions")
    op.drop_table("customer_preference_events")
    op.drop_table("customers")
    op.drop_constraint("uq_event_payment_id", "payment_events", type_="unique")
