"""Store XBRL customer concentration values as percentage points."""

from alembic import op
from sqlalchemy import text

revision = "0006_customer_concentration_percent"
down_revision = "0005_company_scenario_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""UPDATE company_financials
        SET value=value * 100
        WHERE metric='customer_revenue_concentration_percent'
          AND unit='percent' AND value > 0 AND value <= 1"""))


def downgrade() -> None:
    op.execute(text("""UPDATE company_financials
        SET value=value / 100
        WHERE metric='customer_revenue_concentration_percent'
          AND unit='percent' AND value > 0 AND value <= 100"""))
