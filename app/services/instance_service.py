"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import logging
import shutil
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.config import repo_root, settings
from app.models import Instance, InstanceFeatureOverride, PlatformType
from app.repositories.feature_repository import FeatureRepository
from app.repositories.instance_repository import InstanceRepository
from app.repositories.platform_repository import PlatformRepository
from app.repositories.runtime_state_repository import RuntimeStateRepository
from app.schemas.api_v1 import FeatureOverrideResponse, InstanceCreateRequest, InstancePatchRequest, InstanceResponse
from app.utils.cache_utils import TTLCache
from app.utils.crypto_utils import encryptor
from app.utils.payload_utils import mask_secret


CHATWOOT_CAPABILITIES = {
    'reply_sync': True,
    'media_sync': True,
}

PLATFORM_REQUIRED_TOKEN_KEY = {
    'bale': 'bale_token',
    'bale_enterprise': 'bale_token',
    'bale_pv_enterprise': 'bale_pv_phone_number',
    'telegram': 'telegram_token',
    'telegram_enterprise': 'telegram_token',
}

logger = logging.getLogger('app.services.instance')


@dataclass
class RuntimeInstance:
    """Represents runtime instance."""
    instance: Instance
    platform_type: PlatformType
    platform_metadata: dict[str, Any]
    chatwoot: dict[str, Any]
    proxy: dict[str, Any]
    feature_flags: dict[str, bool]
    feature_overrides: list[FeatureOverrideResponse]
    runtime_state_last_update_id: Optional[str] = None
    runtime_state_last_sms_sync_at: Optional[_dt.datetime] = None


@dataclass(frozen=True)
class _CachedFeatureDef:
    key: str
    default_enabled: bool
    required_platform_capability: Optional[str]
    required_chatwoot_capability: Optional[str]


# Module-level caches to avoid repeated decryption and feature override computation.
_runtime_instance_cache: TTLCache[RuntimeInstance] = TTLCache(maxsize=200, ttl=60)
_feature_defs_cache: list[_CachedFeatureDef] | None = None


def _invalidate_instance_cache(instance_key: str) -> None:
    _runtime_instance_cache.pop(instance_key)


def _invalidate_feature_defs_cache() -> None:
    global _feature_defs_cache
    _feature_defs_cache = None


