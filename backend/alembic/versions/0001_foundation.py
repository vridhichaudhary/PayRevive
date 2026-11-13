"""Establish the simulation-only application metadata marker."""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata = op.create_table(
        "application_metadata",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
    )
    op.bulk_insert(metadata, [{"key": "execution_mode", "value": "simulation"}])


def downgrade() -> None:
    op.drop_table("application_metadata")
