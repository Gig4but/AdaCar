"""AI (M3): model answers cache, warning signs, what the AI read and saw

Revision ID: dd07b4f12e71
Revises: 1ea5951c21fb
Create Date: 2026-11-18 16:22:04.084094
"""

import sqlalchemy as sa
from alembic import op

revision = "dd07b4f12e71"
down_revision = "1ea5951c21fb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.add_column("car_images", sa.Column("kind", sa.String(length=10), nullable=True))
    op.add_column("car_images", sa.Column("text", sa.String(), nullable=True))
    # Existing cars have no warnings yet.
    op.add_column(
        "cars", sa.Column("warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column("cars", sa.Column("ai_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("cars", "ai_hash")
    op.drop_column("cars", "warnings")
    op.drop_column("car_images", "text")
    op.drop_column("car_images", "kind")
    op.drop_table("llm_cache")
