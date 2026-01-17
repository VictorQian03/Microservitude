"""init schema

Revision ID: 20251005_0001_init
Revises: None
Create Date: 2025-10-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "20251005_0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False, unique=True),
    )

    op.create_table(
        "impact_models",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", "version"),
    )

    op.create_table(
        "daily_liquidity",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("d", sa.Date(), nullable=False),
        sa.Column("adv_usd", sa.Numeric(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ticker"], ["symbols.ticker"], ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ticker", "d"),
    )

    op.create_table(
        "cost_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("d", sa.Date(), nullable=False),
        sa.Column("notional_usd", sa.Numeric(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("side in ('buy','sell')", name="ck_cost_requests_side"),
        sa.CheckConstraint("status in ('queued','done','error')", name="ck_cost_requests_status"),
    )

    op.create_table(
        "cost_results",
        sa.Column("request_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("adv_usd", sa.Numeric(), nullable=True),
        sa.Column("models", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("best_model", sa.Text(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(), nullable=True),
        sa.Column("total_cost_bps", sa.Numeric(), nullable=True),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["request_id"], ["cost_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("request_id"),
    )


def downgrade():
    op.drop_table("cost_results")
    op.drop_table("cost_requests")
    op.drop_table("daily_liquidity")
    op.drop_table("impact_models")
    op.drop_table("symbols")
