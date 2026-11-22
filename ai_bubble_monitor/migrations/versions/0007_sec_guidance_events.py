"""Persist sourced, numeric SEC capex guidance events and scan markers."""

from alembic import op
import sqlalchemy as sa


revision = "0007_sec_guidance_events"
down_revision = "0006_customer_concentration_percent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("guidance_period", sa.String(40), nullable=True))
        batch.add_column(sa.Column("guidance_lower", sa.Float(), nullable=True))
        batch.add_column(sa.Column("guidance_upper", sa.Float(), nullable=True))
        batch.add_column(sa.Column("guidance_unit", sa.String(20), nullable=True))
        batch.add_column(sa.Column("source_quote", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_accession", sa.String(32), nullable=True))
        batch.add_column(sa.Column("event_key", sa.String(64), nullable=True))
    op.create_index("uq_events_event_key", "events", ["event_key"], unique=True)
    op.create_table(
        "sec_filing_scans",
        sa.Column("company_id", sa.String(80), primary_key=True),
        sa.Column("accession_number", sa.String(32), primary_key=True),
        sa.Column("scanner", sa.String(40), primary_key=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sec_filing_scans")
    op.drop_index("uq_events_event_key", table_name="events")
    with op.batch_alter_table("events") as batch:
        batch.drop_column("event_key")
        batch.drop_column("source_accession")
        batch.drop_column("source_quote")
        batch.drop_column("guidance_unit")
        batch.drop_column("guidance_upper")
        batch.drop_column("guidance_lower")
        batch.drop_column("guidance_period")
