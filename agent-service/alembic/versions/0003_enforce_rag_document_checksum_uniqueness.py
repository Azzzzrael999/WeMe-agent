"""Enforce checksum deduplication for RAG documents.

Revision ID: 0003_rag_checksum_unique
Revises: 0002_agent_observability
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_rag_checksum_unique"
down_revision: str | None = "0002_agent_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uk_rag_document_checksum", "rag_document", ["checksum"])


def downgrade() -> None:
    op.drop_constraint("uk_rag_document_checksum", "rag_document", type_="unique")
