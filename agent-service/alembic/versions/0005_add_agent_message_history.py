"""Persist user-visible Agent conversation messages.

Revision ID: 0005_agent_message_history
Revises: 0004_rag_document_management
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0005_agent_message_history"
down_revision: str | None = "0004_rag_document_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_message",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("visible_payload", mysql.JSON(), nullable=False),
        sa.Column("client_request_id", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint(
            "thread_id", "sequence_no", name="uk_agent_message_thread_sequence"
        ),
        sa.UniqueConstraint(
            "user_id",
            "client_request_id",
            "role",
            name="uk_agent_message_user_request_role",
        ),
    )
    op.create_index(
        "idx_agent_message_user_thread_created",
        "agent_message",
        ["user_id", "thread_id", "created_at"],
    )
    op.create_index(
        "idx_agent_run_user_thread_created",
        "agent_run",
        ["user_id", "thread_id", "created_at"],
    )
    op.create_index(
        "idx_agent_run_user_status_created",
        "agent_run",
        ["user_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_run_user_status_created", table_name="agent_run")
    op.drop_index("idx_agent_run_user_thread_created", table_name="agent_run")
    op.drop_index("idx_agent_message_user_thread_created", table_name="agent_message")
    op.drop_table("agent_message")
