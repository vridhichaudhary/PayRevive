"""Immutable feature observations with scoped event and dataset provenance."""

from alembic import op

revision = "0004_features"
down_revision = "0003_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE observation_features (
    id UUID NOT NULL,
    org_id UUID NOT NULL,
    payment_id UUID NOT NULL,
    event_id UUID NOT NULL,
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    schema_version VARCHAR(40) NOT NULL,
    dataset_id VARCHAR(16) NOT NULL,
    source_manifest_sha256 VARCHAR(64) NOT NULL,
    generator_version VARCHAR(20) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    values JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(org_id, payment_id, event_id) REFERENCES payment_events (org_id, payment_id,
    id),
    UNIQUE (org_id, event_id),
    FOREIGN KEY(org_id) REFERENCES organizations (id)
    )
    """)
    op.execute("""
    CREATE INDEX ix_features_dataset_time ON observation_features (org_id, dataset_id,
    observed_at)
    """)
    op.execute("""
    CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON observation_features FOR EACH ROW
    EXECUTE FUNCTION reject_history_change()
    """)
    op.execute("""
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON observation_features FOR EACH
    STATEMENT EXECUTE FUNCTION reject_history_change()
    """)


def downgrade() -> None:
    op.drop_table("observation_features")
