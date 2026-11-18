"""Trip to the seller (M5): cars marked for a viewing; travel times computed again

Revision ID: 7a2d4e6c8b31
Revises: 5c1e8a3f9b20
Create Date: 2026-11-18 23:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "7a2d4e6c8b31"
down_revision = "5c1e8a3f9b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cars", sa.Column("visit_at", sa.DateTime(), nullable=True))
    op.create_index("ix_cars_visit_at", "cars", ["visit_at"])
    # Times are a cache kept until the map data changes: those stored with the 2-hour limit
    # would never reach farther. Dropped, they are estimated and computed again when used.
    op.execute("DELETE FROM travel_times")
    op.execute("DELETE FROM travel_sets")


def downgrade() -> None:
    op.drop_index("ix_cars_visit_at", table_name="cars")
    op.drop_column("cars", "visit_at")
