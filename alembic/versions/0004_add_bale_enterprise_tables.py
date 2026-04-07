"""add bale enterprise tables and platform type

Revision ID: 0004_add_bale_enterprise_tables
Revises: 0003_add_active_conversation_mapping
Create Date: 2026-03-17 10:00:00
"""
from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = '0004_add_bale_enterprise_tables'
down_revision = '0003_add_active_conversation_mapping'
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(item.get('name') == index_name for item in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table('enterprise_bale_users'):
        op.create_table(
            'enterprise_bale_users',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('instance_id', sa.String(length=36), nullable=False),
            sa.Column('platform_chat_id', sa.String(length=255), nullable=False),
            sa.Column('display_name', sa.String(length=255), nullable=True),
            sa.Column('phone_number', sa.String(length=64), nullable=True),
            sa.Column('gre_status', sa.String(length=16), nullable=False, server_default='unknown'),
            sa.Column('current_state', sa.String(length=64), nullable=False, server_default='awaiting_phone_input'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('instance_id', 'platform_chat_id', name='uq_enterprise_bale_user_instance_chat'),
        )
        op.create_index('ix_enterprise_bale_users_instance_id', 'enterprise_bale_users', ['instance_id'])
        op.create_index('ix_enterprise_bale_users_platform_chat_id', 'enterprise_bale_users', ['platform_chat_id'])

    if not _has_table('enterprise_bale_sessions'):
        op.create_table(
            'enterprise_bale_sessions',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('user_id', sa.String(length=36), nullable=False),
            sa.Column('route_key', sa.String(length=64), nullable=False),
            sa.Column('chatwoot_conversation_id', sa.String(length=255), nullable=False),
            sa.Column('chatwoot_contact_id', sa.String(length=255), nullable=True),
            sa.Column('chatwoot_inbox_id', sa.String(length=255), nullable=True),
            sa.Column('status', sa.String(length=24), nullable=False, server_default='open'),
            sa.Column('user_present', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('accepted_notice_sent', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('unread_notice_sent', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('unread_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['user_id'], ['enterprise_bale_users.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_enterprise_bale_sessions_user_id', 'enterprise_bale_sessions', ['user_id'])
        op.create_index('ix_enterprise_bale_sessions_route_key', 'enterprise_bale_sessions', ['route_key'])
        op.create_index(
            'ix_enterprise_bale_sessions_chatwoot_conversation_id',
            'enterprise_bale_sessions',
            ['chatwoot_conversation_id'],
        )
        op.create_index('ix_enterprise_bale_sessions_chatwoot_contact_id', 'enterprise_bale_sessions', ['chatwoot_contact_id'])

    if not _has_table('enterprise_bale_pending_messages'):
        op.create_table(
            'enterprise_bale_pending_messages',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('session_id', sa.String(length=36), nullable=False),
            sa.Column('chatwoot_message_id', sa.String(length=255), nullable=True),
            sa.Column('text_payload', sa.Text(), nullable=True),
            sa.Column('attachment_payload_json', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
            sa.Column('delivery_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['session_id'], ['enterprise_bale_sessions.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('session_id', 'chatwoot_message_id', name='uq_enterprise_pending_message_session_chatwoot'),
        )
        op.create_index('ix_enterprise_bale_pending_messages_session_id', 'enterprise_bale_pending_messages', ['session_id'])
        op.create_index(
            'ix_enterprise_bale_pending_messages_chatwoot_message_id',
            'enterprise_bale_pending_messages',
            ['chatwoot_message_id'],
        )

    if not _has_table('enterprise_document_assets'):
        op.create_table(
            'enterprise_document_assets',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('instance_id', sa.String(length=36), nullable=False),
            sa.Column('asset_type', sa.String(length=16), nullable=False),
            sa.Column('display_name', sa.String(length=255), nullable=True),
            sa.Column('storage_path', sa.String(length=512), nullable=False),
            sa.Column('original_filename', sa.String(length=255), nullable=False),
            sa.Column('content_type', sa.String(length=255), nullable=True),
            sa.Column('size_bytes', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_enterprise_document_assets_instance_id', 'enterprise_document_assets', ['instance_id'])
        op.create_index('ix_enterprise_document_assets_asset_type', 'enterprise_document_assets', ['asset_type'])
        op.create_index('ix_enterprise_document_assets_is_active', 'enterprise_document_assets', ['is_active'])

    existing_platform = bind.execute(
        sa.text("SELECT id FROM platform_types WHERE key = 'bale_enterprise' LIMIT 1")
    ).fetchone()
    if not existing_platform:
        bind.execute(
            sa.text(
                """
                INSERT INTO platform_types (
                    id,
                    key,
                    display_name,
                    capabilities_json,
                    metadata_schema_json,
                    is_active
                ) VALUES (
                    :id,
                    :key,
                    :display_name,
                    :capabilities_json,
                    :metadata_schema_json,
                    :is_active
                )
                """
            ),
            {
                'id': str(uuid.uuid4()),
                'key': 'bale_enterprise',
                'display_name': 'Bale Enterprise',
                'capabilities_json': json.dumps(
                    {
                        'send_text': True,
                        'send_media': True,
                        'reply_sync': False,
                        'inbound_polling': True,
                        'mark_as_read': False,
                    }
                ),
                'metadata_schema_json': json.dumps(
                    {
                        'type': 'object',
                        'required': ['bale_token'],
                        'properties': {
                            'bale_token': {'type': 'string'},
                            'bale_api_base_url': {'type': 'string'},
                            'bale_file_base_url': {'type': 'string'},
                            'bale_poll_interval': {'type': 'integer'},
                            'bale_bot_name': {'type': 'string'},
                            'bale_bot_id': {'type': 'string'},
                            'bale_department': {'type': 'string'},
                            'enterprise_address_tehran_alborz_text': {'type': 'string'},
                            'enterprise_address_other_provinces_text': {'type': 'string'},
                            'enterprise_customer_service_inbox_id': {'type': 'integer'},
                            'enterprise_customer_service_inbox_name': {'type': 'string'},
                            'enterprise_customer_service_auto_create': {'type': 'boolean'},
                            'enterprise_customer_service_waiting_text': {'type': 'string'},
                            'enterprise_customer_service_accepted_text': {'type': 'string'},
                            'enterprise_customer_service_unread_text': {'type': 'string'},
                            'enterprise_sales_inbox_id': {'type': 'integer'},
                            'enterprise_sales_inbox_name': {'type': 'string'},
                            'enterprise_sales_auto_create': {'type': 'boolean'},
                            'enterprise_sales_waiting_text': {'type': 'string'},
                            'enterprise_sales_accepted_text': {'type': 'string'},
                            'enterprise_sales_unread_text': {'type': 'string'},
                        },
                    }
                ),
                'is_active': True,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM platform_types WHERE key = 'bale_enterprise'"))

    if _has_table('enterprise_document_assets'):
        if _has_index('enterprise_document_assets', 'ix_enterprise_document_assets_is_active'):
            op.drop_index('ix_enterprise_document_assets_is_active', table_name='enterprise_document_assets')
        if _has_index('enterprise_document_assets', 'ix_enterprise_document_assets_asset_type'):
            op.drop_index('ix_enterprise_document_assets_asset_type', table_name='enterprise_document_assets')
        if _has_index('enterprise_document_assets', 'ix_enterprise_document_assets_instance_id'):
            op.drop_index('ix_enterprise_document_assets_instance_id', table_name='enterprise_document_assets')
        op.drop_table('enterprise_document_assets')

    if _has_table('enterprise_bale_pending_messages'):
        if _has_index('enterprise_bale_pending_messages', 'ix_enterprise_bale_pending_messages_chatwoot_message_id'):
            op.drop_index(
                'ix_enterprise_bale_pending_messages_chatwoot_message_id',
                table_name='enterprise_bale_pending_messages',
            )
        if _has_index('enterprise_bale_pending_messages', 'ix_enterprise_bale_pending_messages_session_id'):
            op.drop_index('ix_enterprise_bale_pending_messages_session_id', table_name='enterprise_bale_pending_messages')
        op.drop_table('enterprise_bale_pending_messages')

    if _has_table('enterprise_bale_sessions'):
        if _has_index('enterprise_bale_sessions', 'ix_enterprise_bale_sessions_chatwoot_contact_id'):
            op.drop_index('ix_enterprise_bale_sessions_chatwoot_contact_id', table_name='enterprise_bale_sessions')
        if _has_index('enterprise_bale_sessions', 'ix_enterprise_bale_sessions_chatwoot_conversation_id'):
            op.drop_index('ix_enterprise_bale_sessions_chatwoot_conversation_id', table_name='enterprise_bale_sessions')
        if _has_index('enterprise_bale_sessions', 'ix_enterprise_bale_sessions_route_key'):
            op.drop_index('ix_enterprise_bale_sessions_route_key', table_name='enterprise_bale_sessions')
        if _has_index('enterprise_bale_sessions', 'ix_enterprise_bale_sessions_user_id'):
            op.drop_index('ix_enterprise_bale_sessions_user_id', table_name='enterprise_bale_sessions')
        op.drop_table('enterprise_bale_sessions')

    if _has_table('enterprise_bale_users'):
        if _has_index('enterprise_bale_users', 'ix_enterprise_bale_users_platform_chat_id'):
            op.drop_index('ix_enterprise_bale_users_platform_chat_id', table_name='enterprise_bale_users')
        if _has_index('enterprise_bale_users', 'ix_enterprise_bale_users_instance_id'):
            op.drop_index('ix_enterprise_bale_users_instance_id', table_name='enterprise_bale_users')
        op.drop_table('enterprise_bale_users')
