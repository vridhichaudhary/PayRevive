"""Organization-owned payment domain, immutable history, and durable outbox.

DDL is frozen here: future changes to domain metadata cannot rewrite this migration.
"""

from alembic import op

revision = "0002_domain"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE organizations (
        id UUID NOT NULL,
        name VARCHAR(100) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id)
    )
    """)
    op.execute("""
    CREATE TABLE payments (
        id UUID NOT NULL,
        org_id UUID NOT NULL,
        reference VARCHAR(100) NOT NULL,
        amount_minor BIGINT NOT NULL,
        currency VARCHAR(3) NOT NULL,
        status VARCHAR(16) DEFAULT 'pending' NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT positive_payment_amount CHECK (amount_minor > 0),
        CONSTRAINT inr_only CHECK (currency = 'INR'),
        CONSTRAINT payment_status CHECK (status IN ('pending','failed','succeeded')),
        UNIQUE (org_id, reference),
        UNIQUE (org_id, id),
        FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE audit_events (
        id UUID NOT NULL,
        org_id UUID NOT NULL,
        payment_id UUID NOT NULL,
        kind VARCHAR(60) NOT NULL,
        actor VARCHAR(40) NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(org_id, payment_id) REFERENCES payments (org_id, id),
        FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE payment_attempts (
        id UUID NOT NULL,
        org_id UUID NOT NULL,
        payment_id UUID NOT NULL,
        number INTEGER NOT NULL,
        status VARCHAR(16) NOT NULL,
        state_at TIMESTAMP WITH TIME ZONE NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT positive_attempt_number CHECK (number > 0),
        CONSTRAINT attempt_status CHECK (status IN ('pending','failed','succeeded')),
        FOREIGN KEY(org_id, payment_id) REFERENCES payments (org_id, id),
        UNIQUE (org_id, payment_id, number),
        UNIQUE (org_id, payment_id, id),
        FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE payment_events (
        id UUID NOT NULL,
        org_id UUID NOT NULL,
        payment_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        provider VARCHAR(20) NOT NULL,
        account VARCHAR(100) NOT NULL,
        source_event_id VARCHAR(100) NOT NULL,
        fingerprint VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        reason VARCHAR(40),
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT simulator_only CHECK (provider = 'simulator'),
        CONSTRAINT event_status CHECK (status IN ('pending','failed','succeeded')),
            FOREIGN KEY(org_id, payment_id, attempt_id) REFERENCES payment_attempts (org_id,
    payment_id, id),
        UNIQUE (org_id, provider, account, source_event_id),
        UNIQUE (org_id, id),
        FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE TABLE outbox_messages (
        id UUID NOT NULL,
        org_id UUID NOT NULL,
        event_id UUID NOT NULL,
        topic VARCHAR(60) NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        published_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(org_id, event_id) REFERENCES payment_events (org_id, id),
        UNIQUE (org_id, event_id, topic),
        FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_audit_payment_time ON audit_events (org_id, payment_id, created_at)
    """)
    op.execute("""
    CREATE INDEX ix_events_payment_time ON payment_events (org_id, payment_id, occurred_at)
    """)
    op.execute("""
    CREATE INDEX ix_outbox_pending ON outbox_messages (created_at) WHERE published_at IS NULL
    """)
    op.execute("""
    INSERT INTO organizations (id,name)
    VALUES ('00000000-0000-0000-0000-000000000001','PayRevive Demo')
    """)
    op.execute("""
    CREATE FUNCTION reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'History is append-only'; END; $$
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON payment_events FOR EACH ROW
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON payment_events FOR EACH STATEMENT
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE
    FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT
    EXECUTE FUNCTION reject_history_change()
    """)


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("payment_events")
    op.drop_table("payment_attempts")
    op.drop_table("audit_events")
    op.drop_table("payments")
    op.drop_table("organizations")
    op.execute("""
    DROP FUNCTION reject_history_change()
    """)
