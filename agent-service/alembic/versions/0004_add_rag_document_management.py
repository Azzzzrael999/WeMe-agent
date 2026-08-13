"""Add managed RAG source content, metadata and tombstone fields.

Revision ID: 0004_rag_document_management
Revises: 0003_rag_checksum_unique
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0004_rag_document_management"
down_revision: str | None = "0003_rag_checksum_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    timestamp = mysql.DATETIME(fsp=3)
    op.add_column(
        "rag_document",
        sa.Column("department", sa.String(length=64), server_default="ALL", nullable=False),
    )
    op.add_column(
        "rag_document",
        sa.Column(
            "effective_date", sa.Date(), server_default=sa.text("'2026-08-01'"), nullable=False
        ),
    )
    op.add_column(
        "rag_document",
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
    )
    op.add_column(
        "rag_document",
        sa.Column("file_name", sa.String(length=255), server_default="document.md", nullable=False),
    )
    op.add_column(
        "rag_document",
        sa.Column("media_type", sa.String(length=64), server_default="text/markdown", nullable=False),
    )
    op.add_column("rag_document", sa.Column("content_text", mysql.MEDIUMTEXT(), nullable=True))
    op.add_column(
        "rag_document",
        sa.Column("record_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "rag_document",
        sa.Column(
            "updated_at",
            timestamp,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
    )
    op.add_column("rag_document", sa.Column("deleted_at", timestamp, nullable=True))
    op.execute(sa.text("UPDATE rag_document SET content_text = '' WHERE content_text IS NULL"))
    op.alter_column(
        "rag_document", "content_text", existing_type=mysql.MEDIUMTEXT(), nullable=False
    )


def downgrade() -> None:
    op.drop_column("rag_document", "deleted_at")
    op.drop_column("rag_document", "updated_at")
    op.drop_column("rag_document", "record_version")
    op.drop_column("rag_document", "content_text")
    op.drop_column("rag_document", "media_type")
    op.drop_column("rag_document", "file_name")
    op.drop_column("rag_document", "priority")
    op.drop_column("rag_document", "effective_date")
    op.drop_column("rag_document", "department")
