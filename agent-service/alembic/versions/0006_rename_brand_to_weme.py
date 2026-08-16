"""Rename persisted demo conversation branding to WeMe.

Revision ID: 0006_rename_brand_to_weme
Revises: 0005_agent_message_history
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_rename_brand_to_weme"
down_revision: str | None = "0005_agent_message_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    previous_brand = "Meet" + "Ops"
    previous_storage_prefix = "meet" + "ops"
    for table_name, column_name in (
        ("agent_message", "content_text"),
        ("agent_thread", "title"),
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET {column_name} = REPLACE(
                    REPLACE({column_name}, :previous_brand, 'WeMe'),
                    :previous_storage_prefix,
                    'weme'
                )
                WHERE LOWER({column_name}) LIKE :brand_pattern
                """
            ).bindparams(
                previous_brand=previous_brand,
                previous_storage_prefix=previous_storage_prefix,
                brand_pattern=f"%{previous_storage_prefix}%",
            )
        )


def downgrade() -> None:
    previous_brand = "Meet" + "Ops"
    previous_storage_prefix = "meet" + "ops"
    for table_name, column_name in (
        ("agent_message", "content_text"),
        ("agent_thread", "title"),
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET {column_name} = REPLACE(
                    REPLACE({column_name}, 'WeMe', :previous_brand),
                    'weme',
                    :previous_storage_prefix
                )
                WHERE LOWER({column_name}) LIKE '%weme%'
                """
            ).bindparams(
                previous_brand=previous_brand,
                previous_storage_prefix=previous_storage_prefix,
            )
        )
