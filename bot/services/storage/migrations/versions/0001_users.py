"""Record who has used the bot and how they want files delivered.

Revision ID: 0001_users
Revises:
Create Date: 2026-10-04

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001_users"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("send_as_video", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
