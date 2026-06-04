"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.feature_repository import FeatureRepository
from app.repositories.platform_repository import PlatformRepository
from app.schemas.api_v1 import FeatureDefinitionResponse, PlatformTypeResponse

logger = logging.getLogger('app.services.platform_registry')


BALE_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': True,
    'inbound_polling': True,
    'mark_as_read': False,
}

BALE_ENTERPRISE_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': False,
    'inbound_polling': True,
    'mark_as_read': False,
}

BALE_PV_ENTERPRISE_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': True,
    'inbound_polling': True,
    'mark_as_read': False,
}

TELEGRAM_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': True,
    'inbound_polling': True,
    'mark_as_read': False,
}

TELEGRAM_ENTERPRISE_CAPABILITIES = {
    'send_text': True,
    'send_media': True,
    'reply_sync': False,
    'inbound_polling': True,
    'mark_as_read': False,
}

BALE_METADATA_SCHEMA: dict[str, Any] = {
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
        'bale_share_phone_prompt_enabled': {'type': 'boolean'},
        'bale_share_phone_prompt_only_if_missing_phone': {'type': 'boolean'},
        'bale_share_phone_prompt_text': {'type': 'string'},
    },
}

BALE_PV_ENTERPRISE_METADATA_SCHEMA: dict[str, Any] = {
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

BALE_ENTERPRISE_METADATA_SCHEMA: dict[str, Any] = {
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
        'enterprise_welcome_text': {'type': 'string'},
        'enterprise_phone_prompt_text': {'type': 'string'},
        'enterprise_menu_prompt_text': {'type': 'string'},
        'enterprise_address_prompt_text': {'type': 'string'},
        'enterprise_number_not_found_text': {'type': 'string'},
        'enterprise_no_manuals_text': {'type': 'string'},
        'enterprise_no_catalog_text': {'type': 'string'},
        'enterprise_not_configured_text': {'type': 'string'},
        'enterprise_live_mode_resume_text': {'type': 'string'},
        'enterprise_invalid_phone_text': {'type': 'string'},
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
        'enterprise_sms_sync_enabled': {'type': 'boolean'},
        'enterprise_sms_api_url': {'type': 'string'},
        'enterprise_sms_api_token': {'type': 'string'},
        'enterprise_sms_token_header': {'type': 'string'},
        'enterprise_sms_token_prefix': {'type': 'string'},
        'enterprise_sms_poll_interval_minutes': {'type': 'integer'},
        'enterprise_sms_last_id': {'type': 'integer'},
        'enterprise_sms_http_timeout_seconds': {'type': 'integer'},
    },
}

TELEGRAM_METADATA_SCHEMA: dict[str, Any] = {
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
        'telegram_share_phone_prompt_enabled': {'type': 'boolean'},
        'telegram_share_phone_prompt_only_if_missing_phone': {'type': 'boolean'},
        'telegram_share_phone_prompt_text': {'type': 'string'},
    },
}

TELEGRAM_ENTERPRISE_METADATA_SCHEMA: dict[str, Any] = {
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

DEFAULT_FEATURES = [
    {
        'key': 'reply_sync',
        'display_name': 'Reply Sync',
        'description': 'Send reply threading metadata between Chatwoot and platform when supported.',
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
]


class PlatformRegistryService:
    """Service for platform registry domain workflows."""
    def ensure_seed_data(self, db: Session) -> None:
        """Ensure seed data."""
        try:
            platform_repo = PlatformRepository(db)
            feature_repo = FeatureRepository(db)

            platform_repo.upsert(
                key='bale',
                display_name='Bale',
                capabilities_json=BALE_CAPABILITIES,
                metadata_schema_json=BALE_METADATA_SCHEMA,
                is_active=True,
            )
            platform_repo.upsert(
                key='bale_enterprise',
                display_name='Bale Enterprise',
                capabilities_json=BALE_ENTERPRISE_CAPABILITIES,
                metadata_schema_json=BALE_ENTERPRISE_METADATA_SCHEMA,
                is_active=True,
            )
            platform_repo.upsert(
                key='bale_pv_enterprise',
                display_name='Bale PV (Personal)',
                capabilities_json=BALE_PV_ENTERPRISE_CAPABILITIES,
                metadata_schema_json=BALE_PV_ENTERPRISE_METADATA_SCHEMA,
                is_active=True,
            )
            platform_repo.upsert(
                key='telegram',
                display_name='Telegram',
                capabilities_json=TELEGRAM_CAPABILITIES,
                metadata_schema_json=TELEGRAM_METADATA_SCHEMA,
                is_active=True,
            )
            platform_repo.upsert(
                key='telegram_enterprise',
                display_name='Telegram Enterprise',
                capabilities_json=TELEGRAM_ENTERPRISE_CAPABILITIES,
                metadata_schema_json=TELEGRAM_ENTERPRISE_METADATA_SCHEMA,
                is_active=True,
            )

            for feature in DEFAULT_FEATURES:
                feature_repo.upsert(**feature)

            db.commit()
        except Exception:
            logger.exception('ensure_seed_data failed')
            raise

    def list_platform_types(self, db: Session) -> list[PlatformTypeResponse]:
        """List platform types."""
        try:
            rows = PlatformRepository(db).list_active()
            return [
                PlatformTypeResponse(
                    id=row.id,
                    key=row.key,
                    display_name=row.display_name,
                    capabilities=row.capabilities_json or {},
                    metadata_schema=row.metadata_schema_json or {},
                    is_active=bool(row.is_active),
                )
                for row in rows
            ]
        except Exception:
            logger.exception('list_platform_types failed')
            raise

    def list_features(self, db: Session) -> list[FeatureDefinitionResponse]:
        """List features."""
        try:
            rows = FeatureRepository(db).list_all()
            return [
                FeatureDefinitionResponse(
                    key=row.key,
                    display_name=row.display_name,
                    description=row.description,
                    default_enabled=bool(row.default_enabled),
                    required_platform_capability=row.required_platform_capability,
                    required_chatwoot_capability=row.required_chatwoot_capability,
                )
                for row in rows
            ]
        except Exception:
            logger.exception('list_features failed')
            raise

