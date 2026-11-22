"""Allow explicit unavailable scenario scores and auditable components."""

from alembic import op
import sqlalchemy as sa

revision = "0002_scenario_scoring"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scenario_scores") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column("status", sa.String(30), nullable=False, server_default="available"))

    with op.batch_alter_table("scenario_components") as batch:
        batch.alter_column("indicator_id", existing_type=sa.String(100), nullable=True)
        batch.add_column(sa.Column("component_id", sa.String(100), nullable=True))

    op.create_index("ix_scenario_scores_scenario_id", "scenario_scores", ["scenario_id", "calculated_at"])
    op.create_index("ix_scenario_components_score_id", "scenario_components", ["scenario_score_id"])


def downgrade() -> None:
    op.drop_index("ix_scenario_components_score_id", table_name="scenario_components")
    op.drop_index("ix_scenario_scores_scenario_id", table_name="scenario_scores")
    with op.batch_alter_table("scenario_components") as batch:
        batch.drop_column("component_id")
        batch.alter_column("indicator_id", existing_type=sa.String(100), nullable=False)
    with op.batch_alter_table("scenario_scores") as batch:
        batch.drop_column("status")
        batch.alter_column("score", existing_type=sa.Float(), nullable=False)
