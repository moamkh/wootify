"""Enterprise Bale service — bot-flow orchestration and Chatwoot routing.

This service handles the full lifecycle of messages arriving from the Bale
Enterprise bot connector and routes them to Chatwoot conversations.

Key responsibilities
--------------------
* Map inbound Bale bot updates to Chatwoot contacts and conversations.
* Drive a state-machine that guides users through phone-number verification,
  welcome messages, and agent-handoff flows.
* Download and convert inbound media (photos, documents, stickers) before
  attaching them to Chatwoot messages.  WEBP stickers are converted to JPEG
  via ``BalePvAdapter._convert_webp`` so Chatwoot can render them inline.
* Route outbound Chatwoot messages back to the correct Bale peer.
* Integrate with the Novin SMS gateway for OTP delivery.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.clients.chatwoot_client import ChatwootClient
from app.clients.novin_sms_client import NovinSmsClient
from app.config import settings
from app.connectors.registry import connector_registry
from app.models import (
    EnterpriseBaleSession,
    EnterpriseBaleUser,
    EnterpriseDocumentAssetType,
    EnterpriseGreStatus,
    EnterprisePendingMessage,
    EnterprisePendingMessageStatus,
    EnterpriseSessionStatus,
    EnterpriseUserState,
)
from app.repositories.enterprise_bale_session_repository import (
    EnterpriseBaleSessionRepository,
)
from app.repositories.enterprise_bale_user_repository import (
    EnterpriseBaleUserRepository,
)
from app.repositories.enterprise_document_asset_repository import (
    EnterpriseDocumentAssetRepository,
)
from app.repositories.enterprise_manual_group_repository import (
    EnterpriseManualGroupRepository,
)
from app.repositories.enterprise_pending_message_repository import (
    EnterprisePendingMessageRepository,
)
from app.services.enterprise_document_service import EnterpriseDocumentService
from app.services.enterprise_gre_service import EnterpriseGreValidator, GreValidationError
from app.services.instance_service import InstanceService
from app.utils.cache_utils import TTLCache
from app.utils.crypto_utils import encryptor
from app.utils.logging_utils import log_sms_to_file

logger = logging.getLogger("app.services.enterprise_bale")
sms_logger = logging.getLogger("app.services.enterprise_sms")


WELCOME_TEXT = "به بازوي دستيار شركت مهندسي پزشكي نوين خوش آمديد."
PHONE_PROMPT_TEXT = "لطفا شماره موبایل خود را از دکمه زیر به اشتراک‌ بگذارید."
MENU_PROMPT_TEXT = "لطفا گزینه مورد نظر خود را انتخاب کنید."
ADDRESS_PROMPT_TEXT = "لطفا استان مورد نظر خود را انتخاب کنید."
NUMBER_NOT_FOUND_TEXT = "شماره همراه شما در بانک اطلاعاتی نوین یافت نشد لطفا با شماره دیگری وارد شوید یا با شماره تلفن 021-41223 تماس حاصل کنید"
NO_MANUALS_TEXT = "فایلی برای این بخش تنظیم نشده است."
NO_CATALOG_TEXT = "کاتالوگی برای این بخش تنظیم نشده است."
NOT_CONFIGURED_TEXT = "این بخش هنوز در پنل مدیریت تنظیم نشده است."
LIVE_MODE_RESUME_TEXT = "گفتگو ادامه دارد. پیام خود را ارسال کنید."
LIVE_SESSION_LOCKED_TEXT = (
    "در گفتگوی زنده فقط می‌توانید پیام خود را ارسال کنید یا «بازگشت به منو» را بزنید."
)
INVALID_PHONE_TEXT = "شماره موبایل معتبر نیست لطفا از دکمه ی اشتراک گذاری شماره تلفین در منو ربات استفاده کنید."
BACK_TO_MENU_LABEL = "بازگشت به منو"
RECHECK_PHONE_LABEL = "بررسی مجدد شماره موبایل"
ADDRESS_TEHRAN_ALBORZ_LABEL = "تهران و البرز"
ADDRESS_OTHER_PROVINCES_LABEL = "مابقی استان ها"
USER_MANUAL_LABEL = "راهنمای کاربری محصولات"
USER_MANUAL_LABEL_LOCKED = "راهنمای کاربری محصولات🔒"
CUSTOMER_SERVICE_ADDRESSES_LABEL = "آدرس مراکز خدمات پس از فروش"
PRODUCTS_CATALOG_LABEL = "کاتالوگ محصولات"
CONTACT_CUSTOMER_SERVICE_LABEL = "ارتباط با کارشناسان خدمات پس از فروش"
CONTACT_SALES_LABEL = "ارتباط با کارشناسان فروش"
DESIERED_FILE_TEXT = "فایل مورد نظر👆"
USER_MANUAL_LINK_TEMPLATE = "برای دریافت راهنمای کاربری مورد نظر بر روی متن زیر ضربه بزنید:\n[{{user_manual_name}}]({{user_manual_url}})"
CATALOG_LINK_TEMPLATE = "برای دریافت کاتالوگ محصولات بر روی متن زیر ضربه بزنید:\n[{{catalog_name}}]({{catalog_url}})"
ROUTE_CUSTOMER_SERVICE = "customer_service"
ROUTE_SALES = "sales"

ROUTE_CONFIG = {
    ROUTE_CUSTOMER_SERVICE: {
        "label": CONTACT_CUSTOMER_SERVICE_LABEL,
        "state": EnterpriseUserState.live_customer_service,
        "inbox_id_key": "enterprise_customer_service_inbox_id",
        "inbox_name_key": "enterprise_customer_service_inbox_name",
        "auto_create_key": "enterprise_customer_service_auto_create",
        "waiting_text_key": "enterprise_customer_service_waiting_text",
        "accepted_text_key": "enterprise_customer_service_accepted_text",
        "unread_text_key": "enterprise_customer_service_unread_text",
    },
    ROUTE_SALES: {
        "label": CONTACT_SALES_LABEL,
        "state": EnterpriseUserState.live_sales,
        "inbox_id_key": "enterprise_sales_inbox_id",
        "inbox_name_key": "enterprise_sales_inbox_name",
        "auto_create_key": "enterprise_sales_auto_create",
        "waiting_text_key": "enterprise_sales_waiting_text",
        "accepted_text_key": "enterprise_sales_accepted_text",
        "unread_text_key": "enterprise_sales_unread_text",
    },
}

ELIGIBLE_ACTIONS = {
    "/user_manual": "user_manual",
    USER_MANUAL_LABEL: "user_manual",
    "/customer_service_addresses": "customer_service_addresses",
    CUSTOMER_SERVICE_ADDRESSES_LABEL: "customer_service_addresses",
    "/products_catalog": "products_catalog",
    "/products_catolag": "products_catalog",
    PRODUCTS_CATALOG_LABEL: "products_catalog",
    "/contact_customer_service": ROUTE_CUSTOMER_SERVICE,
    CONTACT_CUSTOMER_SERVICE_LABEL: ROUTE_CUSTOMER_SERVICE,
    "/contact_sales": ROUTE_SALES,
    CONTACT_SALES_LABEL: ROUTE_SALES,
}

INELIGIBLE_ACTIONS = {
    USER_MANUAL_LABEL_LOCKED: "user_manual_locked",
    "/contact_customer_service": ROUTE_CUSTOMER_SERVICE,
    CONTACT_CUSTOMER_SERVICE_LABEL: ROUTE_CUSTOMER_SERVICE,
    "/contact_sales": ROUTE_SALES,
    CONTACT_SALES_LABEL: ROUTE_SALES,
    "/customer_service_addresses": "customer_service_addresses",
    CUSTOMER_SERVICE_ADDRESSES_LABEL: "customer_service_addresses",
    "/products_catalog": "products_catalog",
    "/products_catolag": "products_catalog",
    PRODUCTS_CATALOG_LABEL: "products_catalog",
    "/recheck_phonenumber": "recheck_phone",
    RECHECK_PHONE_LABEL: "recheck_phone",
}

ADDRESS_ACTIONS = {
    ADDRESS_TEHRAN_ALBORZ_LABEL: "address_tehran_alborz",
    ADDRESS_OTHER_PROVINCES_LABEL: "address_other_provinces",
    BACK_TO_MENU_LABEL: "back_to_menu",
}


class EnterpriseBaleService:
    """Service for Bale Enterprise runtime workflows."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._instances = InstanceService()
        self._users = EnterpriseBaleUserRepository
        self._sessions = EnterpriseBaleSessionRepository
        self._pending = EnterprisePendingMessageRepository
        self._documents = EnterpriseDocumentService()
        self._gre = EnterpriseGreValidator()
        self._novin_sms = NovinSmsClient()
        self._clients: TTLCache[ChatwootClient] = TTLCache(maxsize=50, ttl=3600)
        self._menu_label_cache: TTLCache[str, set[str]] = TTLCache(maxsize=50, ttl=60)

    def get_sms_sync_config(
        self,
        db: Session,
        instance_key: str,
    ) -> dict[str, Any]:
        """Return enterprise SMS sync configuration for an instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        return self._sms_sync_config_payload(runtime.platform_metadata)

    def update_sms_sync_config(
        self,
        db: Session,
        instance_key: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Update enterprise SMS sync configuration for an instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        cfg = dict(runtime.platform_metadata or {})
        data = dict(patch or {})

        if "enabled" in data and data.get("enabled") is not None:
            cfg["enterprise_sms_sync_enabled"] = bool(data.get("enabled"))

        if "api_url" in data and data.get("api_url") is not None:
            api_url = str(data.get("api_url") or "").strip()
            if not api_url:
                raise ValueError("enterprise_sms_api_url is required")
            cfg["enterprise_sms_api_url"] = api_url

        if "api_token" in data and data.get("api_token") is not None:
            cfg["enterprise_sms_api_token"] = str(data.get("api_token") or "").strip()

        if "token_header" in data and data.get("token_header") is not None:
            token_header = str(data.get("token_header") or "").strip() or "Authorization"
            cfg["enterprise_sms_token_header"] = token_header

        if "token_prefix" in data and data.get("token_prefix") is not None:
            cfg["enterprise_sms_token_prefix"] = str(data.get("token_prefix") or "").strip()

        if "poll_interval_minutes" in data and data.get("poll_interval_minutes") is not None:
            poll_interval_minutes = int(data.get("poll_interval_minutes"))
            if poll_interval_minutes < 1:
                raise ValueError("poll_interval_minutes must be >= 1")
            cfg["enterprise_sms_poll_interval_minutes"] = poll_interval_minutes

        if "last_id" in data and data.get("last_id") is not None:
            last_id = int(data.get("last_id"))
            if last_id < 0:
                raise ValueError("last_id must be >= 0")
            cfg["enterprise_sms_last_id"] = last_id

        if "http_timeout_seconds" in data and data.get("http_timeout_seconds") is not None:
            http_timeout_seconds = int(data.get("http_timeout_seconds"))
            if http_timeout_seconds < 5:
                raise ValueError("http_timeout_seconds must be >= 5")
            cfg["enterprise_sms_http_timeout_seconds"] = http_timeout_seconds

        runtime.instance.platform_metadata_encrypted = encryptor.encrypt_json(cfg)
        db.add(runtime.instance)
        db.commit()
        return self._sms_sync_config_payload(cfg)

    @staticmethod
    def _sms_sync_config_payload(platform_metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Build a response payload for enterprise SMS sync configuration."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}

        raw_interval = cfg.get("enterprise_sms_poll_interval_minutes")
        try:
            poll_interval_minutes = int(raw_interval)
        except (TypeError, ValueError):
            poll_interval_minutes = int(settings.ENTERPRISE_SMS_POLL_INTERVAL_MINUTES)

        raw_last_id = cfg.get("enterprise_sms_last_id")
        try:
            last_id = int(raw_last_id)
        except (TypeError, ValueError):
            last_id = int(settings.ENTERPRISE_SMS_INITIAL_LAST_ID)

        raw_timeout = cfg.get("enterprise_sms_http_timeout_seconds")
        try:
            http_timeout_seconds = int(raw_timeout)
        except (TypeError, ValueError):
            http_timeout_seconds = int(settings.ENTERPRISE_SMS_HTTP_TIMEOUT_SECONDS)

        api_token = str(cfg.get("enterprise_sms_api_token") or settings.ENTERPRISE_SMS_API_TOKEN).strip()

        return {
            "enabled": EnterpriseBaleService.sms_sync_enabled(cfg),
            "api_url": str(cfg.get("enterprise_sms_api_url") or settings.ENTERPRISE_SMS_API_URL).strip(),
            "token_header": str(
                cfg.get("enterprise_sms_token_header") or settings.ENTERPRISE_SMS_TOKEN_HEADER or "Authorization"
            ).strip() or "Authorization",
            "token_prefix": str(cfg.get("enterprise_sms_token_prefix") or settings.ENTERPRISE_SMS_TOKEN_PREFIX).strip(),
            "poll_interval_minutes": max(1, poll_interval_minutes),
            "last_id": max(0, last_id),
            "http_timeout_seconds": max(5, http_timeout_seconds),
            "api_token_configured": bool(api_token),
        }

    @staticmethod
    def sms_sync_enabled(platform_metadata: Optional[dict[str, Any]]) -> bool:
        """Return whether enterprise SMS synchronization is enabled."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        raw = cfg.get("enterprise_sms_sync_enabled")
        if raw is None:
            return bool(settings.ENTERPRISE_SMS_SYNC_ENABLED)
        return bool(raw)

    @staticmethod
    def sms_sync_interval_seconds(platform_metadata: Optional[dict[str, Any]]) -> int:
        """Resolve the enterprise SMS synchronization interval in seconds."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        raw = cfg.get("enterprise_sms_poll_interval_minutes")
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = int(settings.ENTERPRISE_SMS_POLL_INTERVAL_MINUTES)
        return max(1, minutes) * 60

    async def sync_external_sms_messages(
        self,
        db: Session,
        instance_key: str,
    ) -> dict[str, Any]:
        """Fetch external SMS records and deliver matching messages to enterprise Bale users."""
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if not runtime:
            raise ValueError("instance not found")
        if str(runtime.platform_type.key or "").strip().lower() != "bale_enterprise":
            return {"message": "ignored", "detail": "instance_not_bale_enterprise"}

        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        last_id_raw = cfg.get("enterprise_sms_last_id")
        try:
            configured_last_id = int(last_id_raw)
        except (TypeError, ValueError):
            configured_last_id = int(settings.ENTERPRISE_SMS_INITIAL_LAST_ID)
        configured_last_id = max(0, configured_last_id)

        if not runtime.instance.is_enabled:
            return {
                "message": "ignored",
                "detail": "instance_disabled",
                "last_id": int(configured_last_id),
            }
        if not self.sms_sync_enabled(runtime.platform_metadata):
            return {
                "message": "ignored",
                "detail": "sms_sync_disabled",
                "last_id": int(configured_last_id),
            }

        api_url = str(
            cfg.get("enterprise_sms_api_url") or settings.ENTERPRISE_SMS_API_URL
        ).strip()
        token = str(
            cfg.get("enterprise_sms_api_token") or settings.ENTERPRISE_SMS_API_TOKEN
        ).strip()
        token_header = str(
            cfg.get("enterprise_sms_token_header")
            or settings.ENTERPRISE_SMS_TOKEN_HEADER
            or "Authorization"
        ).strip() or "Authorization"
        token_prefix = str(
            cfg.get("enterprise_sms_token_prefix")
            or settings.ENTERPRISE_SMS_TOKEN_PREFIX
        ).strip()
        timeout_raw = cfg.get("enterprise_sms_http_timeout_seconds")
        try:
            timeout_seconds = int(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = int(settings.ENTERPRISE_SMS_HTTP_TIMEOUT_SECONDS)
        timeout_seconds = max(5, timeout_seconds)

        last_id = int(configured_last_id)
        fetch_start_id = last_id + 1

        sms_logger.info(
            "sync.start instance=%s enabled=%s api_url=%s processed_last_id=%s fetch_start_id=%s timeout_seconds=%s",
            instance_key,
            True,
            api_url,
            last_id,
                fetch_start_id,
            timeout_seconds,
        )

        try:
            response = await self._novin_sms.fetch_since(
                url=api_url,
                    last_id=fetch_start_id,
                token=token,
                token_header=token_header,
                token_prefix=token_prefix,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            sms_logger.exception(
                    "sync.fetch_failed instance=%s api_url=%s processed_last_id=%s fetch_start_id=%s error=%s",
                instance_key,
                api_url,
                last_id,
                    fetch_start_id,
                str(exc),
            )
            return {
                "message": "failed",
                "detail": str(exc),
                "fetched": 0,
                "delivered": 0,
                "dropped": 0,
                "failed": 1,
                "last_id": int(last_id),
            }
        rows = response.get("data") if isinstance(response, dict) else []
        items = [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []

        delivered = 0
        dropped = 0
        failed = 0
        max_delivered_id = last_id

        for item in items:
            item_id = self._coerce_sms_id(item)
            if item_id is not None and item_id <= last_id:
                sms_logger.info(
                    "sync.item_skip instance=%s reason=already_processed sms_id=%s processed_last_id=%s",
                    instance_key,
                    item_id,
                    last_id,
                )
                continue

            text = str(item.get("messageText") or "").strip()
            if not text:
                sms_logger.debug(
                    "sync.item_drop instance=%s reason=empty_text sms_id=%s raw_to_phone=%s",
                    instance_key,
                    item_id,
                    item.get("toPhoneNumber"),
                )
                dropped += 1
                continue

            raw_to_phone = item.get("toPhoneNumber")
            normalized_phone = self._normalize_phone_number(raw_to_phone)
            sms_logger.debug(
                "sync.phone_compare instance=%s sms_id=%s raw_to_phone=%s normalized_to_phone=%s",
                instance_key,
                item_id,
                raw_to_phone,
                normalized_phone,
            )
            if not normalized_phone:
                sms_logger.debug(
                    "sync.item_drop instance=%s reason=invalid_to_phone sms_id=%s raw_to_phone=%s",
                    instance_key,
                    item_id,
                    raw_to_phone,
                )
                dropped += 1
                continue

            sms_logger.debug(
                "sync.user_lookup_start instance=%s sms_id=%s lookup_phone=%s",
                instance_key,
                item_id,
                normalized_phone,
            )
            users = self._users(db).list_by_phone_number(runtime.instance.id, normalized_phone)
            if not users:
                sms_logger.debug(
                    "sync.user_lookup_miss instance=%s sms_id=%s lookup_phone=%s",
                    instance_key,
                    item_id,
                    normalized_phone,
                )
                dropped += 1
                # Optional file logging
                if settings.ENTERPRISE_SMS_FILE_LOG_ENABLED:
                    log_sms_to_file(
                        phone_number=normalized_phone,
                        text=text,
                        status="dropped: user_not_found",
                        sms_id=item_id,
                    )
                continue

            sms_logger.debug(
                "sync.user_lookup_hit instance=%s sms_id=%s lookup_phone=%s matched_user_ids=%s",
                instance_key,
                item_id,
                normalized_phone,
                [str(user.id) for user in users],
            )

            for user in users:
                chat_id = str(user.platform_chat_id or "").strip()
                if not chat_id:
                    sms_logger.debug(
                        "sync.item_drop instance=%s reason=missing_chat_id sms_id=%s user_id=%s lookup_phone=%s",
                        instance_key,
                        item_id,
                        user.id,
                        normalized_phone,
                    )
                    dropped += 1
                    # Optional file logging
                    if settings.ENTERPRISE_SMS_FILE_LOG_ENABLED:
                        log_sms_to_file(
                            phone_number=normalized_phone,
                            text=text,
                            status="dropped: missing_chat_id",
                            sms_id=item_id,
                        )
                    continue
                try:
                    sms_logger.info(
                        "sync.item_send_start instance=%s sms_id=%s user_id=%s chat_id=%s text_len=%s lookup_phone=%s",
                        instance_key,
                        item_id,
                        user.id,
                        chat_id,
                        len(text or ""),
                        normalized_phone,
                    )
                    await self._send_text(instance_key, chat_id, text)
                    sms_logger.info(
                        "sync.delivery_sent instance=%s sms_id=%s user_id=%s chat_id=%s text_len=%s lookup_phone=%s",
                        instance_key,
                        item_id,
                        user.id,
                        chat_id,
                        len(text or ""),
                        normalized_phone,
                    )
                    delivered += 1
                    if item_id is not None:
                        max_delivered_id = max(max_delivered_id, item_id)
                    # Optional file logging
                    if settings.ENTERPRISE_SMS_FILE_LOG_ENABLED:
                        log_sms_to_file(
                            phone_number=normalized_phone,
                            text=text,
                            status="sent",
                            sms_id=item_id,
                        )
                except Exception as exc:
                    failed += 1
                    sms_logger.error(
                        "sync.delivery_failed instance=%s user_id=%s chat_id=%s sms_id=%s text_len=%s error_type=%s error=%s",
                        instance_key,
                        user.id,
                        chat_id,
                        item_id,
                        len(text or ""),
                        type(exc).__name__,
                        str(exc),
                        exc_info=True,
                    )
                    logger.exception(
                        "enterprise.sms_delivery_failed instance=%s user_id=%s chat_id=%s sms_id=%s",
                        instance_key,
                        user.id,
                        chat_id,
                        item_id,
                    )
                    # Optional file logging
                    if settings.ENTERPRISE_SMS_FILE_LOG_ENABLED:
                        log_sms_to_file(
                            phone_number=normalized_phone,
                            text=text,
                            status=f"failed: {type(exc).__name__}",
                            sms_id=item_id,
                        )

        sms_logger.info(
            "sync.done instance=%s fetched=%s delivered=%s dropped=%s failed=%s last_id=%s",
            instance_key,
            len(items),
            delivered,
            dropped,
            failed,
            int(max_delivered_id),
        )

        # Commit last_id only after delivery attempts so failures can be retried.
        if max_delivered_id != last_id:
            cfg["enterprise_sms_last_id"] = int(max_delivered_id)
            runtime.instance.platform_metadata_encrypted = encryptor.encrypt_json(cfg)
            db.add(runtime.instance)
            db.commit()
            sms_logger.info(
                "sync.last_id_updated instance=%s previous_last_id=%s new_last_id=%s fetched=%s",
                instance_key,
                last_id,
                max_delivered_id,
                len(items),
            )

        return {
            "message": "synced",
            "fetched": len(items),
            "delivered": delivered,
            "dropped": dropped,
            "failed": failed,
            "last_id": int(max_delivered_id),
        }

    @staticmethod
    def _coerce_sms_id(item: dict[str, Any]) -> Optional[int]:
        """Extract a numeric SMS id from known response id keys."""
        for key in ("id", "Id", "sms_id", "smsId", "SMSId"):
            raw = item.get(key)
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _max_sms_id(cls, items: list[dict[str, Any]], fallback: int) -> int:
        """Compute the highest numeric SMS id observed in the API response list."""
        highest = int(fallback)
        for item in items:
            item_id = cls._coerce_sms_id(item)
            if item_id is not None:
                highest = max(highest, item_id)
        return highest

    async def handle_platform_update(
        self, db: Session, instance_key: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle a single Bale Enterprise update from the polling loop."""

        runtime = self._require_runtime_instance(db, instance_key)
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
        )
        if not isinstance(message, dict):
            return {"message": "ignored", "detail": "unsupported_update"}

        chat_id = self._extract_chat_id(message)
        if chat_id is None:
            return {"message": "ignored", "detail": "chat_id_missing"}

        from_name = self._extract_from_name(message)
        user = self._get_or_create_user(
            db, runtime.instance.id, str(chat_id), display_name=from_name
        )
        text = str(message.get("text") or message.get("caption") or "").strip()
        contact_payload = self._extract_contact_payload(message)

        if not text:
            text = self._extract_contact_text(message) or ""
        attachments = await self._extract_attachments(instance_key, message)

        command = self._normalize_command(text)
        live_session = self._active_live_session_for_state(db, user)
        if live_session:
            self._mark_user_present(db, live_session)
            handled = await self._handle_live_session_menu_input(
                db,
                runtime=runtime,
                user=user,
                session=live_session,
                chat_id=str(chat_id),
                text=text,
            )
            if handled is not None:
                db.commit()
                return handled
            await self._forward_customer_message_to_chatwoot(
                db,
                runtime=runtime,
                user=user,
                session=live_session,
                text=text,
                attachments=attachments,
            )
            db.commit()
            return {"message": "forwarded_to_chatwoot", "status": "sent"}

        if command == "/start":
            self._leave_live_session_if_needed(db, user)
            await self._send_text(
                instance_key,
                str(chat_id),
                self._message_text(
                    runtime.platform_metadata, "enterprise_welcome_text", WELCOME_TEXT
                ),
            )

            await self._refresh_gre_and_show_root(db, runtime, user, str(chat_id))

            db.commit()
            return {"message": "start_handled", "status": "ok"}
        if user.current_state == EnterpriseUserState.awaiting_phone_input:
            if self._is_back_to_menu(text):
                await self._show_root_menu(
                    runtime.instance.instance_key,
                    user,
                    chat_id,
                    platform_metadata=runtime.platform_metadata,
                )
                self._set_user_state(db, user, EnterpriseUserState.ineligible_root)
                db.commit()
                return {"message": "exited_manual_menu_locked", "status": "ok"}
            phone_number = self._extract_phone_input(
                contact_payload=contact_payload, text=text
            )
            if not phone_number:
                await self._send_text(
                    instance_key,
                    str(chat_id),
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_invalid_phone_text",
                        INVALID_PHONE_TEXT,
                    ),
                )
                await self._send_phone_prompt(
                    instance_key,
                    str(chat_id),
                    platform_metadata=runtime.platform_metadata,
                )
                db.commit()
                return {"message": "invalid_phone", "detail": "phone_required"}
            await self._handle_phone_submission(
                db, runtime, user, str(chat_id), phone_number
            )
            db.commit()
            return {"message": "phone_saved", "status": "ok"}

        if user.current_state in {
            EnterpriseUserState.manual_menu,
            EnterpriseUserState.manual_group_menu,
        }:
            handled = await self._handle_manual_menu(
                db, runtime, user, str(chat_id), text
            )
            db.commit()
            return handled

        if user.current_state == EnterpriseUserState.address_menu:
            handled = await self._handle_address_menu(
                db, runtime, user, str(chat_id), text
            )
            db.commit()
            return handled

        if user.gre_status == EnterpriseGreStatus.eligible:
            handled = await self._handle_eligible_root(
                db, runtime, user, str(chat_id), text
            )
            db.commit()
            return handled

        handled = await self._handle_ineligible_root(
            db, runtime, user, str(chat_id), text
        )
        db.commit()
        return handled

    async def receive_chatwoot_webhook(
        self, db: Session, instance_key: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle Chatwoot webhook events for Bale Enterprise instances."""
        runtime = self._require_runtime_instance(db, instance_key)
        if not runtime.instance.is_enabled:
            return {"message": "ignored", "detail": "instance_disabled"}

        event_name = str(payload.get("event") or "").strip().lower()
        chatwoot_conversation_id = self._extract_chatwoot_conversation_id(payload)
        if not chatwoot_conversation_id:
            return {"message": "ignored", "detail": "chatwoot_conversation_id_missing"}

        session = self._sessions(db).get_by_chatwoot_conversation_id(
            runtime.instance.id, chatwoot_conversation_id
        )
        if not session:
            return {"message": "ignored", "detail": "session_not_found"}

        if self._is_chatwoot_status_event(payload, event_name):
            status_name = self._extract_chatwoot_status_name(payload)
            if status_name == "resolved":
                session.status = EnterpriseSessionStatus.resolved
                session.user_present = False
                self._sessions(db).save(session)
                db.commit()
                return {"message": "session_resolved", "status": "resolved"}
            return {"message": "ignored", "detail": "status_event_ignored"}

        if payload.get("private"):
            return {"message": "ignored", "detail": "private_message"}
        if not self._is_forwardable_chatwoot_message(payload, event_name):
            return {"message": "ignored", "detail": "message_type_not_outgoing"}
        if session.status == EnterpriseSessionStatus.resolved:
            return {"message": "ignored", "detail": "session_resolved"}

        user = session.user
        route_key = str(session.route_key or "").strip()
        route_cfg = ROUTE_CONFIG.get(route_key)
        if not route_cfg:
            return {"message": "ignored", "detail": "route_not_supported"}

        message_id = self._extract_chatwoot_message_id(payload)
        text = self._extract_chatwoot_message_text(payload)
        attachments = self._extract_chatwoot_attachments(payload)

        if not session.user_present:
            if message_id:
                existing = self._pending(db).get_by_chatwoot_message_id(
                    session.id, str(message_id)
                )
                if existing:
                    return {"message": "duplicate", "detail": "pending_message_exists"}

            pending_row = EnterprisePendingMessage(
                session_id=session.id,
                chatwoot_message_id=str(message_id) if message_id else None,
                text_payload=text or None,
                attachment_payload_json=attachments or None,
                status=EnterprisePendingMessageStatus.pending,
            )
            self._pending(db).save(pending_row)
            session.unread_count = int(session.unread_count or 0) + 1
            notify_text = str(
                runtime.platform_metadata.get(route_cfg["unread_text_key"]) or ""
            ).strip() or self._not_configured_text(runtime.platform_metadata)
            if not session.unread_notice_sent:
                try:
                    await self._send_text(
                        instance_key,
                        user.platform_chat_id,
                        notify_text,
                        reply_markup=self._root_menu_markup(user.gre_status),
                    )
                    session.unread_notice_sent = True
                except Exception:
                    logger.exception(
                        "failed to send enterprise unread notification instance=%s session_id=%s route=%s",
                        instance_key,
                        session.id,
                        route_key,
                    )
            self._sessions(db).save(session)
            db.commit()
            return {"message": "queued", "status": "pending"}

        await self._ensure_accepted_notice(
            instance_key=instance_key,
            runtime=runtime,
            session=session,
            route_key=route_key,
            chat_id=user.platform_chat_id,
        )
        await self._deliver_operator_payload(
            instance_key,
            runtime=runtime,
            chat_id=user.platform_chat_id,
            text=text,
            attachments=attachments,
            reply_markup=False,
        )
        self._sessions(db).save(session)
        db.commit()
        return {"message": "sent", "status": "sent"}

    async def create_route_inbox(
        self, db: Session, instance_key: str, route_key: str
    ) -> dict[str, Any]:
        """Create or discover a route-specific Chatwoot inbox for an enterprise instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        route_cfg = self._require_route(route_key)
        chatwoot = runtime.chatwoot
        account_id = chatwoot.get("account_id")
        inbox_name = str(
            runtime.platform_metadata.get(route_cfg["inbox_name_key"]) or ""
        ).strip()
        if not account_id or not inbox_name:
            raise ValueError(
                "chatwoot.account_id and the route inbox_name are required"
            )

        client = self._get_chatwoot_client(chatwoot)
        inboxes = await client.list_inboxes(int(account_id))
        payload = inboxes.get("payload") if isinstance(inboxes, dict) else None
        existing = None
        if isinstance(payload, list):
            existing = next(
                (
                    item
                    for item in payload
                    if str(item.get("name") or "").strip() == inbox_name
                ),
                None,
            )

        created = False
        webhook_updated = False
        inbox_obj = existing
        webhook_url = self._chatwoot_webhook_url(instance_key, route_key)
        if not existing:
            inbox_obj = await client.create_inbox(
                int(account_id),
                self._build_chatwoot_api_inbox_payload(inbox_name, webhook_url),
            )
            created = True
        else:
            inbox_obj, webhook_updated = await self._ensure_inbox_webhook_url(
                client,
                account_id=int(account_id),
                instance_key=instance_key,
                inbox_obj=existing,
                inbox_name=inbox_name,
                expected_webhook_url=webhook_url,
            )

        inbox_id = self._extract_id(inbox_obj) or self._extract_id(
            (inbox_obj or {}).get("payload")
        )
        if inbox_id:
            runtime.platform_metadata[route_cfg["inbox_id_key"]] = int(inbox_id)
            runtime.instance.platform_metadata_encrypted = encryptor.encrypt_json(
                runtime.platform_metadata
            )
            db.add(runtime.instance)
            db.commit()
        return {
            "created": created,
            "webhook_updated": webhook_updated,
            "webhook_url": webhook_url,
            "inbox_id": int(inbox_id) if inbox_id else None,
            "inbox": inbox_obj,
        }

    def list_sessions(
        self, db: Session, instance_key: str
    ) -> list[EnterpriseBaleSession]:
        """List enterprise sessions for an instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        return self._sessions(db).list_by_instance(runtime.instance.id)

    async def _handle_phone_submission(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        phone_number: str,
    ) -> None:
        """Persist a phone number, validate GRE, and show the correct root menu."""
        try:
            result = await self._gre.validate_phone(phone_number)
        except GreValidationError as exc:
            logger.warning("gre_validation_error instance=%s error=%s", runtime.instance.instance_key, exc)
            result = EnterpriseGreValidationResult(
                normalized_phone=self._gre._normalize_phone_number(phone_number),
                gre_status=EnterpriseGreStatus.unknown,
                message=str(exc),
            )

        # Phone format is invalid — ask again.
        if not result.normalized_phone:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_invalid_phone_text",
                    INVALID_PHONE_TEXT,
                ),
            )
            await self._send_phone_prompt(
                runtime.instance.instance_key,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return

        # GRE couldn't determine eligibility — treat as ineligible so the user
        # always reaches a root menu instead of being stuck in phone-entry.
        resolved_status = (
            EnterpriseGreStatus.ineligible
            if result.gre_status == EnterpriseGreStatus.unknown
            else result.gre_status
        )

        if resolved_status == EnterpriseGreStatus.ineligible:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_number_not_found_text",
                    NUMBER_NOT_FOUND_TEXT,
                ),
            )

        user.phone_number = result.normalized_phone
        user.gre_status = resolved_status
        self._users(db).save(user)
        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )
        self._set_user_state(db, user, user.current_state)


    async def _refresh_gre_and_show_root(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
    ) -> None:
        """Re-run GRE validation using the stored phone number and show the root menu."""
        try:
            result = await self._gre.validate_phone(str(user.phone_number or ""))
        except GreValidationError as exc:
            logger.warning("gre_validation_error instance=%s error=%s", runtime.instance.instance_key, exc)
            result = EnterpriseGreValidationResult(
                normalized_phone=user.phone_number,
                gre_status=EnterpriseGreStatus.unknown,
                message=str(exc),
            )
        user.phone_number = result.normalized_phone
        user.gre_status = result.gre_status
        self._users(db).save(user)
        if result.gre_status == EnterpriseGreStatus.unknown:
            self._set_user_state(db, user, EnterpriseUserState.awaiting_phone_input)
            await self._send_phone_prompt(
                runtime.instance.instance_key,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return
        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )

    async def _handle_eligible_root(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle eligible-root user actions."""
        action = ELIGIBLE_ACTIONS.get(self._normalize_action_text(text))
        if action == "user_manual":
            # Check if manual groups are configured
            groups = EnterpriseManualGroupRepository(db).list_by_instance(runtime.instance.id, active_only=True)
            if groups:
                # Show group menu first
                self._set_user_state(db, user, EnterpriseUserState.manual_group_menu)
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_menu_prompt_text",
                        MENU_PROMPT_TEXT,
                    ),
                    reply_markup=self._manual_group_menu_markup(groups),
                )
                return {"message": "manual_group_menu_opened", "status": "ok"}
            else:
                # Fallback: show manuals directly if no groups
                self._set_user_state(db, user, EnterpriseUserState.manual_menu)
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_menu_prompt_text",
                        MENU_PROMPT_TEXT,
                    ),
                    reply_markup=self._manual_menu_markup(db, runtime.instance.id),
                )
                return {"message": "manual_menu_opened", "status": "ok"}
        if action == "customer_service_addresses":
            self._set_user_state(db, user, EnterpriseUserState.address_menu)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_address_prompt_text",
                    ADDRESS_PROMPT_TEXT,
                ),
                reply_markup=self._address_menu_markup(),
            )
            return {"message": "address_menu_opened", "status": "ok"}
        if action == "products_catalog":
            await self._send_catalog_and_root(db, runtime, user, chat_id)
            return {"message": "catalog_sent", "status": "ok"}
        if action in {ROUTE_CUSTOMER_SERVICE, ROUTE_SALES}:
            await self._enter_live_route(db, runtime, user, chat_id, action)
            return {"message": "live_route_entered", "status": "ok"}

        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )
        return {"message": "root_menu_resent", "detail": "unknown_action"}

    async def _handle_ineligible_root(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle ineligible-root user actions."""
        action = INELIGIBLE_ACTIONS.get(self._normalize_action_text(text))
        if action == "user_manual_locked":
            self._set_user_state(db, user, EnterpriseUserState.awaiting_phone_input)
            await self._send_phone_prompt(
                runtime.instance.instance_key,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return {"message": "manual_menu_opened", "status": "ok"}

        if action == "customer_service_addresses":
            self._set_user_state(db, user, EnterpriseUserState.address_menu)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_address_prompt_text",
                    ADDRESS_PROMPT_TEXT,
                ),
                reply_markup=self._address_menu_markup(),
            )
            return {"message": "address_menu_opened", "status": "ok"}
        if action == "products_catalog":
            await self._send_catalog_and_root(db, runtime, user, chat_id)
            return {"message": "catalog_sent", "status": "ok"}
        if action in {ROUTE_CUSTOMER_SERVICE, ROUTE_SALES}:
            await self._enter_live_route(db, runtime, user, chat_id, action)
            return {"message": "live_route_entered", "status": "ok"}
        if action == "recheck_phone":
            self._set_user_state(db, user, EnterpriseUserState.awaiting_phone_input)
            await self._send_phone_prompt(
                runtime.instance.instance_key,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return {"message": "phone_recheck_requested", "status": "ok"}

        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )
        return {"message": "root_menu_resent", "detail": "unknown_action"}

    async def _handle_manual_menu(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle manual-menu and manual-group-menu selections."""
        if user.gre_status == EnterpriseGreStatus.ineligible:
            return {"message": "phone_recheck_requested", "status": "ok"}

        if self._is_back_to_menu(text):
            # Clear group selection when going back to root
            user.current_group_id = None
            db.add(user)
            db.flush()
            await self._show_root_menu(
                runtime.instance.instance_key,
                user,
                chat_id,
                platform_metadata=runtime.platform_metadata,
                # rebuild_keyboard=self._manual_menu_needs_root_rebuild(
                #     db, runtime.instance.id, user.gre_status
                # ),
            )
            return {"message": "manual_menu_closed", "status": "ok"}

        # Handle group selection (if in manual_group_menu state)
        if user.current_state == EnterpriseUserState.manual_group_menu:
            # Check if text matches a group name
            groups = EnterpriseManualGroupRepository(db).list_by_instance(runtime.instance.id, active_only=True)
            selected_group = next(
                (g for g in groups if str(g.name).strip() == str(text or "").strip()),
                None,
            )
            if selected_group:
                # Store group selection and transition to manual_menu
                user.current_group_id = selected_group.id
                self._set_user_state(db, user, EnterpriseUserState.manual_menu)
                # Show manuals in this group
                manuals = EnterpriseDocumentAssetRepository(db).list_by_group(selected_group.id, active_only=True)
                if not manuals:
                    await self._send_text(
                        runtime.instance.instance_key,
                        chat_id,
                        self._message_text(
                            runtime.platform_metadata,
                            "enterprise_no_manuals_text",
                            NO_MANUALS_TEXT,
                        ),
                        reply_markup=self._manual_menu_markup(db, runtime.instance.id),
                    )
                else:
                    # Build keyboard with manuals from this group
                    keyboard = [
                        [{"text": str(m.display_name or m.original_filename).strip()}]
                        for m in manuals
                    ]
                    keyboard.append([{"text": BACK_TO_MENU_LABEL}])
                    markup = {
                        "keyboard": keyboard,
                        "resize_keyboard": True,
                        "one_time_keyboard": True,
                    }
                    await self._send_text(
                        runtime.instance.instance_key,
                        chat_id,
                        self._message_text(
                            runtime.platform_metadata,
                            "enterprise_menu_prompt_text",
                            MENU_PROMPT_TEXT,
                        ),
                        reply_markup=markup,
                    )
                return {"message": "manual_menu_opened_for_group", "status": "ok"}
            else:
                # Invalid selection, show groups again
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_menu_prompt_text",
                        MENU_PROMPT_TEXT,
                    ),
                    reply_markup=self._manual_group_menu_markup(groups),
                )
                return {"message": "group_not_found", "detail": "selection_invalid"}

        # Handle manual selection (if in manual_menu state)
        # If a group is selected, show only manuals from that group
        if user.current_group_id:
            manuals = EnterpriseDocumentAssetRepository(db).list_by_group(user.current_group_id, active_only=True)
        else:
            manuals = self._documents.list_manuals(db, runtime.instance.instance_key)

        selected = next(
            (
                item
                for item in manuals
                if str(item.display_name or "").strip() == str(text or "").strip()
            ),
            None,
        )
        if not selected:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                (
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_no_manuals_text",
                        NO_MANUALS_TEXT,
                    )
                    if not manuals
                    else self._message_text(
                        runtime.platform_metadata,
                        "enterprise_menu_prompt_text",
                        MENU_PROMPT_TEXT,
                    )
                ),
                reply_markup=self._manual_menu_markup(db, runtime.instance.id),
            )
            return {"message": "manual_not_found", "detail": "selection_invalid"}

        resolved_link = str(selected.link_url or "").strip()
        if resolved_link:
            # Encode any literal spaces in the URL so the Markdown parser
            # (Bale auto-parses all messages as Markdown) doesn't break the
            # URL at the first space.
            safe_url = resolved_link.replace(" ", "%20")
            display_name = (
                str(selected.display_name or "").strip()
                or str(selected.original_filename or "").strip()
                or safe_url
            )
            template = self._message_text(
                runtime.platform_metadata,
                "enterprise_user_manual_link_template",
                USER_MANUAL_LINK_TEMPLATE,
            )
            message = (
                template
                .replace("{{user_manual_name}}", display_name)
                .replace("{{user_manual_url}}", safe_url)
            )
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                message,
                reply_markup=self._remove_keyboard_markup(),
            )
        else:
            asset_row, content = await self._documents.read_asset_bytes(db, selected.id)
            await self._send_media(
                runtime.instance.instance_key,
                chat_id,
                content,
                asset_row.original_filename,
                caption=asset_row.display_name or None,
                reply_markup=self._remove_keyboard_markup(),
            )
        # When a link was sent, the link message already dismissed the keyboard
        # (via _remove_keyboard_markup), so rebuild_keyboard must be False to
        # avoid sending the "فایل مورد نظر👆" separator message unnecessarily.
        needs_rebuild = (not resolved_link) and self._manual_menu_needs_root_rebuild(
            db, runtime.instance.id, user.gre_status
        )
        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
            rebuild_keyboard=needs_rebuild,
        )
        # Clear group selection after sending manual
        user.current_group_id = None
        db.add(user)
        db.flush()
        return {"message": "manual_sent", "status": "ok"}

    async def _handle_address_menu(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle address-menu selections."""
        action = ADDRESS_ACTIONS.get(str(text or "").strip())
        if action == "address_tehran_alborz":
            address_text = str(
                runtime.platform_metadata.get("enterprise_address_tehran_alborz_text")
                or ""
            ).strip() or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                address_text,
                reply_markup=self._remove_keyboard_markup(),
            )
            await self._show_root_menu(
                runtime.instance.instance_key,
                user,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return {"message": "address_sent", "status": "ok"}
        if action == "address_other_provinces":
            address_text = str(
                runtime.platform_metadata.get("enterprise_address_other_provinces_text")
                or ""
            ).strip() or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                address_text,
                reply_markup=self._remove_keyboard_markup(),
            )
            await self._show_root_menu(
                runtime.instance.instance_key,
                user,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return {"message": "address_sent", "status": "ok"}

        await self._show_root_menu(
            runtime.instance.instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )
        return {"message": "address_menu_closed", "status": "ok"}

    async def _send_catalog_and_root(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
    ) -> None:
        """Send the configured catalog and return to the root menu."""
        instance_key = runtime.instance.instance_key
        catalog = self._documents.get_catalog(db, instance_key)
        if not catalog:
            await self._send_text(
                instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_no_catalog_text",
                    NO_CATALOG_TEXT,
                ),
            )
            await self._show_root_menu(
                instance_key,
                user,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return

        resolved_link = str(catalog.link_url or "").strip()
        if resolved_link:
            safe_url = resolved_link.replace(" ", "%20")
            display_name = (
                str(catalog.display_name or "").strip()
                or str(catalog.original_filename or "").strip()
                or safe_url
            )
            template = self._message_text(
                runtime.platform_metadata,
                "enterprise_catalog_link_template",
                CATALOG_LINK_TEMPLATE,
            )
            message = (
                template
                .replace("{{catalog_name}}", display_name)
                .replace("{{catalog_url}}", safe_url)
            )
            await self._send_text(
                instance_key,
                chat_id,
                message,
                reply_markup=self._remove_keyboard_markup(),
            )
        else:
            asset_row, content = await self._documents.read_asset_bytes(db, catalog.id)
            await self._send_media(
                instance_key,
                chat_id,
                content,
                asset_row.original_filename,
                caption=asset_row.display_name or None,
                reply_markup=self._remove_keyboard_markup(),
            )
        await self._show_root_menu(
            instance_key,
            user,
            chat_id,
            platform_metadata=runtime.platform_metadata,
        )

    async def _enter_live_route(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        chat_id: str,
        route_key: str,
    ) -> None:
        """Enter or resume a route-specific live-chat session."""
        session, created_new = await self._get_or_open_route_session(
            db, runtime, user, route_key
        )
        self._set_user_state(db, user, self._require_route(route_key)["state"])

        pending_rows = self._pending(db).list_pending_for_session(session.id)
        if pending_rows:
            accepted_notice_sent_before = bool(session.accepted_notice_sent)
            await self._ensure_accepted_notice(
                instance_key=runtime.instance.instance_key,
                runtime=runtime,
                session=session,
                route_key=route_key,
                chat_id=chat_id,
            )
            if accepted_notice_sent_before:
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        runtime.platform_metadata,
                        "enterprise_live_mode_resume_text",
                        LIVE_MODE_RESUME_TEXT,
                    ),
                    reply_markup=self._live_menu_markup(),
                )
            for row in pending_rows:
                try:
                    await self._deliver_operator_payload(
                        runtime.instance.instance_key,
                        runtime=runtime,
                        chat_id=chat_id,
                        text=str(row.text_payload or ""),
                        attachments=row.attachment_payload_json
                        if isinstance(row.attachment_payload_json, list)
                        else [],
                        reply_markup=False,
                    )
                    row.status = EnterprisePendingMessageStatus.delivered
                    row.delivery_error = None
                except Exception as exc:
                    logger.warning(
                        "enterprise._enter_live_route pending_delivery_failed instance=%s session_id=%s pending_id=%s error_type=%s error=%s",
                        runtime.instance.instance_key,
                        session.id,
                        row.id,
                        type(exc).__name__,
                        str(exc),
                    )
                    row.status = EnterprisePendingMessageStatus.failed
                    row.delivery_error = str(exc) or type(exc).__name__
                self._pending(db).save(row)
            session.unread_notice_sent = False
            session.unread_count = 0
            self._sessions(db).save(session)
            return

        if not session.accepted_notice_sent:
            waiting_text = str(
                self._route_text(runtime.platform_metadata, route_key, "waiting")
            ) or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                waiting_text,
                reply_markup=self._live_menu_markup(),
            )
            return

        resume_text = self._message_text(
            runtime.platform_metadata,
            "enterprise_live_mode_resume_text",
            LIVE_MODE_RESUME_TEXT,
        )
        await self._send_text(
            runtime.instance.instance_key,
            chat_id,
            resume_text,
            reply_markup=self._live_menu_markup(),
        )

    async def _leave_live_route(
        self,
        instance_key: str,
        user: EnterpriseBaleUser,
        session: EnterpriseBaleSession,
        chat_id: str,
        *,
        platform_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Leave a live route without resolving the Chatwoot conversation."""
        session.user_present = False
        session.status = EnterpriseSessionStatus.closed_by_user
        await self._show_root_menu(
            instance_key,
            user,
            chat_id,
            platform_metadata=platform_metadata,
        )

    async def _handle_live_session_menu_input(
        self,
        db: Session,
        *,
        runtime: Any,
        user: EnterpriseBaleUser,
        session: EnterpriseBaleSession,
        chat_id: str,
        text: str,
    ) -> Optional[dict[str, Any]]:
        """Block commands/menu selections while a live session is active."""
        normalized = self._normalize_action_text(text)
        if not normalized:
            return None

        if self._is_back_to_menu(normalized):
            await self._leave_live_route(
                runtime.instance.instance_key,
                user,
                session,
                chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            return {"message": "left_live_route", "status": "ok"}

        if self._is_live_session_restricted_input(
            db,
            instance_id=runtime.instance.id,
            text=normalized,
        ):
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    runtime.platform_metadata,
                    "enterprise_live_session_locked_text",
                    LIVE_SESSION_LOCKED_TEXT,
                ),
                reply_markup=self._live_menu_markup(),
            )
            return {"message": "live_session_restricted_input_blocked", "status": "ok"}

        return None

    def _is_live_session_restricted_input(
        self,
        db: Session,
        *,
        instance_id: str,
        text: str,
    ) -> bool:
        """Return whether a live-session input is a blocked command/menu action."""
        command = self._normalize_command(text)
        if command and command not in {"/menu", "/back"}:
            return True
        return text in self._known_menu_button_labels(db, instance_id)

    def _known_menu_button_labels(
        self,
        db: Session,
        instance_id: str,
    ) -> set[str]:
        """Collect visible enterprise keyboard labels that should never hit Chatwoot."""
        cache_key = str(instance_id)
        cached = self._menu_label_cache.get(cache_key)
        if cached is not None:
            return cached

        labels: set[str] = set()
        for markup in (
            self._eligible_root_markup(),
            self._ineligible_root_markup(),
            self._address_menu_markup(),
            self._phone_prompt_markup(),
            self._live_menu_markup(),
            self._manual_menu_markup(db, instance_id),
        ):
            for row in self._keyboard_items(markup):
                labels.update(item for item in row if item)

        groups = EnterpriseManualGroupRepository(db).list_by_instance(
            instance_id,
            active_only=True,
        )
        for row in self._keyboard_items(self._manual_group_menu_markup(groups)):
            labels.update(item for item in row if item)

        self._menu_label_cache[cache_key] = labels
        return labels

    async def _forward_customer_message_to_chatwoot(
        self,
        db: Session,
        *,
        runtime: Any,
        user: EnterpriseBaleUser,
        session: EnterpriseBaleSession,
        text: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        """Post a customer message from Bale Enterprise into Chatwoot."""
        session = await self._ensure_forwardable_route_session(
            db, runtime, user, session
        )
        if session.status == EnterpriseSessionStatus.resolved:
            await self._show_root_menu(
                runtime.instance.instance_key,
                user,
                user.platform_chat_id,
                platform_metadata=runtime.platform_metadata,
            )
            raise ValueError(
                f"enterprise session for route {session.route_key} is resolved"
            )

        client = self._get_chatwoot_client(runtime.chatwoot)
        account_id = int(runtime.chatwoot["account_id"])
        payload = {
            "content": str(text or ""),
            "message_type": "incoming",
            "private": False,
            "source_id": self._enterprise_source_id(
                runtime.instance.instance_key, user.platform_chat_id
            ),
        }

        try:
            if attachments:
                normalized_attachments: list[tuple[str, bytes, Optional[str]]] = []
                for item in attachments:
                    content = item.get("content")
                    if not isinstance(content, (bytes, bytearray)):
                        continue
                    filename = str(item.get("filename") or "file").strip() or "file"
                    content_type = self._normalize_content_type(
                        filename=filename,
                        content_type=item.get("content_type"),
                        content=bytes(content),
                    )
                    normalized_attachments.append(
                        (filename, bytes(content), content_type)
                    )
                await client.post_message_with_attachments(
                    account_id,
                    int(session.chatwoot_conversation_id),
                    payload,
                    normalized_attachments,
                )
            else:
                await client.post_message(
                    account_id, int(session.chatwoot_conversation_id), payload
                )
            session.status = EnterpriseSessionStatus.open
            self._sessions(db).save(session)
        except httpx.HTTPStatusError as exc:
            if self._is_missing_chatwoot_conversation(exc.response):
                replacement = await self._recreate_route_session(
                    db, runtime, user, session
                )
                if attachments:
                    normalized_attachments = []
                    for item in attachments:
                        content = item.get("content")
                        if not isinstance(content, (bytes, bytearray)):
                            continue
                        filename = str(item.get("filename") or "file").strip() or "file"
                        content_type = self._normalize_content_type(
                            filename=filename,
                            content_type=item.get("content_type"),
                            content=bytes(content),
                        )
                        normalized_attachments.append(
                            (filename, bytes(content), content_type)
                        )
                    await client.post_message_with_attachments(
                        account_id,
                        int(replacement.chatwoot_conversation_id),
                        payload,
                        normalized_attachments,
                    )
                else:
                    await client.post_message(
                        account_id, int(replacement.chatwoot_conversation_id), payload
                    )
                return
            raise

    async def _ensure_accepted_notice(
        self,
        *,
        instance_key: str,
        runtime: Any,
        session: EnterpriseBaleSession,
        route_key: str,
        chat_id: str,
    ) -> None:
        """Send the accepted message once per session before operator content."""
        if session.accepted_notice_sent:
            return
        try:
            accepted_text = str(
                self._route_text(runtime.platform_metadata, route_key, "accepted")
            ) or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                instance_key,
                chat_id,
                accepted_text,
                reply_markup=self._live_menu_markup(),
            )
            session.accepted_notice_sent = True
        except Exception as exc:
            logger.warning(
                "enterprise._ensure_accepted_notice failed instance=%s session_id=%s route=%s error_type=%s error=%s",
                instance_key,
                session.id,
                route_key,
                type(exc).__name__,
                str(exc),
            )

    async def _deliver_operator_payload(
        self,
        instance_key: str,
        *,
        runtime: Any,
        chat_id: str,
        text: str,
        attachments: list[dict[str, Any]],
        reply_markup: Any = False,
    ) -> None:
        """Deliver a Chatwoot operator payload to Bale."""
        try:
            has_attachments = isinstance(attachments, list) and any(
                isinstance(item, dict) for item in attachments
            )
            if has_attachments:
                first = True
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    media = attachment.get("data_url") or attachment.get("content")
                    if isinstance(media, str) and media.startswith("/"):
                        media = f"{str(runtime.chatwoot.get('base_url') or '').rstrip('/')}{media}"
                    filename = str(attachment.get("filename") or "file").strip() or "file"
                    caption = str(text or "") if first and text else None
                    await self._send_media(
                        instance_key,
                        chat_id,
                        media,
                        filename,
                        caption=caption,
                        reply_markup=reply_markup if first else False,
                    )
                    first = False
                if not text:
                    return
                if first:
                    await self._send_text(
                        instance_key, chat_id, text, reply_markup=reply_markup
                    )
                return

            if text:
                await self._send_text(
                    instance_key, chat_id, text, reply_markup=reply_markup
                )
        except Exception as exc:
            logger.warning(
                "enterprise._deliver_operator_payload failed instance=%s chat_id=%s error_type=%s error=%s",
                instance_key,
                chat_id,
                type(exc).__name__,
                str(exc),
            )

    async def _get_or_open_route_session(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        route_key: str,
    ) -> tuple[EnterpriseBaleSession, bool]:
        """Get the current unresolved session for a route or create a new one."""
        inbox_id = await self._ensure_route_inbox(db, runtime, route_key)
        session = self._sessions(db).get_unresolved_for_user_route(user.id, route_key)
        if session:
            session = await self._resolve_reusable_route_session(db, runtime, session)
        if session and str(session.chatwoot_inbox_id or "").strip() == str(inbox_id):
            session.user_present = True
            session.status = EnterpriseSessionStatus.open
            self._sessions(db).save(session)
            return session, False

        if session:
            session.status = EnterpriseSessionStatus.resolved
            session.user_present = False
            self._sessions(db).save(session)

        contact_id = await self._get_or_create_contact(runtime, user, int(inbox_id))
        client = self._get_chatwoot_client(runtime.chatwoot)
        created = await client.create_conversation(
            int(runtime.chatwoot["account_id"]),
            {
                "contact_id": str(contact_id),
                "inbox_id": str(inbox_id),
            },
        )
        chatwoot_conversation_id = self._extract_id(created) or self._extract_id(
            (created or {}).get("payload")
        )
        if not chatwoot_conversation_id:
            raise RuntimeError(
                f"failed to create Chatwoot conversation for route {route_key}"
            )

        row = EnterpriseBaleSession(
            user_id=user.id,
            route_key=route_key,
            chatwoot_conversation_id=str(chatwoot_conversation_id),
            chatwoot_contact_id=str(contact_id),
            chatwoot_inbox_id=str(inbox_id),
            status=EnterpriseSessionStatus.open,
            user_present=True,
            accepted_notice_sent=False,
            unread_notice_sent=False,
            unread_count=0,
        )
        self._sessions(db).save(row)
        return row, True

    async def _ensure_forwardable_route_session(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        session: EnterpriseBaleSession,
    ) -> EnterpriseBaleSession:
        """Resolve a usable route session before sending a customer message."""
        reusable = await self._resolve_reusable_route_session(db, runtime, session)
        if reusable is not None:
            return reusable
        replacement, _created_new = await self._get_or_open_route_session(
            db,
            runtime,
            user,
            str(session.route_key or "").strip(),
        )
        return replacement

    async def _resolve_reusable_route_session(
        self,
        db: Session,
        runtime: Any,
        session: EnterpriseBaleSession,
    ) -> Optional[EnterpriseBaleSession]:
        """Return the session only if Chatwoot still reports it unresolved."""
        if session.status == EnterpriseSessionStatus.resolved:
            return None

        remote_status = await self._get_remote_route_session_status(runtime, session)
        if remote_status is None:
            return session
        if remote_status != "resolved":
            return session

        logger.info(
            "enterprise session marked resolved from remote status instance=%s session_id=%s conversation_id=%s route=%s",
            runtime.instance.instance_key,
            session.id,
            session.chatwoot_conversation_id,
            session.route_key,
        )
        session.status = EnterpriseSessionStatus.resolved
        session.user_present = False
        self._sessions(db).save(session)
        return None

    async def _get_remote_route_session_status(
        self,
        runtime: Any,
        session: EnterpriseBaleSession,
    ) -> Optional[str]:
        """Resolve the current Chatwoot status for a stored enterprise session."""
        account_id = runtime.chatwoot.get("account_id")
        contact_id = str(session.chatwoot_contact_id or "").strip()
        if not account_id or not contact_id.isdigit():
            return None

        client = self._get_chatwoot_client(runtime.chatwoot)
        try:
            response = await client.list_contact_conversations(
                int(account_id), int(contact_id)
            )
        except httpx.HTTPStatusError as exc:
            # A missing contact means Chatwoot no longer recognizes the stored
            # route session. Returning "resolved" lets the existing recreation
            # path build a fresh contact/conversation pair on the next send.
            if self._is_missing_chatwoot_contact(exc.response):
                logger.info(
                    "enterprise contact missing remotely; route session will be recreated instance=%s account_id=%s contact_id=%s session_id=%s conversation_id=%s route=%s",
                    runtime.instance.instance_key,
                    account_id,
                    contact_id,
                    session.id,
                    session.chatwoot_conversation_id,
                    session.route_key,
                )
                return "resolved"
            logger.exception(
                "failed to list enterprise contact conversations instance=%s account_id=%s contact_id=%s",
                runtime.instance.instance_key,
                account_id,
                contact_id,
            )
            return None
        except Exception:
            logger.exception(
                "failed to list enterprise contact conversations instance=%s account_id=%s contact_id=%s",
                runtime.instance.instance_key,
                account_id,
                contact_id,
            )
            return None

        payload = response.get("payload") if isinstance(response, dict) else None
        if not isinstance(payload, list):
            return None

        matched = self._find_remote_route_conversation(
            [item for item in payload if isinstance(item, dict)],
            conversation_id=str(session.chatwoot_conversation_id or "").strip(),
            inbox_id=str(session.chatwoot_inbox_id or "").strip() or None,
        )
        if matched is None:
            return "resolved"
        return self._normalize_chatwoot_status(matched.get("status")) or "open"

    def _find_remote_route_conversation(
        self,
        conversations: list[dict[str, Any]],
        *,
        conversation_id: str,
        inbox_id: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Find a remote Chatwoot conversation payload for an enterprise route session."""
        expected_id = str(conversation_id or "").strip()
        expected_inbox = str(inbox_id or "").strip() or None
        if not expected_id:
            return None

        for item in conversations:
            if str(self._extract_id(item) or "").strip() != expected_id:
                continue
            if (
                expected_inbox is not None
                and str(item.get("inbox_id") or "").strip() != expected_inbox
            ):
                continue
            return item
        return None

    async def _recreate_route_session(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseBaleUser,
        session: EnterpriseBaleSession,
    ) -> EnterpriseBaleSession:
        """Replace a missing Chatwoot conversation with a new route session."""
        session.status = EnterpriseSessionStatus.resolved
        session.user_present = False
        self._sessions(db).save(session)
        replacement, _created_new = await self._get_or_open_route_session(
            db, runtime, user, str(session.route_key or "").strip()
        )
        return replacement

    async def _get_or_create_contact(
        self, runtime: Any, user: EnterpriseBaleUser, inbox_id: int
    ) -> int:
        """Resolve a Chatwoot contact for an enterprise user."""
        client = self._get_chatwoot_client(runtime.chatwoot)
        account_id = int(runtime.chatwoot["account_id"])
        identifier = self._enterprise_source_id(
            runtime.instance.instance_key, user.platform_chat_id
        )
        normalized_phone = self._normalize_phone_number(user.phone_number)
        resolved_name = str(user.display_name or user.platform_chat_id).strip() or str(
            user.platform_chat_id
        )

        current_contact = await self._find_contact_by_identifier(
            client, account_id, identifier
        )
        if not current_contact and normalized_phone:
            current_contact = await self._find_contact_by_phone(
                client, account_id, normalized_phone
            )

        if current_contact:
            contact_id = self._extract_id(current_contact) or self._extract_id(
                (current_contact or {}).get("payload")
            )
            if not contact_id:
                raise RuntimeError("failed to resolve existing enterprise contact id")
            await self._sync_contact_phone_if_needed(
                client,
                account_id=account_id,
                contact_id=int(contact_id),
                current_contact=current_contact,
                normalized_phone=normalized_phone,
                identifier=identifier,
                fallback_name=resolved_name,
            )
            return int(contact_id)

        create_payload = {
            "inbox_id": int(inbox_id),
            "name": resolved_name,
            "identifier": identifier,
        }
        if normalized_phone:
            create_payload["phone_number"] = normalized_phone

        try:
            created = await client.create_contact(account_id, create_payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            # 422 can mean a race-condition duplicate or an invalid phone number.
            logger.warning(
                "enterprise_contact_create_422 identifier=%s response=%s",
                identifier,
                exc.response.text[:500],
            )
            # Re-try the lookup – another worker may have just created the contact.
            retry_contact = await self._find_contact_by_identifier(
                client, account_id, identifier
            )
            if not retry_contact and normalized_phone:
                retry_contact = await self._find_contact_by_phone(
                    client, account_id, normalized_phone
                )
            if retry_contact:
                retry_id = self._extract_id(retry_contact) or self._extract_id(
                    (retry_contact or {}).get("payload")
                )
                if retry_id:
                    return int(retry_id)
            # If a phone number was included the 422 may be due to its format;
            # retry the creation without it as a last resort.
            if normalized_phone and "phone_number" in create_payload:
                logger.warning(
                    "enterprise_contact_create_retry_without_phone identifier=%s",
                    identifier,
                )
                fallback_payload = {
                    k: v for k, v in create_payload.items() if k != "phone_number"
                }
                created = await client.create_contact(account_id, fallback_payload)
            else:
                raise RuntimeError(
                    f"create_contact returned 422 for identifier={identifier!r}: "
                    f"{exc.response.text[:300]}"
                ) from exc
        contact_id = (
            self._extract_id(created)
            or self._extract_id((created or {}).get("payload"))
            or self._extract_id(((created or {}).get("payload") or {}).get("contact"))
        )
        if not contact_id:
            logger.error(
                "enterprise_contact_create_no_id identifier=%s created_response=%s",
                identifier,
                str(created)[:500],
            )
            raise RuntimeError("failed to create enterprise contact")
        return int(contact_id)

    async def _find_contact_by_identifier(
        self,
        client: ChatwootClient,
        account_id: int,
        identifier: str,
    ) -> Optional[dict[str, Any]]:
        """Find a contact by its enterprise identifier."""
        found = await client.search_contacts(account_id, identifier)
        payload = found.get("payload") if isinstance(found, dict) else None
        if not isinstance(payload, list):
            return None
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("identifier") or "").strip() == identifier:
                return row
            contact = (
                row.get("contact") if isinstance(row.get("contact"), dict) else None
            )
            if contact and str(contact.get("identifier") or "").strip() == identifier:
                return contact
        return None

    async def _find_contact_by_phone(
        self,
        client: ChatwootClient,
        account_id: int,
        normalized_phone: str,
    ) -> Optional[dict[str, Any]]:
        """Find a contact by phone number using the current Chatwoot search endpoint."""
        candidates = [normalized_phone]
        plus_candidate = self._to_plus_phone_candidate(normalized_phone)
        if plus_candidate and plus_candidate not in candidates:
            candidates.append(plus_candidate)

        logger.debug(
            "enterprise.contact_phone_compare_start account_id=%s normalized_phone=%s candidates=%s",
            account_id,
            normalized_phone,
            candidates,
        )

        for candidate in candidates:
            logger.debug(
                "enterprise.contact_phone_compare_candidate account_id=%s candidate=%s",
                account_id,
                candidate,
            )
            found = await client.search_contacts(account_id, candidate)
            payload = found.get("payload") if isinstance(found, dict) else None
            if not isinstance(payload, list):
                logger.debug(
                    "enterprise.contact_phone_compare_no_payload account_id=%s candidate=%s",
                    account_id,
                    candidate,
                )
                continue
            for row in payload:
                if not isinstance(row, dict):
                    continue
                contact_payload = (
                    row.get("contact") if isinstance(row.get("contact"), dict) else row
                )
                phone_number = self._normalize_phone_number(
                    contact_payload.get("phone_number")
                )
                logger.debug(
                    "enterprise.contact_phone_compare_row account_id=%s candidate=%s contact_id=%s raw_phone=%s normalized_phone=%s",
                    account_id,
                    candidate,
                    self._extract_id(contact_payload),
                    contact_payload.get("phone_number"),
                    phone_number,
                )
                if phone_number == normalized_phone or phone_number == plus_candidate:
                    logger.debug(
                        "enterprise.contact_phone_compare_match account_id=%s candidate=%s contact_id=%s",
                        account_id,
                        candidate,
                        self._extract_id(contact_payload),
                    )
                    return contact_payload
        logger.debug(
            "enterprise.contact_phone_compare_miss account_id=%s normalized_phone=%s",
            account_id,
            normalized_phone,
        )
        return None

    async def _sync_contact_phone_if_needed(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        contact_id: int,
        current_contact: dict[str, Any],
        normalized_phone: Optional[str],
        identifier: str,
        fallback_name: str,
    ) -> None:
        """Best-effort phone sync for enterprise contacts."""
        if not normalized_phone:
            return

        current_phone = self._normalize_phone_number(
            current_contact.get("phone_number")
        )
        if current_phone == normalized_phone:
            return

        payload = {
            "name": str(current_contact.get("name") or "").strip() or fallback_name,
            "phone_number": normalized_phone,
            "identifier": str(current_contact.get("identifier") or "").strip()
            or identifier,
        }
        try:
            await client.update_contact(account_id, contact_id, payload)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 422:
                logger.info(
                    "enterprise contact phone sync skipped due to validation conflict account_id=%s contact_id=%s",
                    account_id,
                    contact_id,
                )
                return
            raise
        except Exception:
            logger.exception(
                "enterprise contact phone sync failed account_id=%s contact_id=%s",
                account_id,
                contact_id,
            )

    async def _ensure_route_inbox(
        self, db: Session, runtime: Any, route_key: str
    ) -> int:
        """Resolve or auto-create a route inbox."""
        route_cfg = self._require_route(route_key)
        inbox_id = runtime.platform_metadata.get(route_cfg["inbox_id_key"])
        if inbox_id is not None and str(inbox_id).strip():
            return int(inbox_id)

        if bool(runtime.platform_metadata.get(route_cfg["auto_create_key"])):
            created = await self.create_route_inbox(
                db, runtime.instance.instance_key, route_key
            )
            resolved_id = created.get("inbox_id")
            if resolved_id:
                runtime.platform_metadata[route_cfg["inbox_id_key"]] = int(resolved_id)
                return int(resolved_id)

        raise ValueError(
            f"{route_cfg['inbox_id_key']} is required for route {route_key}"
        )

    def _leave_live_session_if_needed(
        self, db: Session, user: EnterpriseBaleUser
    ) -> None:
        """Close the current live session on explicit restart."""
        session = self._active_live_session_for_state(db, user)
        if not session:
            return
        session.user_present = False
        session.status = EnterpriseSessionStatus.closed_by_user
        self._sessions(db).save(session)

    def _active_live_session_for_state(
        self, db: Session, user: EnterpriseBaleUser
    ) -> Optional[EnterpriseBaleSession]:
        """Resolve the live session matching the user's current state."""
        route_key = None
        if user.current_state == EnterpriseUserState.live_customer_service:
            route_key = ROUTE_CUSTOMER_SERVICE
        elif user.current_state == EnterpriseUserState.live_sales:
            route_key = ROUTE_SALES
        if not route_key:
            return None
        session = self._sessions(db).get_unresolved_for_user_route(user.id, route_key)
        return session

    def _mark_user_present(self, db: Session, session: EnterpriseBaleSession) -> None:
        """Mark the user as present in a live session."""
        if not session.user_present:
            session.user_present = True
            self._sessions(db).save(session)

    async def _show_root_menu(
        self,
        instance_key: str,
        user: EnterpriseBaleUser,
        chat_id: str,
        *,
        platform_metadata: Optional[dict[str, Any]] = None,
        rebuild_keyboard: bool = False,
        send_prompt_text: bool = True,
    ) -> None:
        """Render the correct GRE-root menu."""
        user.current_group_id = None
        markup = self._root_menu_markup(user.gre_status)
        prompt_text = (
            self._message_text(
                platform_metadata, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
            )
            if send_prompt_text
            else ""
        )
        logger.info(
            "enterprise.menu_send instance=%s chat_id=%s menu=root gre_status=%s rebuild=%s items=%s",
            instance_key,
            chat_id,
            str(user.gre_status),
            rebuild_keyboard,
            self._keyboard_items(markup),
        )
        if rebuild_keyboard:
            await self._send_text(
                instance_key,
                chat_id,
                DESIERED_FILE_TEXT,
                reply_markup=self._remove_keyboard_markup(),
            )
            await asyncio.sleep(0.15)
        if user.gre_status == EnterpriseGreStatus.eligible:
            user.current_state = EnterpriseUserState.eligible_root
            if prompt_text:  # Only send message if text is not empty
                await self._send_text(
                    instance_key,
                    chat_id,
                    prompt_text,
                    reply_markup=markup,
                )
            return

        user.current_state = EnterpriseUserState.ineligible_root
        if prompt_text:  # Only send message if text is not empty
            await self._send_text(
                instance_key,
                chat_id,
                prompt_text,
                reply_markup=markup,
            )

    async def _send_phone_prompt(
        self,
        instance_key: str,
        chat_id: str,
        *,
        platform_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send the enterprise phone-capture prompt."""
        await self._send_text(
            instance_key,
            chat_id,
            self._message_text(
                platform_metadata, "enterprise_phone_prompt_text", PHONE_PROMPT_TEXT
            ),
            reply_markup=self._phone_prompt_markup(),
        )

    def _get_or_create_user(
        self,
        db: Session,
        instance_id: str,
        platform_chat_id: str,
        *,
        display_name: Optional[str],
    ) -> EnterpriseBaleUser:
        """Resolve or create an enterprise Bale user row."""
        repo = self._users(db)
        row = repo.get_by_platform_chat_id(instance_id, platform_chat_id)
        if not row:
            row = EnterpriseBaleUser(
                instance_id=str(instance_id),
                platform_chat_id=str(platform_chat_id),
                display_name=str(display_name or "").strip() or None,
                gre_status=EnterpriseGreStatus.unknown,
                current_state=EnterpriseUserState.awaiting_phone_input,
            )
        elif display_name:
            row.display_name = str(display_name).strip() or row.display_name
        repo.save(row)
        return row

    def _set_user_state(
        self, db: Session, user: EnterpriseBaleUser, state: EnterpriseUserState
    ) -> None:
        """Persist a user state transition."""
        user.current_state = state
        self._users(db).save(user)

    def _get_chatwoot_client(self, chatwoot: dict[str, Any]) -> ChatwootClient:
        """Get or create a cached Chatwoot client."""
        base_url = str(chatwoot.get("base_url") or "").strip()
        token = str(chatwoot.get("api_access_token") or "").strip()
        key = f"{base_url}|{token}"
        client = self._clients.get(key)
        if client is None:
            client = ChatwootClient(base_url=base_url, token=token)
            self._clients.set(key, client)
        return client

    def _require_runtime_instance(self, db: Session, instance_key: str):
        """Require a Bale Enterprise runtime instance."""
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if not runtime:
            raise ValueError("instance not found")
        if str(runtime.platform_type.key or "").strip().lower() != "bale_enterprise":
            raise ValueError("instance is not a bale_enterprise instance")
        if not runtime.chatwoot.get("account_id"):
            raise ValueError("chatwoot.account_id is required")
        return runtime

    @staticmethod
    def _require_route(route_key: str) -> dict[str, Any]:
        """Resolve a supported enterprise route config."""
        cfg = ROUTE_CONFIG.get(str(route_key or "").strip())
        if not cfg:
            raise ValueError(f"unsupported enterprise route {route_key}")
        return cfg

    @staticmethod
    def _route_text(
        platform_metadata: dict[str, Any], route_key: str, kind: str
    ) -> Optional[str]:
        """Resolve a route-specific configured text."""
        route_cfg = ROUTE_CONFIG.get(str(route_key or "").strip())
        if not route_cfg:
            return None
        key_map = {
            "waiting": route_cfg["waiting_text_key"],
            "accepted": route_cfg["accepted_text_key"],
            "unread": route_cfg["unread_text_key"],
        }
        field_name = key_map.get(kind)
        if not field_name:
            return None
        text = str((platform_metadata or {}).get(field_name) or "").strip()
        return text or None

    @staticmethod
    def _message_text(
        platform_metadata: Optional[dict[str, Any]], field_name: str, default: str
    ) -> str:
        """Resolve a configurable enterprise message text with a static fallback."""
        text = str((platform_metadata or {}).get(field_name) or "").strip()
        return text or str(default or "").strip()

    @staticmethod
    def _not_configured_text(platform_metadata: Optional[dict[str, Any]]) -> str:
        """Resolve the configurable fallback for missing route/content configuration."""
        return EnterpriseBaleService._message_text(
            platform_metadata,
            "enterprise_not_configured_text",
            NOT_CONFIGURED_TEXT,
        )

    @staticmethod
    def _extract_chat_id(message: dict[str, Any]) -> Optional[str]:
        """Extract a chat id from a Bale update message."""
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        value = chat.get("id") or chat.get("username") or message.get("chat_id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_from_name(message: dict[str, Any]) -> Optional[str]:
        """Extract a sender display name from a Bale update message."""
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        full_name = " ".join(
            [
                str(sender.get("first_name") or "").strip(),
                str(sender.get("last_name") or "").strip(),
            ]
        ).strip()
        return full_name or str(sender.get("username") or "").strip() or None

    @staticmethod
    def _normalize_command(text: str) -> Optional[str]:
        """Normalize a leading slash command."""
        raw = str(text or "").strip()
        if not raw.startswith("/"):
            return None
        return raw.split()[0].split("@")[0].strip().lower() or None

    @staticmethod
    def _normalize_action_text(text: str) -> str:
        """Normalize a user-entered menu action string."""
        return str(text or "").strip()

    @staticmethod
    def _extract_contact_payload(message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Extract a shared-contact payload."""
        contact = message.get("contact")
        if not isinstance(contact, dict):
            return None
        phone = str(contact.get("phone_number") or "").strip()
        if not phone:
            return None
        first_name = str(contact.get("first_name") or "").strip() or None
        last_name = str(contact.get("last_name") or "").strip() or None
        user_id = contact.get("user_id")
        return {
            "phone_number": phone,
            "first_name": first_name,
            "last_name": last_name,
            "user_id": str(user_id).strip() if user_id is not None else None,
        }

    @staticmethod
    def _extract_contact_text(message: dict[str, Any]) -> Optional[str]:
        """Build a human-readable contact text representation."""
        contact = EnterpriseBaleService._extract_contact_payload(message)
        if not contact:
            return None
        name = " ".join(
            [
                str(contact.get("first_name") or "").strip(),
                str(contact.get("last_name") or "").strip(),
            ]
        ).strip()
        if name:
            return f"Shared phone number: {contact['phone_number']} ({name})"
        return f"Shared phone number: {contact['phone_number']}"

    def _extract_phone_input(
        self, *, contact_payload: Optional[dict[str, Any]], text: str
    ) -> Optional[str]:
        """Resolve a submitted phone number from a contact share or typed text."""
        if isinstance(contact_payload, dict):
            phone_number = self._normalize_phone_number(
                contact_payload.get("phone_number")
            )
            if phone_number:
                return phone_number
        return self._normalize_phone_number(text)

    @staticmethod
    def _normalize_phone_number(value: Any) -> Optional[str]:
        """Normalize a phone number for GRE and contact operations."""
        text = str(value or "").strip()
        if not text:
            return None
        compact = re.sub(r"\s+", "", text)
        if compact.startswith("+"):
            digits = re.sub(r"\D", "", compact[1:])
            if len(digits) < 8 or len(digits) > 15:
                return None
            return f"+{digits}" if digits else None
        if compact.startswith("00"):
            digits = re.sub(r"\D", "", compact[2:])
            if len(digits) < 8 or len(digits) > 15:
                return None
            return f"+{digits}" if digits else None
        digits = re.sub(r"\D", "", compact)
        if not digits or len(digits) < 8 or len(digits) > 15:
            return None
        if len(digits) == 12 and digits.startswith("98"):
            return f"+{digits}"
        if len(digits) == 11 and digits.startswith("0"):
            return f"+98{digits[1:]}"
        if len(digits) == 10 and digits.startswith("9"):
            return f"+98{digits}"
        if not digits.startswith("0"):
            return f"+{digits}"
        return digits

    @staticmethod
    def _to_plus_phone_candidate(phone: Optional[str]) -> Optional[str]:
        """Convert a normalized phone into a plus-prefixed search candidate when possible."""
        value = str(phone or "").strip()
        if not value or value.startswith("+"):
            return None
        digits = re.sub(r"\D", "", value)
        if not digits:
            return None
        if len(digits) == 12 and digits.startswith("98"):
            return f"+{digits}"
        if len(digits) == 11 and digits.startswith("0"):
            return f"+98{digits[1:]}"
        if len(digits) == 10 and digits.startswith("9"):
            return f"+98{digits}"
        if digits.startswith("0"):
            return None
        if len(digits) < 8 or len(digits) > 15:
            return None
        return f"+{digits}"

    @staticmethod
    def _enterprise_source_id(instance_key: str, chat_id: str) -> str:
        """Build a stable enterprise contact identifier."""
        return f"BALE_ENTERPRISE:{str(instance_key).strip()}:{str(chat_id).strip()}"

    @staticmethod
    def _phone_prompt_markup() -> dict[str, Any]:
        """Build the enterprise phone prompt keyboard."""
        return {
            "keyboard": [
                [{"text": "اشتراک‌گذاری شماره موبایل", "request_contact": True}],
                [{"text": BACK_TO_MENU_LABEL}],
            ],
            "resize_keyboard": True,
        }

    @staticmethod
    def _eligible_root_markup() -> dict[str, Any]:
        """Build the eligible enterprise root keyboard."""
        return {
            "keyboard": [
                [{"text": USER_MANUAL_LABEL}],
                # [{'text': CUSTOMER_SERVICE_ADDRESSES_LABEL}],
                [{"text": PRODUCTS_CATALOG_LABEL}],
                [{"text": CONTACT_CUSTOMER_SERVICE_LABEL}],
                [{"text": CONTACT_SALES_LABEL}],
            ],
            "resize_keyboard": True,
        }

    @staticmethod
    def _ineligible_root_markup() -> dict[str, Any]:
        """Build the ineligible enterprise root keyboard."""
        return {
            "keyboard": [
                [{"text": USER_MANUAL_LABEL_LOCKED}],
                [{"text": PRODUCTS_CATALOG_LABEL}],
                [{"text": CONTACT_CUSTOMER_SERVICE_LABEL}],
                [{"text": CONTACT_SALES_LABEL}],
                # [{'text': CUSTOMER_SERVICE_ADDRESSES_LABEL}],
                # [{"text": RECHECK_PHONE_LABEL}],
            ],
            "resize_keyboard": True,
        }

    def _manual_menu_markup(self, db: Session, instance_id: str) -> dict[str, Any]:
        """Build the manual-selection keyboard."""
        rows = EnterpriseDocumentAssetRepository(db).list_for_instance(
            instance_id,
            asset_type=EnterpriseDocumentAssetType.manual,
        )
        keyboard = [
            [{"text": str(row.display_name or row.original_filename).strip()}]
            for row in rows
        ]
        keyboard.append([{"text": BACK_TO_MENU_LABEL}])
        return {
            "keyboard": keyboard,
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    @staticmethod
    def _manual_group_menu_markup(groups: list) -> dict[str, Any]:
        """Build the manual-group selection keyboard."""
        keyboard = [
            [{"text": str(group.name).strip()}]
            for group in groups
        ]
        keyboard.append([{"text": BACK_TO_MENU_LABEL}])
        return {
            "keyboard": keyboard,
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    @staticmethod
    def _address_menu_markup() -> dict[str, Any]:
        """Build the address-selection keyboard."""
        return {
            "keyboard": [
                [{"text": ADDRESS_TEHRAN_ALBORZ_LABEL}],
                [{"text": ADDRESS_OTHER_PROVINCES_LABEL}],
                [{"text": BACK_TO_MENU_LABEL}],
            ],
            "resize_keyboard": True,
        }

    @staticmethod
    def _live_menu_markup() -> dict[str, Any]:
        """Build the live-chat keyboard."""
        return {"keyboard": [[{"text": BACK_TO_MENU_LABEL}]], "resize_keyboard": True}

    def _root_menu_markup(self, gre_status: EnterpriseGreStatus) -> dict[str, Any]:
        """Resolve the correct root keyboard for a GRE status."""
        if gre_status == EnterpriseGreStatus.eligible:
            return self._eligible_root_markup()
        return self._ineligible_root_markup()

    def _manual_menu_needs_root_rebuild(
        self,
        db: Session,
        instance_id: str,
        gre_status: EnterpriseGreStatus,
    ) -> bool:
        """Return whether the manual submenu is taller than the root menu and needs an explicit rebuild."""
        manual_rows = self._manual_menu_row_count(db, instance_id)
        root_rows = len(self._root_menu_markup(gre_status).get("keyboard") or [])
        return manual_rows > root_rows

    @staticmethod
    def _manual_menu_row_count(db: Session, instance_id: str) -> int:
        """Return the number of visible rows in the current manual menu, including the back row."""
        rows = EnterpriseDocumentAssetRepository(db).list_for_instance(
            instance_id,
            asset_type=EnterpriseDocumentAssetType.manual,
        )
        return len(rows) + 1

    @staticmethod
    def _remove_keyboard_markup() -> dict[str, Any]:
        """Build a reply-markup payload that explicitly clears the current Bale keyboard."""
        return {"remove_keyboard": True}

    @staticmethod
    def _keyboard_items(reply_markup: Optional[dict[str, Any]]) -> list[list[str]]:
        """Extract keyboard item labels for debug logging."""
        if not isinstance(reply_markup, dict):
            return []
        keyboard = reply_markup.get("keyboard")
        if not isinstance(keyboard, list):
            return []
        out: list[list[str]] = []
        for row in keyboard:
            if not isinstance(row, list):
                continue
            labels = []
            for item in row:
                if not isinstance(item, dict):
                    continue
                labels.append(str(item.get("text") or "").strip())
            out.append(labels)
        return out

    @staticmethod
    def _is_back_to_menu(text: str) -> bool:
        """Check whether the user requested to leave the current menu or live session."""
        normalized = str(text or "").strip()
        return normalized == BACK_TO_MENU_LABEL or normalized in {"/menu", "/back"}

    async def _send_text(
        self, instance_key: str, chat_id: str, text: str, *, reply_markup: Any = False
    ) -> dict[str, Any]:
        """Send a Bale Enterprise text message. Raises on failure so callers know."""
        connector = connector_registry.get("bale_enterprise")
        return await connector.send_text(
            instance_key, chat_id, text, reply_markup=reply_markup
        )

    async def _send_media(
        self,
        instance_key: str,
        chat_id: str,
        media: Any,
        filename: str,
        *,
        caption: Optional[str] = None,
        reply_markup: Any = False,
    ) -> dict[str, Any]:
        """Send a Bale Enterprise media message. Raises on failure so callers know."""
        connector = connector_registry.get("bale_enterprise")
        return await connector.send_media(
            instance_key,
            chat_id,
            media,
            filename,
            caption=caption,
            reply_markup=reply_markup,
        )

    async def _extract_attachments(
        self, instance_key: str, message: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Download Bale attachments into Chatwoot-ready payloads."""
        file_id, filename, content_type_hint = self._extract_file(message)
        if not file_id:
            return []
        connector = connector_registry.get("bale_enterprise")
        content, content_type, file_path = await connector.download_file_by_id(
            instance_key, file_id=file_id
        )
        if not content:
            return []
        resolved_filename = filename or (
            str(file_path).split("/")[-1] if file_path else "file"
        )
        resolved_content_type = self._normalize_content_type(
            filename=resolved_filename,
            content_type=content_type or content_type_hint,
            content=content,
        )
        # Chatwoot cannot display WEBP stickers — convert to JPEG/PNG.
        if resolved_content_type == "image/webp":
            from app.adapters.bale_pv import BalePvAdapter
            converted, ext, converted_ct = BalePvAdapter._convert_webp(content)
            if converted and ext and converted_ct:
                content = converted
                resolved_filename = str(resolved_filename).rsplit(".", 1)[0] + ext
                resolved_content_type = converted_ct
        return [
            {
                "filename": resolved_filename,
                "content": content,
                "content_type": resolved_content_type,
            }
        ]

    @staticmethod
    def _extract_file(
        message: dict[str, Any],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract a single Bale attachment reference from a message."""
        doc = message.get("document")
        if isinstance(doc, dict) and doc.get("file_id"):
            return str(doc.get("file_id")), doc.get("file_name"), doc.get("mime_type")

        video = message.get("video")
        if isinstance(video, dict) and video.get("file_id"):
            return (
                str(video.get("file_id")),
                "video.mp4",
                video.get("mime_type") or "video/mp4",
            )

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidate = photo[-1]
            if isinstance(candidate, dict) and candidate.get("file_id"):
                return str(candidate.get("file_id")), "photo.jpg", "image/jpeg"

        audio = message.get("audio")
        if isinstance(audio, dict) and audio.get("file_id"):
            return (
                str(audio.get("file_id")),
                audio.get("file_name") or "audio.ogg",
                audio.get("mime_type"),
            )

        voice = message.get("voice")
        if isinstance(voice, dict) and voice.get("file_id"):
            return (
                str(voice.get("file_id")),
                "voice.ogg",
                voice.get("mime_type") or "audio/ogg",
            )

        sticker = message.get("sticker")
        if isinstance(sticker, dict) and sticker.get("file_id"):
            thumbnail = sticker.get("thumbnail")
            thumb_id = thumbnail.get("file_id") if isinstance(thumbnail, dict) else None
            return str(thumb_id or sticker.get("file_id")), "sticker.webp", "image/webp"

        return None, None, None

    @staticmethod
    def _normalize_content_type(
        *, filename: str, content_type: Optional[str], content: bytes
    ) -> Optional[str]:
        """Resolve a usable content type for an attachment."""
        raw = str(content_type or "").strip().lower()
        if raw and raw != "application/octet-stream":
            return raw

        guessed = mimetypes.guess_type(str(filename or "").strip())[0]
        if guessed:
            return guessed.lower()

        if content.startswith(b"%PDF"):
            return "application/pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"OggS"):
            return "audio/ogg"
        if len(content) > 8 and content[4:8] == b"ftyp":
            return "video/mp4"
        return None

    @staticmethod
    def _extract_chatwoot_conversation_id(payload: dict[str, Any]) -> Optional[str]:
        """Extract a Chatwoot conversation id from a webhook payload."""
        conversation = (
            payload.get("conversation")
            if isinstance(payload.get("conversation"), dict)
            else {}
        )
        cid = (
            conversation.get("id")
            or payload.get("conversation_id")
            or payload.get("conversationId")
        )
        if cid is None:
            return None
        text = str(cid).strip()
        return text or None

    @staticmethod
    def _extract_chatwoot_message_id(payload: dict[str, Any]) -> Optional[str]:
        """Extract a Chatwoot message id from a webhook payload."""
        message_obj = (
            payload.get("message") if isinstance(payload.get("message"), dict) else {}
        )
        candidate = payload.get("id")
        if candidate is None:
            candidate = message_obj.get("id")
        if candidate is None:
            return None
        text = str(candidate).strip()
        return text or None

    @staticmethod
    def _extract_chatwoot_message_text(payload: dict[str, Any]) -> str:
        """Extract outbound Chatwoot message text."""
        message_obj = (
            payload.get("message") if isinstance(payload.get("message"), dict) else {}
        )
        candidates = [
            payload.get("content"),
            message_obj.get("content"),
            payload.get("processed_message_content"),
            message_obj.get("processed_message_content"),
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_chatwoot_message_type(value: Any) -> str:
        """Normalize Chatwoot webhook message_type values across enum/string shapes."""
        if isinstance(value, int):
            return {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(
                value, str(value)
            )
        return str(value or "").strip().lower()

    @staticmethod
    def _is_forwardable_chatwoot_message(
        payload: dict[str, Any], event_name: str
    ) -> bool:
        """Decide whether an enterprise Chatwoot webhook should be forwarded to Bale."""
        message_obj = (
            payload.get("message") if isinstance(payload.get("message"), dict) else {}
        )
        message_type = EnterpriseBaleService._normalize_chatwoot_message_type(
            payload.get("message_type")
        )
        nested_type = EnterpriseBaleService._normalize_chatwoot_message_type(
            message_obj.get("message_type")
        )
        event = str(event_name or "").strip().lower()

        if message_type == "outgoing" or nested_type == "outgoing":
            return True

        if event == "message_created" and (
            message_type == "template" or nested_type == "template"
        ):
            return True

        return False

    @staticmethod
    def _extract_chatwoot_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract outbound Chatwoot attachments."""
        direct = payload.get("attachments")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = (payload.get("message") or {}).get("attachments")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def _is_chatwoot_status_event(payload: dict[str, Any], event_name: str) -> bool:
        """Check whether a webhook payload is a conversation status event."""
        if event_name in {
            "conversation_status_changed",
            "conversation_resolved",
            "conversation_opened",
            "conversation_pending",
            "conversation_snoozed",
            "conversation_unsnoozed",
            "conversation_reopened",
        }:
            return True
        if event_name == "conversation_updated":
            return (
                EnterpriseBaleService._extract_status_from_changed_attributes(payload)
                is not None
            )
        if event_name:
            return False
        return (
            EnterpriseBaleService._extract_status_from_changed_attributes(payload)
            is not None
        )

    @staticmethod
    def _extract_chatwoot_status_name(payload: dict[str, Any]) -> Optional[str]:
        """Extract a normalized conversation status from a webhook payload."""
        conversation = (
            payload.get("conversation")
            if isinstance(payload.get("conversation"), dict)
            else {}
        )
        changed = EnterpriseBaleService._extract_status_from_changed_attributes(payload)
        candidates = [
            changed,
            payload.get("event"),
            payload.get("status"),
            conversation.get("status"),
        ]
        for value in candidates:
            status_name = EnterpriseBaleService._normalize_chatwoot_status(value)
            if status_name:
                return status_name
        return None

    @staticmethod
    def _extract_status_from_changed_attributes(
        payload: dict[str, Any],
    ) -> Optional[str]:
        """Extract status from changed_attributes containers."""
        return EnterpriseBaleService._extract_status_from_change_container(
            payload.get("changed_attributes")
        )

    @staticmethod
    def _extract_status_from_change_container(value: Any) -> Optional[str]:
        """Walk nested change containers to find a status value."""
        if isinstance(value, dict):
            if "status" in value:
                return EnterpriseBaleService._extract_status_from_change_value(
                    value.get("status")
                )
            for nested in value.values():
                status_name = (
                    EnterpriseBaleService._extract_status_from_change_container(nested)
                )
                if status_name:
                    return status_name
            return None
        if isinstance(value, list):
            for item in value:
                status_name = (
                    EnterpriseBaleService._extract_status_from_change_container(item)
                )
                if status_name:
                    return status_name
        return None

    @staticmethod
    def _extract_status_from_change_value(value: Any) -> Optional[str]:
        """Normalize status values from list/dict scalars."""
        if isinstance(value, list):
            if not value:
                return None
            return EnterpriseBaleService._normalize_chatwoot_status(value[-1])
        if isinstance(value, dict):
            for key in ("new", "current", "to", "after", "value"):
                if key in value:
                    status_name = EnterpriseBaleService._normalize_chatwoot_status(
                        value.get(key)
                    )
                    if status_name:
                        return status_name
            for nested in value.values():
                status_name = EnterpriseBaleService._extract_status_from_change_value(
                    nested
                )
                if status_name:
                    return status_name
            return None
        return EnterpriseBaleService._normalize_chatwoot_status(value)

    @staticmethod
    def _normalize_chatwoot_status(value: Any) -> Optional[str]:
        """Normalize Chatwoot status names from numeric or textual payloads."""
        mapping = {0: "open", 1: "resolved", 2: "pending", 3: "snoozed"}
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return mapping.get(value)
        text = str(value or "").strip().lower()
        if not text:
            return None
        if text.isdigit():
            return mapping.get(int(text))
        aliases = {
            "open": "open",
            "opened": "open",
            "reopened": "open",
            "conversation_opened": "open",
            "resolved": "resolved",
            "conversation_resolved": "resolved",
            "pending": "pending",
            "conversation_pending": "pending",
            "snoozed": "snoozed",
            "conversation_snoozed": "snoozed",
            "unsnoozed": "open",
            "conversation_unsnoozed": "open",
        }
        return aliases.get(text)

    @staticmethod
    def _extract_id(obj: Any) -> Optional[int]:
        """Extract a numeric id from a Chatwoot response object."""
        if isinstance(obj, dict):
            value = obj.get("id")
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    @staticmethod
    def _chatwoot_webhook_url(instance_key: str, route_key: str) -> str:
        """Build the expected Chatwoot webhook URL for an enterprise route inbox."""
        return (
            f"{settings.SERVER_BASE_URL.rstrip('/')}/api/v1/webhooks/chatwoot/"
            f"{str(instance_key).strip()}/enterprise/{str(route_key).strip()}"
        )

    @staticmethod
    def _build_chatwoot_api_inbox_payload(
        inbox_name: str, webhook_url: str
    ) -> dict[str, Any]:
        """Build a Chatwoot API inbox payload with an explicit callback webhook."""
        return {
            "name": str(inbox_name).strip(),
            "callback_webhook_url": str(webhook_url).strip(),
            "channel": {
                "type": "api",
                "webhook_url": str(webhook_url).strip(),
            },
        }

    async def _ensure_inbox_webhook_url(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        instance_key: str,
        inbox_obj: dict[str, Any],
        inbox_name: str,
        expected_webhook_url: str,
    ) -> tuple[dict[str, Any], bool]:
        """Repair stale Chatwoot callback webhook URLs on reused enterprise inboxes."""
        inbox_id = self._extract_id(inbox_obj) or self._extract_id(
            (inbox_obj or {}).get("payload")
        )
        if not inbox_id:
            return inbox_obj, False

        current_webhook_url = ChatwootClient.extract_inbox_webhook_url(inbox_obj)
        if str(current_webhook_url or "").strip() == str(expected_webhook_url).strip():
            return inbox_obj, False

        logger.warning(
            "repairing enterprise inbox webhook instance=%s inbox_id=%s inbox_name=%s old=%s new=%s",
            instance_key,
            inbox_id,
            inbox_name,
            current_webhook_url,
            expected_webhook_url,
        )

        updated = await client.update_inbox(
            account_id,
            int(inbox_id),
            {
                "name": str(inbox_name).strip(),
                "callback_webhook_url": str(expected_webhook_url).strip(),
            },
        )
        normalized = updated if isinstance(updated, dict) else dict(inbox_obj)
        normalized.setdefault("id", int(inbox_id))
        target = (
            normalized.get("payload")
            if isinstance(normalized.get("payload"), dict)
            else normalized
        )
        target["callback_webhook_url"] = str(expected_webhook_url).strip()
        channel = (
            target.get("channel") if isinstance(target.get("channel"), dict) else {}
        )
        channel["webhook_url"] = str(expected_webhook_url).strip()
        target["channel"] = channel
        return normalized, True

    @staticmethod
    def _is_missing_chatwoot_conversation(response: Optional[httpx.Response]) -> bool:
        """Identify deleted or missing Chatwoot conversation responses."""
        return EnterpriseBaleService._is_missing_chatwoot_resource(response)

    @staticmethod
    def _is_missing_chatwoot_contact(response: Optional[httpx.Response]) -> bool:
        """Identify deleted or missing Chatwoot contact responses."""
        return EnterpriseBaleService._is_missing_chatwoot_resource(response)

    @staticmethod
    def _is_missing_chatwoot_resource(response: Optional[httpx.Response]) -> bool:
        """Return True when a Chatwoot 404 response indicates a missing resource."""
        if response is None or response.status_code != 404:
            return False
        body = ""
        try:
            body = str(response.text or "").strip().lower()
        except Exception:
            body = ""
        return "resource could not be found" in body or "not found" in body
