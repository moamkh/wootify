"""add per-instance proxy config

Revision ID: 0002_add_instance_proxy_config
Revises: 0001_initial_wootify
Create Date: 2026-02-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0002_add_instance_proxy_config'
down_revision = '0001_initial_wootify'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade."""
    op.add_column(
        'instances',
        sa.Column('proxy_config_encrypted', sa.Text(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    """Downgrade."""
    op.drop_column('instances', 'proxy_config_encrypted')

