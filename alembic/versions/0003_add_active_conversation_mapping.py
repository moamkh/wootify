"""add active flag to conversation mappings

Revision ID: 0003_add_active_conversation_mapping
Revises: 0002_add_instance_proxy_config
Create Date: 2026-03-14 13:45:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0003_add_active_conversation_mapping'
down_revision = '0002_add_instance_proxy_config'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))

    op.execute(sa.text('UPDATE conversations SET is_active = 1'))

    with op.batch_alter_table('conversations') as batch_op:
        batch_op.alter_column('is_active', server_default=None)
        batch_op.drop_constraint('uq_instance_platform_conversation', type_='unique')
        batch_op.create_index('ix_conversations_is_active', ['is_active'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    duplicate_rows = bind.execute(
        sa.text(
            """
            SELECT instance_id, platform_conversation_id, COUNT(*) AS row_count
            FROM conversations
            GROUP BY instance_id, platform_conversation_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicate_rows:
        raise RuntimeError(
            'Cannot downgrade 0003_add_active_conversation_mapping while duplicate '
            'platform conversation mappings exist'
        )

    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_index('ix_conversations_is_active')
        batch_op.create_unique_constraint(
            'uq_instance_platform_conversation',
            ['instance_id', 'platform_conversation_id'],
        )
        batch_op.drop_column('is_active')
