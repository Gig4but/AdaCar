"""Vehicle and seller checks (M4): VIN records, dealers, a car's dealer and check warnings

Revision ID: 5c1e8a3f9b20
Revises: dd07b4f12e71
Create Date: 2026-11-18 22:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "5c1e8a3f9b20"
down_revision = "dd07b4f12e71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_checks",
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("km_seen", sa.JSON(), nullable=False),
        sa.Column("register", sa.JSON(), nullable=False),
        sa.Column("register_status", sa.String(length=10), nullable=True),
        sa.Column("register_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("vin"),
    )
    op.create_table(
        "dealers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("ico", sa.String(length=8), nullable=True),
        sa.Column("match", sa.String(length=10), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("legal_name", sa.String(), nullable=True),
        sa.Column("legal_form", sa.String(length=4), nullable=True),
        sa.Column("founded", sa.Date(), nullable=True),
        sa.Column("ended", sa.Date(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("dic", sa.String(length=14), nullable=True),
        sa.Column("insolvency", sa.JSON(), nullable=False),
        sa.Column("vat_unreliable", sa.String(length=10), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("rating", sa.Double(), nullable=True),
        sa.Column("rating_count", sa.Integer(), nullable=True),
        sa.Column("maps_url", sa.String(), nullable=True),
        sa.Column("reviews", sa.JSON(), nullable=False),
        sa.Column("reviews_at", sa.DateTime(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key"),
    )
    op.create_index(op.f("ix_dealers_ico"), "dealers", ["ico"], unique=False)
    # SQLite adds a column with its reference, which Alembic only does in batch mode (that
    # rebuilds the table and drops the FTS triggers).
    op.execute(
        "ALTER TABLE cars ADD COLUMN dealer_id INTEGER REFERENCES dealers (id) ON DELETE SET NULL"
    )
    op.create_index(op.f("ix_cars_dealer_id"), "cars", ["dealer_id"], unique=False)
    op.add_column(
        "cars",
        sa.Column("check_warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("cars", "check_warnings")
    op.drop_index(op.f("ix_cars_dealer_id"), table_name="cars")
    op.drop_column("cars", "dealer_id")
    op.drop_index(op.f("ix_dealers_ico"), table_name="dealers")
    op.drop_table("dealers")
    op.drop_table("vehicle_checks")
