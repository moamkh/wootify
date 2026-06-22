"""add enterprise manual groups and assignments

Revision ID: 0006_add_enterprise_manual_groups
Revises: 0005_add_link_url_to_enterprise_document_assets
Create Date: 2026-04-09 14:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0006_add_enterprise_manual_groups'
down_revision = '0005_add_link_url_to_enterprise_document_assets'
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table('enterprise_manual_groups'):
        op.create_table(
            'enterprise_manual_groups',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('instance_id', sa.String(length=36), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('instance_id', 'name', name='uq_enterprise_manual_group_instance_name'),
        )
        op.create_index('ix_enterprise_manual_groups_instance_id', 'enterprise_manual_groups', ['instance_id'])
        op.create_index('ix_enterprise_manual_groups_sort_order', 'enterprise_manual_groups', ['instance_id', 'sort_order'])

    if not _has_table('enterprise_manual_group_assignments'):
        op.create_table(
            'enterprise_manual_group_assignments',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('group_id', sa.String(length=36), nullable=False),
            sa.Column('asset_id', sa.String(length=36), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['group_id'], ['enterprise_manual_groups.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['asset_id'], ['enterprise_document_assets.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('group_id', 'asset_id', name='uq_enterprise_manual_group_assignment_group_asset'),
        )
        op.create_index('ix_enterprise_manual_group_assignments_group_id', 'enterprise_manual_group_assignments', ['group_id'])
        op.create_index('ix_enterprise_manual_group_assignments_asset_id', 'enterprise_manual_group_assignments', ['asset_id'])
        op.create_index('ix_enterprise_manual_group_assignments_sort_order', 'enterprise_manual_group_assignments', ['group_id', 'sort_order'])


def downgrade() -> None:
    if _has_table('enterprise_manual_group_assignments'):
        op.drop_table('enterprise_manual_group_assignments')

    if _has_table('enterprise_manual_groups'):
        op.drop_table('enterprise_manual_groups')
