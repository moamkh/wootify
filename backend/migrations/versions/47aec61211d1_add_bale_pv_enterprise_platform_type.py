
"""add bale pv enterprise platform type

Revision ID: 47aec61211d1
Revises: ff196af55240
Create Date: 2026-06-01 15:02:47.004448

"""

from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '47aec61211d1'
down_revision = 'ff196af55240'
branch_labels = None
depends_on = None


BALE_PV_ENTERPRISE_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': True,
    'inbound_polling': True,
    'mark_as_read': False,
}

BALE_PV_ENTERPRISE_METADATA_SCHEMA = {
    'type': 'object',
    'required': ['bale_pv_phone_number'],
    'properties': {
        'bale_pv_phone_number': {'type': 'string'},
        'bale_pv_session_dir': {'type': 'string'},
        'bale_pv_poll_interval': {'type': 'integer'},
        'bale_pv_display_name': {'type': 'string'},
        'bale_pv_department': {'type': 'string'},
        'bale_pv_share_phone_prompt_enabled': {'type': 'boolean'},
        'bale_pv_share_phone_prompt_only_if_missing_phone': {'type': 'boolean'},
        'bale_pv_share_phone_prompt_text': {'type': 'string'},
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM platform_types WHERE key = 'bale_pv_enterprise' LIMIT 1")
    ).fetchone()
    if not existing:
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
                'key': 'bale_pv_enterprise',
                'display_name': 'Bale PV (Personal)',
                'capabilities_json': json.dumps(BALE_PV_ENTERPRISE_CAPABILITIES),
                'metadata_schema_json': json.dumps(BALE_PV_ENTERPRISE_METADATA_SCHEMA),
                'is_active': True,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM platform_types WHERE key = 'bale_pv_enterprise'"))
