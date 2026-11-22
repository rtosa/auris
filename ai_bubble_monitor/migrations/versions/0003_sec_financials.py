"""Retain SEC filing context alongside company financial facts."""

from alembic import op
import sqlalchemy as sa

revision = "0003_sec_financials"
down_revision = "0002_scenario_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("company_financials") as batch:
        batch.add_column(sa.Column("accession_number", sa.String(32), nullable=True))
        batch.add_column(sa.Column("filing_form", sa.String(12), nullable=True))
        batch.add_column(sa.Column("fiscal_year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fiscal_period", sa.String(8), nullable=True))
        batch.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("end_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("fact_tag", sa.String(120), nullable=True))
    with op.batch_alter_table("company_scores") as batch:
        batch.add_column(sa.Column("reporting_period", sa.String(40), nullable=True))
        batch.add_column(sa.Column("end_date", sa.Date(), nullable=True))
    op.create_table(
        "company_score_components",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_score_id", sa.Integer(), sa.ForeignKey("company_scores.id"), nullable=False),
        sa.Column("component_id", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("contribution", sa.Float(), nullable=False),
    )
    op.create_index("ix_company_score_components_score", "company_score_components", ["company_score_id"])
    op.create_index("ix_company_financials_company_period", "company_financials", ["company_id", "reporting_period", "metric"])
    op.create_index("ix_company_financials_filed", "company_financials", ["filing_date"])
    op.create_index("uq_company_financials_filing_metric", "company_financials",
                    ["company_id", "accession_number", "metric", "reporting_period"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_company_score_components_score", table_name="company_score_components")
    op.drop_table("company_score_components")
    with op.batch_alter_table("company_scores") as batch:
        batch.drop_column("end_date")
        batch.drop_column("reporting_period")
    op.drop_index("ix_company_financials_filed", table_name="company_financials")
    op.drop_index("uq_company_financials_filing_metric", table_name="company_financials")
    op.drop_index("ix_company_financials_company_period", table_name="company_financials")
    with op.batch_alter_table("company_financials") as batch:
        batch.drop_column("fact_tag")
        batch.drop_column("end_date")
        batch.drop_column("start_date")
        batch.drop_column("fiscal_period")
        batch.drop_column("fiscal_year")
        batch.drop_column("filing_form")
        batch.drop_column("accession_number")
