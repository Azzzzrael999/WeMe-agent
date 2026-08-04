"""Persist model identity, token usage and safe loop events.

Revision ID: 0002_agent_observability
Revises: 0001_agent_metadata
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0002_agent_observability"
down_revision: str | None = "0001_agent_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_run", sa.Column("model_provider", sa.String(32), nullable=True))
    op.add_column("agent_run", sa.Column("configured_model", sa.String(128), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column(
            "response_models",
            mysql.JSON(),
            nullable=False,
            server_default=sa.text("(JSON_ARRAY())"),
        ),
    )
    op.add_column(
        "agent_run",
        sa.Column(
            "prompt_version",
            sa.String(64),
            nullable=False,
            server_default="meeting-agent-prompts-v3",
        ),
    )
    op.add_column(
        "agent_run",
        sa.Column(
            "schema_version",
            sa.String(64),
            nullable=False,
            server_default="meeting-agent-state-v3",
        ),
    )
    op.add_column(
        "agent_run",
        sa.Column("cache_hit_tokens", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_run",
        sa.Column("cache_miss_tokens", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "agent_loop_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(128), nullable=False),
        sa.Column("feedback_codes", mysql.JSON(), nullable=False),
        sa.Column("replan_count", sa.Integer(), nullable=False),
        sa.Column("remaining_model_calls", sa.Integer(), nullable=False),
        sa.Column("remaining_tool_calls", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uk_agent_loop_run_sequence"),
    )


def downgrade() -> None:
    op.drop_table("agent_loop_event")
    op.drop_column("agent_run", "cache_miss_tokens")
    op.drop_column("agent_run", "cache_hit_tokens")
    op.drop_column("agent_run", "schema_version")
    op.drop_column("agent_run", "prompt_version")
    op.drop_column("agent_run", "response_models")
    op.drop_column("agent_run", "configured_model")
    op.drop_column("agent_run", "model_provider")
