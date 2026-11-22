"""Retain reporting currency with analyst consensus values."""

from alembic import op
import sqlalchemy as sa

revision = "0009_analyst_currency"
down_revision = "0008_analyst_estimates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyst_estimates", sa.Column("currency", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("analyst_estimates", "currency")
