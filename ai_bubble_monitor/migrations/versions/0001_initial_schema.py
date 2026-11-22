"""Create the initial local history schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "indicators",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("unit", sa.String(40), nullable=True),
        sa.Column("frequency", sa.String(30), nullable=True),
        sa.Column("direction", sa.String(40), nullable=True),
        sa.Column("normalization_method", sa.String(80), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "raw_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(80), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("indicator_id", sa.String(100), sa.ForeignKey("indicators.id"), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.UniqueConstraint("source_id", "indicator_id", "observation_date", name="uq_raw_observation"),
    )
    op.create_table(
        "indicator_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("indicator_id", sa.String(100), sa.ForeignKey("indicators.id"), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("normalized_value", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("data_quality", sa.String(40), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("indicator_id", "observation_date", name="uq_indicator_observation"),
    )
    op.create_table(
        "scoring_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(40), nullable=False, unique=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
    )
    op.create_table(
        "scenario_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scenario_id", sa.String(80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("methodology_version", sa.String(40), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scenario_components",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scenario_score_id", sa.Integer(), sa.ForeignKey("scenario_scores.id"), nullable=False),
        sa.Column("indicator_id", sa.String(100), sa.ForeignKey("indicators.id"), nullable=False),
        sa.Column("normalized_value", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("contribution", sa.Float(), nullable=True),
    )
    op.create_table(
        "company_financials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(80), nullable=False),
        sa.Column("metric", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(40), nullable=True),
        sa.Column("reporting_period", sa.String(40), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
    )
    op.create_table(
        "company_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("company_id", sa.String(80), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(40), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.create_index("ix_raw_observations_observation_date", "raw_observations", ["observation_date"])
    op.create_index("ix_indicator_values_observation_date", "indicator_values", ["observation_date"])
    op.create_index("ix_scenario_scores_calculated_at", "scenario_scores", ["calculated_at"])
    op.create_index("ix_company_scores_calculated_at", "company_scores", ["calculated_at"])


def downgrade() -> None:
    op.drop_index("ix_company_scores_calculated_at", table_name="company_scores")
    op.drop_index("ix_scenario_scores_calculated_at", table_name="scenario_scores")
    op.drop_index("ix_indicator_values_observation_date", table_name="indicator_values")
    op.drop_index("ix_raw_observations_observation_date", table_name="raw_observations")
    for table in (
        "events", "company_scores", "company_financials", "scenario_components",
        "scenario_scores", "scoring_versions", "indicator_values", "raw_observations",
        "indicators", "data_sources",
    ):
        op.drop_table(table)
