"""add integrity constraints and backfills

Revision ID: 20260117_0002_constraints
Revises: 20251005_0001_init
Create Date: 2026-01-17
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "20260117_0002_constraints"
down_revision = "20251005_0001_init"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        insert into symbols (ticker)
        select distinct ticker from cost_requests
        on conflict (ticker) do nothing
        """
    )
    op.execute("delete from daily_liquidity where adv_usd is null or adv_usd <= 0")
    op.execute("delete from cost_results where total_cost_usd is null or total_cost_bps is null")
    op.execute("delete from cost_requests where notional_usd is null or notional_usd <= 0")

    op.alter_column("daily_liquidity", "adv_usd", existing_type=sa.Numeric(), nullable=False)
    op.create_check_constraint("ck_daily_liquidity_adv_usd", "daily_liquidity", "adv_usd > 0")

    op.create_foreign_key(
        "fk_cost_requests_ticker",
        "cost_requests",
        "symbols",
        ["ticker"],
        ["ticker"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("ck_cost_requests_notional_usd", "cost_requests", "notional_usd > 0")

    op.alter_column("cost_results", "total_cost_usd", existing_type=sa.Numeric(), nullable=False)
    op.alter_column("cost_results", "total_cost_bps", existing_type=sa.Numeric(), nullable=False)


def downgrade():
    op.alter_column("cost_results", "total_cost_bps", existing_type=sa.Numeric(), nullable=True)
    op.alter_column("cost_results", "total_cost_usd", existing_type=sa.Numeric(), nullable=True)

    op.drop_constraint("ck_cost_requests_notional_usd", "cost_requests", type_="check")
    op.drop_constraint("fk_cost_requests_ticker", "cost_requests", type_="foreignkey")

    op.drop_constraint("ck_daily_liquidity_adv_usd", "daily_liquidity", type_="check")
    op.alter_column("daily_liquidity", "adv_usd", existing_type=sa.Numeric(), nullable=True)
