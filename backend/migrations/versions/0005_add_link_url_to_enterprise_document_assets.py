"""add link_url to enterprise document assets

Revision ID: 0005_add_link_url_to_enterprise_document_assets
Revises: 0004_add_bale_enterprise_tables
Create Date: 2026-04-05 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0005_add_link_url_to_enterprise_document_assets'
down_revision = '0004_add_bale_enterprise_tables'
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(col.get('name') == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if _has_column('enterprise_document_assets', 'link_url'):
        return

    with op.batch_alter_table('enterprise_document_assets') as batch_op:
        batch_op.add_column(sa.Column('link_url', sa.String(length=1024), nullable=True, server_default=''))

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE enterprise_document_assets SET link_url = '' WHERE link_url IS NULL"))

    with op.batch_alter_table('enterprise_document_assets') as batch_op:
        batch_op.alter_column('link_url', existing_type=sa.String(length=1024), nullable=False, server_default='')


def downgrade() -> None:
    if not _has_column('enterprise_document_assets', 'link_url'):
        return

    with op.batch_alter_table('enterprise_document_assets') as batch_op:
        batch_op.drop_column('link_url')
