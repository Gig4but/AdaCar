"""The price a listing card showed: a card's price change re-reads the ad's detail page

Revision ID: e3c8a1f4d602
Revises: 9b4f1d2a6e57
Create Date: 2026-11-24 21:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e3c8a1f4d602"
down_revision = "9b4f1d2a6e57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("car_ads", sa.Column("card_price", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("car_ads", "card_price")
