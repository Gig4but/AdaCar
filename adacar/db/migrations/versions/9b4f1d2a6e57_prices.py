"""Automation and alerts (M6): price history, the price-drop mark

Revision ID: 9b4f1d2a6e57
Revises: 7a2d4e6c8b31
Create Date: 2026-11-19 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "9b4f1d2a6e57"
down_revision = "7a2d4e6c8b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cars", sa.Column("price_was", sa.Integer(), nullable=True))
    op.add_column("cars", sa.Column("price_dropped_at", sa.DateTime(), nullable=True))
    op.create_index("ix_cars_price_dropped_at", "cars", ["price_dropped_at"])
    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("old_price", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_history_car_id", "price_history", ["car_id"])


def downgrade() -> None:
    op.drop_index("ix_price_history_car_id", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_cars_price_dropped_at", table_name="cars")
    op.drop_column("cars", "price_dropped_at")
    op.drop_column("cars", "price_was")
