"""add telegram enterprise tables and platform type

Revision ID: 0008_add_telegram_enterprise_tables
Revises: 0007_add_current_group_id
Create Date: 2026-05-10 12:00:00
"""
from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = '0008_add_telegram_enterprise_tables'
down_revision = '0007_add_current_group_id'
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

    if not _has_table('enterprise_telegram_users'):
        op.create_table(
            'enterprise_telegram_users',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('instance_id', sa.String(length=36), nullable=False),
            sa.Column('platform_chat_id', sa.String(length=255), nullable=False),
            sa.Column('display_name', sa.String(length=255), nullable=True),
            sa.Column('phone_number', sa.String(length=64), nullable=True),
            sa.Column('current_state', sa.String(length=64), nullable=False, server_default='root'),
            sa.Column('current_group_id', sa.String(length=36), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['current_group_id'], ['enterprise_manual_groups.id'], ondelete='SET NULL'),
            sa.UniqueConstraint('instance_id', 'platform_chat_id', name='uq_enterprise_telegram_user_instance_chat'),
        )
        op.create_index('ix_enterprise_telegram_users_instance_id', 'enterprise_telegram_users', ['instance_id'])
        op.create_index('ix_enterprise_telegram_users_platform_chat_id', 'enterprise_telegram_users', ['platform_chat_id'])

    if not _has_table('enterprise_telegram_sessions'):
        op.create_table(
            'enterprise_telegram_sessions',
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
            sa.ForeignKeyConstraint(['user_id'], ['enterprise_telegram_users.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_enterprise_telegram_sessions_user_id', 'enterprise_telegram_sessions', ['user_id'])
        op.create_index('ix_enterprise_telegram_sessions_route_key', 'enterprise_telegram_sessions', ['route_key'])
        op.create_index(
            'ix_enterprise_telegram_sessions_chatwoot_conversation_id',
            'enterprise_telegram_sessions',
            ['chatwoot_conversation_id'],
        )
        op.create_index('ix_enterprise_telegram_sessions_chatwoot_contact_id', 'enterprise_telegram_sessions', ['chatwoot_contact_id'])

    if not _has_table('enterprise_telegram_pending_messages'):
        op.create_table(
            'enterprise_telegram_pending_messages',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('session_id', sa.String(length=36), nullable=False),
            sa.Column('chatwoot_message_id', sa.String(length=255), nullable=True),
            sa.Column('text_payload', sa.Text(), nullable=True),
            sa.Column('attachment_payload_json', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
            sa.Column('delivery_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
            sa.ForeignKeyConstraint(['session_id'], ['enterprise_telegram_sessions.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('session_id', 'chatwoot_message_id', name='uq_enterprise_telegram_pending_message_session_chatwoot'),
        )
        op.create_index('ix_enterprise_telegram_pending_messages_session_id', 'enterprise_telegram_pending_messages', ['session_id'])
        op.create_index(
            'ix_enterprise_telegram_pending_messages_chatwoot_message_id',
            'enterprise_telegram_pending_messages',
            ['chatwoot_message_id'],
        )

    existing_platform = bind.execute(
        sa.text("SELECT id FROM platform_types WHERE key = 'telegram_enterprise' LIMIT 1")
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
                'key': 'telegram_enterprise',
                'display_name': 'Telegram Enterprise',
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
                        'required': ['telegram_token'],
                        'properties': {
                            'telegram_token': {'type': 'string'},
                            'telegram_api_base_url': {'type': 'string'},
                            'telegram_file_base_url': {'type': 'string'},
                            'telegram_poll_interval': {'type': 'integer'},
                            'telegram_bot_name': {'type': 'string'},
                            'telegram_bot_id': {'type': 'string'},
                            'telegram_department': {'type': 'string'},
                            'enterprise_welcome_text': {'type': 'string'},
                            'enterprise_menu_prompt_text': {'type': 'string'},
                            'enterprise_address_prompt_text': {'type': 'string'},
                            'enterprise_not_configured_text': {'type': 'string'},
                            'enterprise_live_mode_resume_text': {'type': 'string'},
                            'enterprise_live_session_locked_text': {'type': 'string'},
                            'enterprise_no_manuals_text': {'type': 'string'},
                            'enterprise_no_catalog_text': {'type': 'string'},
                            'enterprise_address_tehran_alborz_text': {'type': 'string'},
                            'enterprise_address_other_provinces_text': {'type': 'string'},
                            'enterprise_user_manual_link_template': {'type': 'string'},
                            'enterprise_catalog_button_label': {'type': 'string'},
                            'enterprise_manuals_button_label': {'type': 'string'},
                            'enterprise_address_button_label': {'type': 'string'},
                            'enterprise_back_button_label': {'type': 'string'},
                            'enterprise_routes': {'type': 'array'},
                        },
                    }
                ),
                'is_active': True,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM platform_types WHERE key = 'telegram_enterprise'"))

    if _has_table('enterprise_telegram_pending_messages'):
        if _has_index('enterprise_telegram_pending_messages', 'ix_enterprise_telegram_pending_messages_chatwoot_message_id'):
            op.drop_index(
                'ix_enterprise_telegram_pending_messages_chatwoot_message_id',
                table_name='enterprise_telegram_pending_messages',
            )
        if _has_index('enterprise_telegram_pending_messages', 'ix_enterprise_telegram_pending_messages_session_id'):
            op.drop_index('ix_enterprise_telegram_pending_messages_session_id', table_name='enterprise_telegram_pending_messages')
        op.drop_table('enterprise_telegram_pending_messages')

    if _has_table('enterprise_telegram_sessions'):
        if _has_index('enterprise_telegram_sessions', 'ix_enterprise_telegram_sessions_chatwoot_contact_id'):
            op.drop_index('ix_enterprise_telegram_sessions_chatwoot_contact_id', table_name='enterprise_telegram_sessions')
        if _has_index('enterprise_telegram_sessions', 'ix_enterprise_telegram_sessions_chatwoot_conversation_id'):
            op.drop_index('ix_enterprise_telegram_sessions_chatwoot_conversation_id', table_name='enterprise_telegram_sessions')
        if _has_index('enterprise_telegram_sessions', 'ix_enterprise_telegram_sessions_route_key'):
            op.drop_index('ix_enterprise_telegram_sessions_route_key', table_name='enterprise_telegram_sessions')
        if _has_index('enterprise_telegram_sessions', 'ix_enterprise_telegram_sessions_user_id'):
            op.drop_index('ix_enterprise_telegram_sessions_user_id', table_name='enterprise_telegram_sessions')
        op.drop_table('enterprise_telegram_sessions')

    if _has_table('enterprise_telegram_users'):
        if _has_index('enterprise_telegram_users', 'ix_enterprise_telegram_users_platform_chat_id'):
            op.drop_index('ix_enterprise_telegram_users_platform_chat_id', table_name='enterprise_telegram_users')
        if _has_index('enterprise_telegram_users', 'ix_enterprise_telegram_users_instance_id'):
            op.drop_index('ix_enterprise_telegram_users_instance_id', table_name='enterprise_telegram_users')
        op.drop_table('enterprise_telegram_users')
