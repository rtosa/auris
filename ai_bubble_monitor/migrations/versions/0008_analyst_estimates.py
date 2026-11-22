"""Store point-in-time analyst consensus observations."""

from alembic import op
import sqlalchemy as sa

revision = "0008_analyst_estimates"
down_revision = "0007_sec_guidance_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyst_estimates",
        sa.Column("company_id", sa.String(80), primary_key=True),
        sa.Column("period_id", sa.String(100), primary_key=True),
        sa.Column("metric", sa.String(80), primary_key=True),
        sa.Column("as_of", sa.Date(), primary_key=True),
        sa.Column("period_type", sa.String(40), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("num_estimates", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
    )
    op.create_index("ix_analyst_estimates_company_period", "analyst_estimates", ["company_id", "report_date"])


def downgrade() -> None:
    op.drop_index("ix_analyst_estimates_company_period", table_name="analyst_estimates")
    op.drop_table("analyst_estimates")
