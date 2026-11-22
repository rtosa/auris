"""Retain scenario input references for non-indicator sources."""

from alembic import op
import sqlalchemy as sa

revision = "0005_company_scenario_inputs"
down_revision = "0004_company_score_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scenario_components") as batch:
        batch.add_column(sa.Column("input_ref", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scenario_components") as batch:
        batch.drop_column("input_ref")
