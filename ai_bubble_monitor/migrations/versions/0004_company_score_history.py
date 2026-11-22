"""Version company scores and retain weighted, source-linked components."""

from alembic import op
import sqlalchemy as sa

revision = "0004_company_score_history"
down_revision = "0003_sec_financials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("company_scores") as batch:
        batch.add_column(sa.Column("coverage", sa.Float(), nullable=False, server_default="0.5"))
        batch.add_column(sa.Column("methodology_version", sa.String(40), nullable=False, server_default="0.1.0"))
    with op.batch_alter_table("company_score_components") as batch:
        batch.add_column(sa.Column("weight", sa.Float(), nullable=False, server_default="0.2"))
        batch.add_column(sa.Column("source_metrics", sa.JSON(), nullable=False, server_default="[]"))
    op.create_index("ix_company_scores_company_time", "company_scores", ["company_id", "calculated_at"])


def downgrade() -> None:
    op.drop_index("ix_company_scores_company_time", table_name="company_scores")
    with op.batch_alter_table("company_score_components") as batch:
        batch.drop_column("source_metrics")
        batch.drop_column("weight")
    with op.batch_alter_table("company_scores") as batch:
        batch.drop_column("methodology_version")
        batch.drop_column("coverage")