class InstanceService:
    """Service for instance domain workflows."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._instance_repo = InstanceRepository
        self._platform_repo = PlatformRepository
        self._feature_repo = FeatureRepository
        self._runtime_repo = RuntimeStateRepository

    def create_instance(self, db: Session, payload: InstanceCreateRequest) -> InstanceResponse:
        """Create instance."""
        try:
            repo = self._instance_repo(db)
            if repo.get_by_key(payload.instance_key):
                raise ValueError(f"Instance '{payload.instance_key}' already exists")

            platform = self._require_platform(db, payload.platform_type_key)
            platform_metadata = self._normalize_platform_metadata(platform.key, payload.platform_metadata)
            chatwoot = self._normalize_chatwoot_config(payload.chatwoot)
            proxy = self._normalize_proxy_config(payload.proxy.model_dump(exclude_none=True), validate_dependencies=True)
            required_token_key = self._required_platform_token_key(platform.key)
            if required_token_key and not platform_metadata.get(required_token_key):
                raise ValueError(f'platform_metadata.{required_token_key} is required')

            row = Instance(
                instance_key=payload.instance_key.strip(),
                platform_type_id=platform.id,
                is_enabled=bool(payload.is_enabled),
                platform_metadata_encrypted=encryptor.encrypt_json(platform_metadata),
                chatwoot_config_encrypted=encryptor.encrypt_json(chatwoot),
                proxy_config_encrypted=encryptor.encrypt_json(proxy),
            )
            repo.add(row)

            feature_rows = self._upsert_feature_overrides(
                db,
                instance=row,
                platform=platform,
                requested_overrides=payload.feature_overrides,
            )
            self._runtime_repo(db).get_or_create(row.id)

            db.commit()
            db.refresh(row)
            _invalidate_instance_cache(payload.instance_key)
            _invalidate_feature_defs_cache()
            return self._to_response(row, platform, platform_metadata, chatwoot, proxy, feature_rows)
        except ValueError:
            raise
        except Exception:
            logger.exception('create_instance failed instance_key=%s', payload.instance_key)
            raise

    def list_instances(self, db: Session) -> list[InstanceResponse]:
        """List instances."""
        try:
            rows = self._instance_repo(db).list_all()
            responses: list[InstanceResponse] = []
            for row in rows:
                runtime = self._to_runtime(db, row)
                responses.append(
                    self._to_response(
                        runtime.instance,
                        runtime.platform_type,
                        runtime.platform_metadata,
                        runtime.chatwoot,
                        runtime.proxy,
                        runtime.feature_overrides,
                    )
                )
            return responses
        except Exception:
            logger.exception('list_instances failed')
            raise

    def get_instance(self, db: Session, instance_key: str) -> Optional[InstanceResponse]:
        """Get instance."""
        try:
            row = self._instance_repo(db).get_by_key(instance_key)
            if not row:
                return None
            runtime = self._to_runtime(db, row)
            return self._to_response(
                runtime.instance,
                runtime.platform_type,
                runtime.platform_metadata,
                runtime.chatwoot,
                runtime.proxy,
                runtime.feature_overrides,
            )
        except Exception:
            logger.exception('get_instance failed instance_key=%s', instance_key)
            raise

    def update_instance(self, db: Session, instance_key: str, payload: InstancePatchRequest) -> Optional[InstanceResponse]:
        """Update instance."""
        try:
            row = self._instance_repo(db).get_by_key(instance_key)
            if not row:
                return None

            platform = row.platform_type
            if payload.platform_type_key:
                platform = self._require_platform(db, payload.platform_type_key)
                row.platform_type_id = platform.id

            platform_metadata = encryptor.decrypt_json(row.platform_metadata_encrypted)
            chatwoot = encryptor.decrypt_json(row.chatwoot_config_encrypted)
            proxy = self._normalize_proxy_config(encryptor.decrypt_json(row.proxy_config_encrypted))

            if payload.is_enabled is not None:
                row.is_enabled = bool(payload.is_enabled)

            if payload.platform_metadata is not None or payload.platform_type_key is not None:
                merged_platform = dict(platform_metadata)
                if payload.platform_metadata is not None:
                    merged_platform.update(
                        {
                            key: val
                            for key, val in payload.platform_metadata.items()
                            if val is not None and not (isinstance(val, str) and not val.strip())
                        }
                    )
                platform_metadata = self._normalize_platform_metadata(platform.key, merged_platform)
                required_token_key = self._required_platform_token_key(platform.key)
                if required_token_key and not platform_metadata.get(required_token_key):
                    raise ValueError(f'platform_metadata.{required_token_key} is required')
                row.platform_metadata_encrypted = encryptor.encrypt_json(platform_metadata)

            if payload.chatwoot is not None:
                merged_chatwoot = dict(chatwoot)
                merged_chatwoot.update(
                    {
                        key: val
                        for key, val in payload.chatwoot.items()
                        if val is not None and not (isinstance(val, str) and not val.strip())
                    }
                )
                chatwoot = self._normalize_chatwoot_config(merged_chatwoot)
                row.chatwoot_config_encrypted = encryptor.encrypt_json(chatwoot)

            if payload.proxy is not None:
                merged_proxy = dict(proxy)
                merged_proxy.update(payload.proxy.model_dump(exclude_none=True))
                proxy = self._normalize_proxy_config(merged_proxy, validate_dependencies=True)
                row.proxy_config_encrypted = encryptor.encrypt_json(proxy)

            feature_rows = self._upsert_feature_overrides(
                db,
                instance=row,
                platform=platform,
                requested_overrides=payload.feature_overrides,
            )

            self._runtime_repo(db).get_or_create(row.id)
            db.commit()
            db.refresh(row)
            _invalidate_instance_cache(instance_key)
            _invalidate_feature_defs_cache()

            platform = row.platform_type
            return self._to_response(row, platform, platform_metadata, chatwoot, proxy, feature_rows)
        except ValueError:
            raise
        except Exception:
            logger.exception('update_instance failed instance_key=%s', instance_key)
            raise

    def delete_instance(self, db: Session, instance_key: str) -> bool:
        """Delete instance."""
        try:
            row = self._instance_repo(db).get_by_key(instance_key)
            if not row:
                return False
            asset_dir = repo_root / 'data' / 'enterprise_assets' / str(row.instance_key).strip()
            self._instance_repo(db).delete(row)
            db.commit()
            _invalidate_instance_cache(instance_key)
            if asset_dir.exists():
                shutil.rmtree(asset_dir, ignore_errors=True)
            return True
        except Exception:
            logger.exception('delete_instance failed instance_key=%s', instance_key)
            raise

    def get_runtime_instance(self, db: Session, instance_key: str) -> Optional[RuntimeInstance]:
        """Get runtime instance.

        NOTE: We intentionally do NOT cache the result here. Caching
        RuntimeInstance objects causes DetachedInstanceError because they
        contain live SQLAlchemy ORM instances that cannot safely be shared
        across sessions or async tasks.
        """
        try:
            row = self._instance_repo(db).get_by_key(instance_key)
            if not row:
                return None
            return self._to_runtime(db, row)
        except Exception:
            logger.exception('get_runtime_instance failed instance_key=%s', instance_key)
            raise

    def list_runtime_enabled_instances(self, db: Session) -> list[RuntimeInstance]:
        """List runtime enabled instances."""
        try:
            out: list[RuntimeInstance] = []
            for row in self._instance_repo(db).list_all():
                if not row.is_enabled:
                    continue
                runtime = self._to_runtime(db, row)
                required_token_key = self._required_platform_token_key(runtime.platform_type.key)
                if required_token_key and not runtime.platform_metadata.get(required_token_key):
                    continue
                out.append(runtime)
            return out
        except Exception:
            logger.exception('list_runtime_enabled_instances failed')
            raise

    def update_runtime_state(
        self,
        db: Session,
        instance_id: str,
        *,
        last_platform_update_id: Optional[str] = None,
        last_error: Optional[str] = None,
        touch_sync: bool = True,
        last_enterprise_sms_sync_at: Optional[_dt.datetime] = None,
    ) -> None:
        """Update runtime state."""
        import datetime as _dt

        try:
            row = self._runtime_repo(db).get_or_create(instance_id)
            if last_platform_update_id is not None:
                row.last_platform_update_id = str(last_platform_update_id)
            if last_error is not None:
                row.last_error = last_error
            if touch_sync:
                row.last_sync_at = _dt.datetime.utcnow()
            if last_enterprise_sms_sync_at is not None:
                row.last_enterprise_sms_sync_at = last_enterprise_sms_sync_at
            self._runtime_repo(db).save(row)
            db.commit()
        except OperationalError as exc:
            try:
                db.rollback()
            except Exception:
                logger.exception('update_runtime_state rollback failed instance_id=%s', instance_id)
            if 'database is locked' in str(exc).lower():
                logger.warning('update_runtime_state sqlite locked instance_id=%s', instance_id)
            else:
                logger.exception('update_runtime_state failed instance_id=%s', instance_id)
            raise
        except Exception:
            try:
                db.rollback()
            except Exception:
                logger.exception('update_runtime_state rollback failed instance_id=%s', instance_id)
            logger.exception('update_runtime_state failed instance_id=%s', instance_id)
            raise

    def _to_runtime(self, db: Session, row: Instance) -> RuntimeInstance:
        """Internal helper to to runtime."""
        try:
            platform = row.platform_type
            platform_metadata = self._normalize_platform_metadata(
                platform.key,
                encryptor.decrypt_json(row.platform_metadata_encrypted),
            )
            chatwoot = self._normalize_chatwoot_config(encryptor.decrypt_json(row.chatwoot_config_encrypted))
            proxy = self._normalize_proxy_config(encryptor.decrypt_json(row.proxy_config_encrypted))

            feature_rows = self._upsert_feature_overrides(db, row, platform, requested_overrides=None)
            db.flush()

            feature_flags = {item.feature_key: bool(item.effective_enabled) for item in feature_rows}

            runtime_state = self._runtime_repo(db).get(row.id)
            return RuntimeInstance(
                instance=row,
                platform_type=platform,
                platform_metadata=platform_metadata,
                chatwoot=chatwoot,
                proxy=proxy,
                feature_flags=feature_flags,
                feature_overrides=feature_rows,
                runtime_state_last_update_id=runtime_state.last_platform_update_id if runtime_state else None,
                runtime_state_last_sms_sync_at=runtime_state.last_enterprise_sms_sync_at if runtime_state else None,
            )
        except Exception:
            logger.exception('instance runtime resolution failed instance_id=%s', getattr(row, 'id', None))
            raise

    def _require_platform(self, db: Session, platform_type_key: str) -> PlatformType:
        """Internal helper to require platform."""
        key = (platform_type_key or '').strip().lower()
        row = self._platform_repo(db).get_by_key(key)
        if not row:
            raise ValueError(f"Unknown platform_type_key '{platform_type_key}'")
        if not row.is_active:
            raise ValueError(f"Platform '{platform_type_key}' is not active")
        return row

    def _get_feature_definitions(self, db: Session):
        """Return cached feature definitions (seed data that rarely changes)."""
        global _feature_defs_cache
        if _feature_defs_cache is not None:
            return _feature_defs_cache
        feature_defs = self._feature_repo(db).list_all()
        _feature_defs_cache = [
            _CachedFeatureDef(
                key=f.key,
                default_enabled=f.default_enabled,
                required_platform_capability=f.required_platform_capability,
                required_chatwoot_capability=f.required_chatwoot_capability,
            )
            for f in feature_defs
        ]
        return _feature_defs_cache

    def _upsert_feature_overrides(
        self,
        db: Session,
        instance: Instance,
        platform: PlatformType,
        requested_overrides: Optional[dict[str, bool]],
    ) -> list[FeatureOverrideResponse]:
        """Internal helper to upsert feature overrides."""
        feature_defs = self._get_feature_definitions(db)
        valid_keys = {item.key for item in feature_defs}

        if requested_overrides is not None:
            for key in requested_overrides.keys():
                if key not in valid_keys:
                    raise ValueError(f"Unknown feature override '{key}'")

        existing = {row.feature_key: row for row in (instance.feature_overrides or [])}

        result: list[FeatureOverrideResponse] = []
        platform_caps = platform.capabilities_json or {}
        for feature in feature_defs:
            row = existing.get(feature.key)
            if not row and requested_overrides is None:
                requested_enabled = bool(feature.default_enabled)
                effective_enabled, reason = self._compute_effective(
                    feature_key=feature.key,
                    requested_enabled=requested_enabled,
                    required_platform_capability=feature.required_platform_capability,
                    required_chatwoot_capability=feature.required_chatwoot_capability,
                    platform_capabilities=platform_caps,
                )
                result.append(
                    FeatureOverrideResponse(
                        feature_key=feature.key,
                        requested_enabled=requested_enabled,
                        effective_enabled=effective_enabled,
                        disabled_reason=reason,
                    )
                )
                continue

            if not row:
                row = InstanceFeatureOverride(instance_id=instance.id, feature_key=feature.key)
                db.add(row)

            if requested_overrides is not None and feature.key in requested_overrides:
                requested_enabled = bool(requested_overrides[feature.key])
            elif row is not None:
                requested_enabled = bool(row.requested_enabled)
            else:
                requested_enabled = bool(feature.default_enabled)

            row.requested_enabled = requested_enabled
            effective_enabled, reason = self._compute_effective(
                feature_key=feature.key,
                requested_enabled=requested_enabled,
                required_platform_capability=feature.required_platform_capability,
                required_chatwoot_capability=feature.required_chatwoot_capability,
                platform_capabilities=platform_caps,
            )
            row.effective_enabled = effective_enabled
            row.disabled_reason = reason
            db.flush()

            result.append(
                FeatureOverrideResponse(
                    feature_key=feature.key,
                    requested_enabled=bool(row.requested_enabled),
                    effective_enabled=bool(row.effective_enabled),
                    disabled_reason=row.disabled_reason,
                )
            )

        result.sort(key=lambda x: x.feature_key)
        return result

    def _compute_effective(
        self,
        *,
        feature_key: str,
        requested_enabled: bool,
        required_platform_capability: Optional[str],
        required_chatwoot_capability: Optional[str],
        platform_capabilities: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Internal helper to compute effective."""
        if not requested_enabled:
            return False, 'disabled_by_user'

        if required_platform_capability and not bool(platform_capabilities.get(required_platform_capability)):
            return False, f'platform_missing_capability:{required_platform_capability}'

        if required_chatwoot_capability and not bool(CHATWOOT_CAPABILITIES.get(required_chatwoot_capability)):
            return False, f'chatwoot_missing_capability:{required_chatwoot_capability}'

        if feature_key == 'payload_debug_store' and not bool(settings.STORE_MESSAGE_PAYLOADS):
            return False, 'env_gate:STORE_MESSAGE_PAYLOADS=false'

        return True, None

    def _normalize_platform_metadata(self, platform_key: str, value: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Internal helper to normalize platform metadata."""
        key = str(platform_key or '').strip().lower()
        data = dict(value or {})
        if key == 'telegram':
            return {
                'telegram_token': str(data.get('telegram_token') or '').strip(),
                'telegram_api_base_url': str(data.get('telegram_api_base_url') or settings.TELEGRAM_API_BASE_URL).strip(),
                'telegram_file_base_url': str(data.get('telegram_file_base_url') or settings.TELEGRAM_FILE_BASE_URL).strip(),
                'telegram_poll_interval': int(data.get('telegram_poll_interval') or settings.TELEGRAM_POLL_INTERVAL_SECONDS),
                'telegram_bot_name': str(data.get('telegram_bot_name') or '').strip() or None,
                'telegram_bot_id': str(data.get('telegram_bot_id') or '').strip() or None,
                'telegram_department': str(data.get('telegram_department') or '').strip() or None,
                'telegram_share_phone_prompt_enabled': self._coerce_bool(
                    data.get('telegram_share_phone_prompt_enabled'),
                    default=settings.TELEGRAM_SHARE_PHONE_BUTTON,
                ),
                'telegram_share_phone_prompt_only_if_missing_phone': self._coerce_bool(
                    data.get('telegram_share_phone_prompt_only_if_missing_phone'),
                    default=True,
                ),
                'telegram_share_phone_prompt_text': str(
                    data.get('telegram_share_phone_prompt_text') or settings.TELEGRAM_SHARE_PHONE_PROMPT_TEXT
                ).strip(),
            }

        if key == 'telegram_enterprise':
            routes = data.get('enterprise_routes')
            if isinstance(routes, list):
                normalized_routes = []
                for route in routes:
                    if not isinstance(route, dict):
                        continue
                    normalized_routes.append({
                        'route_key': str(route.get('route_key') or '').strip(),
                        'display_name': str(route.get('display_name') or '').strip() or None,
                        'inbox_id': self._coerce_int(route.get('inbox_id')),
                        'inbox_name': str(route.get('inbox_name') or '').strip() or None,
                        'auto_create': self._coerce_bool(route.get('auto_create'), default=False),
                        'waiting_text': str(route.get('waiting_text') or '').strip() or None,
                        'accepted_text': str(route.get('accepted_text') or '').strip() or None,
                        'unread_text': str(route.get('unread_text') or '').strip() or None,
                    })
                routes = normalized_routes
            else:
                routes = []
            return {
                'telegram_token': str(data.get('telegram_token') or '').strip(),
                'telegram_api_base_url': str(data.get('telegram_api_base_url') or settings.TELEGRAM_API_BASE_URL).strip(),
                'telegram_file_base_url': str(data.get('telegram_file_base_url') or settings.TELEGRAM_FILE_BASE_URL).strip(),
                'telegram_poll_interval': int(data.get('telegram_poll_interval') or settings.TELEGRAM_POLL_INTERVAL_SECONDS),
                'telegram_bot_name': str(data.get('telegram_bot_name') or '').strip() or None,
                'telegram_bot_id': str(data.get('telegram_bot_id') or '').strip() or None,
                'telegram_department': str(data.get('telegram_department') or '').strip() or None,
                'enterprise_welcome_text': str(data.get('enterprise_welcome_text') or '').strip() or None,
                'enterprise_menu_prompt_text': str(data.get('enterprise_menu_prompt_text') or '').strip() or None,
                'enterprise_address_prompt_text': str(data.get('enterprise_address_prompt_text') or '').strip() or None,
                'enterprise_not_configured_text': str(data.get('enterprise_not_configured_text') or '').strip() or None,
                'enterprise_live_mode_resume_text': str(data.get('enterprise_live_mode_resume_text') or '').strip() or None,
                'enterprise_live_session_locked_text': str(data.get('enterprise_live_session_locked_text') or '').strip() or None,
                'enterprise_no_manuals_text': str(data.get('enterprise_no_manuals_text') or '').strip() or None,
                'enterprise_no_catalog_text': str(data.get('enterprise_no_catalog_text') or '').strip() or None,
                'enterprise_address_tehran_alborz_text': str(data.get('enterprise_address_tehran_alborz_text') or '').strip() or None,
                'enterprise_address_other_provinces_text': str(data.get('enterprise_address_other_provinces_text') or '').strip() or None,
                'enterprise_user_manual_link_template': str(data.get('enterprise_user_manual_link_template') or '').strip() or None,
                'enterprise_catalog_button_label': str(data.get('enterprise_catalog_button_label') or '').strip() or None,
                'enterprise_manuals_button_label': str(data.get('enterprise_manuals_button_label') or '').strip() or None,
                'enterprise_address_button_label': str(data.get('enterprise_address_button_label') or '').strip() or None,
                'enterprise_back_button_label': str(data.get('enterprise_back_button_label') or '').strip() or None,
                'enterprise_routes': routes,
            }

        if key == 'bale_pv_enterprise':
            return {
                'bale_pv_phone_number': str(data.get('bale_pv_phone_number') or '').strip(),
                'bale_pv_session_dir': str(data.get('bale_pv_session_dir') or '').strip() or None,
                'bale_pv_poll_interval': int(data.get('bale_pv_poll_interval') or settings.BALE_POLL_INTERVAL_SECONDS),
                'bale_pv_display_name': str(data.get('bale_pv_display_name') or '').strip() or None,
                'bale_pv_department': str(data.get('bale_pv_department') or '').strip() or None,
                'bale_pv_share_phone_prompt_enabled': self._coerce_bool(
                    data.get('bale_pv_share_phone_prompt_enabled'),
                    default=settings.BALE_SHARE_PHONE_BUTTON,
                ),
                'bale_pv_share_phone_prompt_only_if_missing_phone': self._coerce_bool(
                    data.get('bale_pv_share_phone_prompt_only_if_missing_phone'),
                    default=True,
                ),
                'bale_pv_share_phone_prompt_text': str(
                    data.get('bale_pv_share_phone_prompt_text') or settings.BALE_SHARE_PHONE_PROMPT_TEXT
                ).strip(),
            }

        if key == 'bale_enterprise':
            return {
                'bale_token': str(data.get('bale_token') or '').strip(),
                'bale_api_base_url': str(data.get('bale_api_base_url') or settings.BALE_API_BASE_URL).strip(),
                'bale_file_base_url': str(data.get('bale_file_base_url') or settings.BALE_FILE_BASE_URL).strip(),
                'bale_poll_interval': int(data.get('bale_poll_interval') or settings.BALE_POLL_INTERVAL_SECONDS),
                'bale_bot_name': str(data.get('bale_bot_name') or '').strip() or None,
                'bale_bot_id': str(data.get('bale_bot_id') or '').strip() or None,
                'bale_department': str(data.get('bale_department') or '').strip() or None,
                'enterprise_welcome_text': str(data.get('enterprise_welcome_text') or '').strip() or None,
                'enterprise_phone_prompt_text': str(data.get('enterprise_phone_prompt_text') or '').strip() or None,
                'enterprise_menu_prompt_text': str(data.get('enterprise_menu_prompt_text') or '').strip() or None,
                'enterprise_address_prompt_text': str(data.get('enterprise_address_prompt_text') or '').strip() or None,
                'enterprise_number_not_found_text': str(data.get('enterprise_number_not_found_text') or '').strip() or None,
                'enterprise_no_manuals_text': str(data.get('enterprise_no_manuals_text') or '').strip() or None,
                'enterprise_no_catalog_text': str(data.get('enterprise_no_catalog_text') or '').strip() or None,
                'enterprise_not_configured_text': str(data.get('enterprise_not_configured_text') or '').strip() or None,
                'enterprise_live_mode_resume_text': str(data.get('enterprise_live_mode_resume_text') or '').strip() or None,
                'enterprise_invalid_phone_text': str(data.get('enterprise_invalid_phone_text') or '').strip() or None,
                'enterprise_address_tehran_alborz_text': str(data.get('enterprise_address_tehran_alborz_text') or '').strip() or None,
                'enterprise_address_other_provinces_text': str(data.get('enterprise_address_other_provinces_text') or '').strip() or None,
                'enterprise_customer_service_inbox_id': self._coerce_int(data.get('enterprise_customer_service_inbox_id')),
                'enterprise_customer_service_inbox_name': str(data.get('enterprise_customer_service_inbox_name') or '').strip() or None,
                'enterprise_customer_service_auto_create': self._coerce_bool(
                    data.get('enterprise_customer_service_auto_create'),
                    default=False,
                ),
                'enterprise_customer_service_waiting_text': str(data.get('enterprise_customer_service_waiting_text') or '').strip() or None,
                'enterprise_customer_service_accepted_text': str(data.get('enterprise_customer_service_accepted_text') or '').strip() or None,
                'enterprise_customer_service_unread_text': str(data.get('enterprise_customer_service_unread_text') or '').strip() or None,
                'enterprise_sales_inbox_id': self._coerce_int(data.get('enterprise_sales_inbox_id')),
                'enterprise_sales_inbox_name': str(data.get('enterprise_sales_inbox_name') or '').strip() or None,
                'enterprise_sales_auto_create': self._coerce_bool(
                    data.get('enterprise_sales_auto_create'),
                    default=False,
                ),
                'enterprise_sales_waiting_text': str(data.get('enterprise_sales_waiting_text') or '').strip() or None,
                'enterprise_sales_accepted_text': str(data.get('enterprise_sales_accepted_text') or '').strip() or None,
                'enterprise_sales_unread_text': str(data.get('enterprise_sales_unread_text') or '').strip() or None,
                'enterprise_sms_sync_enabled': self._coerce_bool(
                    data.get('enterprise_sms_sync_enabled'),
                    default=settings.ENTERPRISE_SMS_SYNC_ENABLED,
                ),
                'enterprise_sms_api_url': str(
                    data.get('enterprise_sms_api_url') or settings.ENTERPRISE_SMS_API_URL
                ).strip(),
                'enterprise_sms_api_token': str(
                    data.get('enterprise_sms_api_token') or settings.ENTERPRISE_SMS_API_TOKEN
                ).strip(),
                'enterprise_sms_token_header': str(
                    data.get('enterprise_sms_token_header') or settings.ENTERPRISE_SMS_TOKEN_HEADER
                ).strip() or 'Authorization',
                'enterprise_sms_token_prefix': str(
                    data.get('enterprise_sms_token_prefix') or settings.ENTERPRISE_SMS_TOKEN_PREFIX
                ).strip(),
                'enterprise_sms_poll_interval_minutes': max(
                    1,
                    int(
                        self._coerce_int(data.get('enterprise_sms_poll_interval_minutes'))
                        or settings.ENTERPRISE_SMS_POLL_INTERVAL_MINUTES
                    ),
                ),
                'enterprise_sms_last_id': max(
                    0,
                    int(
                        self._coerce_int(data.get('enterprise_sms_last_id'))
                        if data.get('enterprise_sms_last_id') is not None
                        else settings.ENTERPRISE_SMS_INITIAL_LAST_ID
                    ),
                ),
                'enterprise_sms_http_timeout_seconds': max(
                    5,
                    int(
                        self._coerce_int(data.get('enterprise_sms_http_timeout_seconds'))
                        or settings.ENTERPRISE_SMS_HTTP_TIMEOUT_SECONDS
                    ),
                ),
            }

        return {
            'bale_token': str(data.get('bale_token') or '').strip(),
            'bale_api_base_url': str(data.get('bale_api_base_url') or settings.BALE_API_BASE_URL).strip(),
            'bale_file_base_url': str(data.get('bale_file_base_url') or settings.BALE_FILE_BASE_URL).strip(),
            'bale_poll_interval': int(data.get('bale_poll_interval') or settings.BALE_POLL_INTERVAL_SECONDS),
            'bale_bot_name': str(data.get('bale_bot_name') or '').strip() or None,
            'bale_bot_id': str(data.get('bale_bot_id') or '').strip() or None,
            'bale_department': str(data.get('bale_department') or '').strip() or None,
            'bale_share_phone_prompt_enabled': self._coerce_bool(
                data.get('bale_share_phone_prompt_enabled'),
                default=settings.BALE_SHARE_PHONE_BUTTON,
            ),
            'bale_share_phone_prompt_only_if_missing_phone': self._coerce_bool(
                data.get('bale_share_phone_prompt_only_if_missing_phone'),
                default=True,
            ),
            'bale_share_phone_prompt_text': str(
                data.get('bale_share_phone_prompt_text') or settings.BALE_SHARE_PHONE_PROMPT_TEXT
            ).strip(),
        }

    @staticmethod
    def _required_platform_token_key(platform_key: str) -> Optional[str]:
        """Internal helper to required platform token key."""
        return PLATFORM_REQUIRED_TOKEN_KEY.get(str(platform_key or '').strip().lower())

    def _normalize_chatwoot_config(self, value: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Internal helper to normalize chatwoot config."""
        data = dict(value or {})
        account_id = data.get('account_id')
        inbox_id = data.get('inbox_id')
        reopen_conversation = data.get('reopen_conversation')
        if reopen_conversation is None:
            reopen_conversation = data.get('reopenConversation')

        return {
            'base_url': str(data.get('base_url') or settings.CHATWOOT_BASE_URL).strip(),
            'api_access_token': str(data.get('api_access_token') or settings.CHATWOOT_API_TOKEN).strip(),
            'account_id': int(account_id) if account_id is not None and str(account_id).strip() else None,
            'inbox_id': int(inbox_id) if inbox_id is not None and str(inbox_id).strip() else None,
            'auto_create': bool(data.get('auto_create', False)),
            'reopen_conversation': self._coerce_bool(reopen_conversation, default=False),
            'inbox_name': (str(data.get('inbox_name') or '').strip() or None),
        }

    def _normalize_proxy_config(
        self,
        value: Optional[dict[str, Any]],
        *,
        validate_dependencies: bool = False,
    ) -> dict[str, Any]:
        """Internal helper to normalize proxy config."""
        data = dict(value or {})
        enabled = self._coerce_bool(data.get('enabled'), default=False)

        protocol_raw = str(data.get('protocol') or '').strip().lower()
        protocol = protocol_raw if protocol_raw else None
        host = str(data.get('host') or '').strip() or None
        port_raw = data.get('port')
        port: Optional[int]
        if port_raw is None or str(port_raw).strip() == '':
            port = None
        else:
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                raise ValueError('proxy.port must be an integer between 1 and 65535')
            if port < 1 or port > 65535:
                raise ValueError('proxy.port must be an integer between 1 and 65535')

        username = str(data.get('username') or '').strip() or None
        password = str(data.get('password') or '').strip() or None

        if not enabled:
            return {
                'enabled': False,
                'protocol': None,
                'host': None,
                'port': None,
                'username': None,
                'password': None,
            }

        if protocol not in {'http', 'https', 'socks5'}:
            raise ValueError('proxy.protocol must be one of: http, https, socks5')
        if not host:
            raise ValueError('proxy.host is required when proxy.enabled is true')
        if port is None:
            raise ValueError('proxy.port is required when proxy.enabled is true')
        if protocol == 'socks5' and validate_dependencies:
            if importlib.util.find_spec('socksio') is None:
                raise ValueError("SOCKS5 proxy requires optional dependency 'socksio' (install httpx[socks])")

        return {
            'enabled': True,
            'protocol': protocol,
            'host': host,
            'port': port,
            'username': username,
            'password': password,
        }

    def _mask_json(self, value: dict[str, Any]) -> dict[str, Any]:
        """Internal helper to mask json."""
        masked: dict[str, Any] = {}
        for key, raw in value.items():
            lower = str(key).lower()
            if any(token in lower for token in ('token', 'secret', 'password', 'key')) and raw:
                masked[key] = mask_secret(raw)
            else:
                masked[key] = raw
        return masked

    @staticmethod
    def _build_chatwoot_webhook_url(instance_key: str) -> str:
        """Internal helper to build the Chatwoot webhook URL for an instance."""
        return f"{settings.SERVER_BASE_URL.rstrip('/')}/api/v1/webhooks/chatwoot/{str(instance_key).strip()}"

    @staticmethod
    def _build_enterprise_chatwoot_webhook_url(instance_key: str, route_key: str) -> str:
        """Internal helper to build a route-specific enterprise Chatwoot webhook URL."""
        return (
            f"{settings.SERVER_BASE_URL.rstrip('/')}/api/v1/webhooks/chatwoot/"
            f"{str(instance_key).strip()}/enterprise/{str(route_key).strip()}"
        )

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        """Internal helper to coerce bool."""
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if not text:
            return bool(default)
        if text in {'1', 'true', 'yes', 'on', 'enabled'}:
            return True
        if text in {'0', 'false', 'no', 'off', 'disabled'}:
            return False
        return bool(default)

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        """Internal helper to coerce integer values."""
        if value is None or str(value).strip() == '':
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError('expected integer value')

    def _to_response(
        self,
        row: Instance,
        platform: PlatformType,
        platform_metadata: dict[str, Any],
        chatwoot: dict[str, Any],
        proxy: dict[str, Any],
        feature_rows: list[FeatureOverrideResponse],
    ) -> InstanceResponse:
        """Internal helper to to response."""
        chatwoot_response = self._mask_json(chatwoot)
        chatwoot_response['webhook_url'] = self._build_chatwoot_webhook_url(row.instance_key)
        platform_key = str(platform.key or '').strip().lower()
        if platform_key == 'bale_enterprise':
            chatwoot_response['enterprise_customer_service_webhook_url'] = self._build_enterprise_chatwoot_webhook_url(
                row.instance_key,
                'customer_service',
            )
            chatwoot_response['enterprise_sales_webhook_url'] = self._build_enterprise_chatwoot_webhook_url(
                row.instance_key,
                'sales',
            )
        if platform_key == 'telegram_enterprise':
            routes = platform_metadata.get('enterprise_routes') or []
            for route in routes:
                route_key = route.get('route_key')
                if route_key:
                    chatwoot_response[f'enterprise_{route_key}_webhook_url'] = self._build_enterprise_chatwoot_webhook_url(
                        row.instance_key,
                        route_key,
                    )

        return InstanceResponse(
            id=row.id,
            instance_key=row.instance_key,
            platform_type_key=platform.key,
            platform_display_name=platform.display_name,
            is_enabled=bool(row.is_enabled),
            platform_metadata=self._mask_json(platform_metadata),
            chatwoot=chatwoot_response,
            proxy=self._mask_json(proxy),
            feature_overrides=feature_rows,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
