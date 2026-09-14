"""add Eitaa PV personal platform type.

Revision ID: d2e9f8a7c6b5
Revises: c7a2f1d94e05
"""

from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "d2e9f8a7c6b5"
down_revision = "c7a2f1d94e05"
branch_labels = None
depends_on = None


CAPABILITIES = {
    "send_text": True,
    "send_media": True,
    "reply_sync": True,
    "inbound_polling": True,
    "mark_as_read": True,
    "edit_message": True,
    "delete_message": True,
}

METADATA_SCHEMA = {
    "type": "object",
    "required": ["eitaa_pv_phone_number"],
    "properties": {
        "eitaa_pv_phone_number": {"type": "string"},
        "eitaa_pv_session_dir": {"type": "string"},
        "eitaa_pv_poll_interval": {"type": "integer"},
        "eitaa_pv_display_name": {"type": "string"},
        "eitaa_pv_department": {"type": "string"},
        "eitaa_pv_endpoint": {"type": "string"},
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT id FROM platform_types WHERE key = 'eitaa_pv_enterprise' LIMIT 1")
    ).fetchone()
    if not exists:
        bind.execute(
            sa.text(
                """
                INSERT INTO platform_types (
                    id, key, display_name, capabilities_json,
                    metadata_schema_json, is_active
                ) VALUES (
                    :id, :key, :display_name, :capabilities_json,
                    :metadata_schema_json, :is_active
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "key": "eitaa_pv_enterprise",
                "display_name": "Eitaa PV (Personal)",
                "capabilities_json": json.dumps(CAPABILITIES),
                "metadata_schema_json": json.dumps(METADATA_SCHEMA),
                "is_active": True,
            },
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM platform_types WHERE key = 'eitaa_pv_enterprise'")
    )
