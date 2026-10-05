"""Store the playlist selections so a hard kill cannot leave jobs stuck.

Revision ID: 0002_jobs
Revises: 0001_users
Create Date: 2026-10-05

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_jobs"
down_revision: str = "0001_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("quality", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_chat_id_status", "jobs", ["chat_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_chat_id_status", table_name="jobs")
    op.drop_table("jobs")
