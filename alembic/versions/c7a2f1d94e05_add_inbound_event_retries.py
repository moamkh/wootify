
"""add_inbound_event_retries

Revision ID: c7a2f1d94e05
Revises: 5b8f2dfaae9f
Create Date: 2026-08-18 14:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7a2f1d94e05'
down_revision = '5b8f2dfaae9f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'inbound_event_retries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('instance_key', sa.String(length=128), nullable=False),
        sa.Column('platform_key', sa.String(length=64), nullable=False),
        sa.Column('update_id', sa.String(length=64), nullable=True),
        sa.Column('payload_json', sa.JSON(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instance_key', 'platform_key', 'update_id', name='uq_inbound_event_retry'),
    )
    op.create_index('ix_inbound_event_retries_instance_key', 'inbound_event_retries', ['instance_key'])
    op.create_index('ix_inbound_event_retries_next_attempt_at', 'inbound_event_retries', ['next_attempt_at'])


def downgrade() -> None:
    op.drop_index('ix_inbound_event_retries_next_attempt_at', table_name='inbound_event_retries')
    op.drop_index('ix_inbound_event_retries_instance_key', table_name='inbound_event_retries')
    op.drop_table('inbound_event_retries')
