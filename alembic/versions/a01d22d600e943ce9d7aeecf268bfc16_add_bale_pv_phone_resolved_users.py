
"""add bale pv phone resolved users

Revision ID: a01d22d600e9
Revises: 47aec61211d1
Create Date: 2026-06-02 17:05:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a01d22d600e9'
down_revision = '47aec61211d1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bale_pv_phone_resolved_users',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('instance_id', sa.String(36), nullable=False),
        sa.Column('phone_number', sa.String(32), nullable=False),
        sa.Column('bale_user_id', sa.Integer(), nullable=False),
        sa.Column('access_hash', sa.String(128), nullable=True),
        sa.Column('name', sa.String(256), nullable=True),
        sa.Column('nick', sa.String(256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instance_id', 'phone_number', name='uq_bale_pv_resolved_phone'),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
    )
    op.create_index(
        'ix_bale_pv_phone_resolved_users_instance_id',
        'bale_pv_phone_resolved_users',
        ['instance_id'],
        unique=False,
    )
    op.create_index(
        'ix_bale_pv_phone_resolved_users_phone_number',
        'bale_pv_phone_resolved_users',
        ['phone_number'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table('bale_pv_phone_resolved_users')
