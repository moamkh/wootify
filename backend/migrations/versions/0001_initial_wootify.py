"""initial wootify schema

Revision ID: 0001_initial_wootify
Revises:
Create Date: 2026-02-12
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = '0001_initial_wootify'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade."""
    op.create_table(
        'platform_types',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=False),
        sa.Column('capabilities_json', sa.JSON(), nullable=False),
        sa.Column('metadata_schema_json', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.UniqueConstraint('key', name='uq_platform_types_key'),
    )
    op.create_index('ix_platform_types_key', 'platform_types', ['key'])

    op.create_table(
        'feature_definitions',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('display_name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('default_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('required_platform_capability', sa.String(length=64), nullable=True),
        sa.Column('required_chatwoot_capability', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
    )

    op.create_table(
        'instances',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('instance_key', sa.String(length=128), nullable=False),
        sa.Column('platform_type_id', sa.String(length=36), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('platform_metadata_encrypted', sa.Text(), nullable=False, server_default=''),
        sa.Column('chatwoot_config_encrypted', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.ForeignKeyConstraint(['platform_type_id'], ['platform_types.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('instance_key', name='uq_instances_instance_key'),
    )
    op.create_index('ix_instances_instance_key', 'instances', ['instance_key'])
    op.create_index('ix_instances_platform_type_id', 'instances', ['platform_type_id'])

    op.create_table(
        'instance_feature_overrides',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('instance_id', sa.String(length=36), nullable=False),
        sa.Column('feature_key', sa.String(length=64), nullable=False),
        sa.Column('requested_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('effective_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('disabled_reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['feature_key'], ['feature_definitions.key'], ondelete='CASCADE'),
        sa.UniqueConstraint('instance_id', 'feature_key', name='uq_instance_feature_override'),
    )
    op.create_index('ix_instance_feature_overrides_instance_id', 'instance_feature_overrides', ['instance_id'])
    op.create_index('ix_instance_feature_overrides_feature_key', 'instance_feature_overrides', ['feature_key'])

    op.create_table(
        'instance_runtime_state',
        sa.Column('instance_id', sa.String(length=36), primary_key=True),
        sa.Column('last_platform_update_id', sa.String(length=255), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('instance_id', sa.String(length=36), nullable=False),
        sa.Column('platform_conversation_id', sa.String(length=255), nullable=False),
        sa.Column('chatwoot_conversation_id', sa.String(length=255), nullable=False),
        sa.Column('chatwoot_contact_id', sa.String(length=255), nullable=True),
        sa.Column('chatwoot_inbox_id', sa.String(length=255), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('instance_id', 'platform_conversation_id', name='uq_instance_platform_conversation'),
        sa.UniqueConstraint('instance_id', 'chatwoot_conversation_id', name='uq_instance_chatwoot_conversation'),
    )
    op.create_index('ix_conversations_instance_id', 'conversations', ['instance_id'])
    op.create_index('ix_conversations_platform_conversation_id', 'conversations', ['platform_conversation_id'])
    op.create_index('ix_conversations_chatwoot_conversation_id', 'conversations', ['chatwoot_conversation_id'])

    op.create_table(
        'message_mappings',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('direction', sa.String(length=32), nullable=False),
        sa.Column('chatwoot_message_id', sa.String(length=255), nullable=True),
        sa.Column('platform_message_id', sa.String(length=255), nullable=True),
        sa.Column('chatwoot_parent_message_id', sa.String(length=255), nullable=True),
        sa.Column('platform_parent_message_id', sa.String(length=255), nullable=True),
        sa.Column('message_kind', sa.String(length=16), nullable=False, server_default='text'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('chatwoot_payload_json', sa.JSON(), nullable=True),
        sa.Column('platform_payload_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('(CURRENT_TIMESTAMP)')),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('conversation_id', 'chatwoot_message_id', name='uq_conversation_chatwoot_message'),
        sa.UniqueConstraint('conversation_id', 'platform_message_id', name='uq_conversation_platform_message'),
    )
    op.create_index('ix_message_mappings_conversation_id', 'message_mappings', ['conversation_id'])
    op.create_index('ix_message_mappings_chatwoot_message_id', 'message_mappings', ['chatwoot_message_id'])
    op.create_index('ix_message_mappings_platform_message_id', 'message_mappings', ['platform_message_id'])
    op.create_index(
        'ix_message_mappings_conversation_direction_created',
        'message_mappings',
        ['conversation_id', 'direction', 'created_at'],
    )

    platform_types = sa.table(
        'platform_types',
        sa.column('id', sa.String(length=36)),
        sa.column('key', sa.String(length=64)),
        sa.column('display_name', sa.String(length=128)),
        sa.column('capabilities_json', sa.JSON()),
        sa.column('metadata_schema_json', sa.JSON()),
        sa.column('is_active', sa.Boolean()),
    )
    op.bulk_insert(
        platform_types,
        [
            {
                'id': str(uuid.uuid4()),
                'key': 'bale',
                'display_name': 'Bale',
                'capabilities_json': {
                    'send_text': True,
                    'send_media': True,
                    'reply_sync': True,
                    'inbound_polling': True,
                    'mark_as_read': False,
                },
                'metadata_schema_json': {
                    'type': 'object',
                    'required': ['bale_token'],
                    'properties': {
                        'bale_token': {'type': 'string'},
                        'bale_api_base_url': {'type': 'string'},
                        'bale_file_base_url': {'type': 'string'},
                        'bale_poll_interval': {'type': 'integer'},
                    },
                },
                'is_active': True,
            }
        ],
    )

    feature_definitions = sa.table(
        'feature_definitions',
        sa.column('key', sa.String(length=64)),
        sa.column('display_name', sa.String(length=128)),
        sa.column('description', sa.Text()),
        sa.column('default_enabled', sa.Boolean()),
        sa.column('required_platform_capability', sa.String(length=64)),
        sa.column('required_chatwoot_capability', sa.String(length=64)),
    )
    op.bulk_insert(
        feature_definitions,
        [
            {
                'key': 'reply_sync',
                'display_name': 'Reply Sync',
                'description': 'Send reply threading metadata between Chatwoot and Bale.',
                'default_enabled': True,
                'required_platform_capability': 'reply_sync',
                'required_chatwoot_capability': None,
            },
            {
                'key': 'media_sync',
                'display_name': 'Media Sync',
                'description': 'Send and receive media attachments.',
                'default_enabled': True,
                'required_platform_capability': 'send_media',
                'required_chatwoot_capability': None,
            },
            {
                'key': 'payload_debug_store',
                'display_name': 'Payload Debug Store',
                'description': 'Persist sanitized payload snapshots for debugging.',
                'default_enabled': False,
                'required_platform_capability': None,
                'required_chatwoot_capability': None,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade."""
    op.drop_index('ix_message_mappings_conversation_direction_created', table_name='message_mappings')
    op.drop_index('ix_message_mappings_platform_message_id', table_name='message_mappings')
    op.drop_index('ix_message_mappings_chatwoot_message_id', table_name='message_mappings')
    op.drop_index('ix_message_mappings_conversation_id', table_name='message_mappings')
    op.drop_table('message_mappings')

    op.drop_index('ix_conversations_chatwoot_conversation_id', table_name='conversations')
    op.drop_index('ix_conversations_platform_conversation_id', table_name='conversations')
    op.drop_index('ix_conversations_instance_id', table_name='conversations')
    op.drop_table('conversations')

    op.drop_table('instance_runtime_state')

    op.drop_index('ix_instance_feature_overrides_feature_key', table_name='instance_feature_overrides')
    op.drop_index('ix_instance_feature_overrides_instance_id', table_name='instance_feature_overrides')
    op.drop_table('instance_feature_overrides')

    op.drop_index('ix_instances_platform_type_id', table_name='instances')
    op.drop_index('ix_instances_instance_key', table_name='instances')
    op.drop_table('instances')

    op.drop_table('feature_definitions')

    op.drop_index('ix_platform_types_key', table_name='platform_types')
    op.drop_table('platform_types')

