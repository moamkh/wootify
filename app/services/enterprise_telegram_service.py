"""
Module Overview
---------------
Purpose: Service-layer business logic for Telegram Enterprise bot flows and Chatwoot routing.
Documentation Standard: module/class/public-method docstrings.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.clients.chatwoot_client import ChatwootClient
from app.config import settings
from app.connectors.registry import connector_registry
from app.models import (
    EnterpriseDocumentAssetType,
    EnterprisePendingMessageStatus,
    EnterpriseSessionStatus,
    EnterpriseTelegramPendingMessage,
    EnterpriseTelegramSession,
    EnterpriseTelegramUser,
)
from app.repositories.enterprise_document_asset_repository import (
    EnterpriseDocumentAssetRepository,
)
from app.repositories.enterprise_manual_group_repository import (
    EnterpriseManualGroupRepository,
)
from app.repositories.enterprise_telegram_pending_message_repository import (
    EnterpriseTelegramPendingMessageRepository,
)
from app.repositories.enterprise_telegram_session_repository import (
    EnterpriseTelegramSessionRepository,
)
from app.repositories.enterprise_telegram_user_repository import (
    EnterpriseTelegramUserRepository,
)
from app.services.enterprise_document_service import EnterpriseDocumentService
from app.services.instance_service import InstanceService
from app.utils.crypto_utils import encryptor

logger = logging.getLogger("app.services.enterprise_telegram")

# Persian defaults for user-facing text
WELCOME_TEXT = "به ربات دستیار شرکت خوش آمدید."
MENU_PROMPT_TEXT = "لطفا گزینه مورد نظر خود را انتخاب کنید."
ADDRESS_PROMPT_TEXT = "لطفا استان مورد نظر خود را انتخاب کنید."
NO_MANUALS_TEXT = "فایلی برای این بخش تنظیم نشده است."
NO_CATALOG_TEXT = "کاتالوگی برای این بخش تنظیم نشده است."
NOT_CONFIGURED_TEXT = "این بخش هنوز در پنل مدیریت تنظیم نشده است."
LIVE_MODE_RESUME_TEXT = "گفتگو ادامه دارد. پیام خود را ارسال کنید."
LIVE_SESSION_LOCKED_TEXT = (
    "در گفتگوی زنده فقط می‌توانید پیام خود را ارسال کنید یا «بازگشت به منو» را بزنید."
)
USER_MANUAL_LINK_TEMPLATE = (
    "برای دریافت راهنمای کاربری مورد نظر بر روی متن زیر ضربه بزنید:\n"
    "[{{user_manual_name}}]({{user_manual_url}})"
)

# Hardcoded Persian button defaults
BACK_LABEL = "بازگشت به منو"
CATALOG_LABEL = "کاتالوگ محصولات"
MANUALS_LABEL = "راهنمای کاربری محصولات"
ADDRESS_LABEL = "آدرس مراکز خدمات پس از فروش"
ADDRESS_TEHRAN_LABEL = "تهران و البرز"
ADDRESS_OTHER_LABEL = "مابقی استان‌ها"

# Known non-live states
STATE_ROOT = "root"
STATE_MANUAL_GROUP_MENU = "manual_group_menu"
STATE_MANUAL_MENU = "manual_menu"
STATE_ADDRESS_MENU = "address_menu"


class EnterpriseTelegramService:
    """Service for Telegram Enterprise runtime workflows."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._instances = InstanceService()
        self._users = EnterpriseTelegramUserRepository
        self._sessions = EnterpriseTelegramSessionRepository
        self._pending = EnterpriseTelegramPendingMessageRepository
        self._documents = EnterpriseDocumentService()
        self._clients: dict[str, ChatwootClient] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_platform_update(
        self, db: Session, instance_key: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle a single Telegram Enterprise update from the polling loop."""
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
        attachments = await self._extract_attachments(instance_key, message)

        command = self._normalize_command(text)
        live_session = self._active_live_session(db, user)
        if live_session:
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
            await self._show_root_menu(
                runtime.instance.instance_key, user, str(chat_id), platform_metadata=runtime.platform_metadata
            )
            db.commit()
            return {"message": "start_handled", "status": "ok"}

        if user.current_state in {STATE_MANUAL_GROUP_MENU, STATE_MANUAL_MENU}:
            handled = await self._handle_manual_menu(
                db, runtime, user, str(chat_id), text
            )
            db.commit()
            return handled

        if user.current_state == STATE_ADDRESS_MENU:
            handled = await self._handle_address_menu(
                db, runtime, user, str(chat_id), text
            )
            db.commit()
            return handled

        handled = await self._handle_root_actions(
            db, runtime, user, str(chat_id), text
        )
        db.commit()
        return handled

    async def receive_chatwoot_webhook(
        self, db: Session, instance_key: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle Chatwoot webhook events for Telegram Enterprise instances."""
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

            pending_row = EnterpriseTelegramPendingMessage(
                session_id=session.id,
                chatwoot_message_id=str(message_id) if message_id else None,
                text_payload=text or None,
                attachment_payload_json=attachments or None,
                status=EnterprisePendingMessageStatus.pending,
            )
            self._pending(db).save(pending_row)
            session.unread_count = int(session.unread_count or 0) + 1
            notify_text = self._route_text(
                runtime.platform_metadata, route_key, "unread"
            ) or self._not_configured_text(runtime.platform_metadata)
            if not session.unread_notice_sent:
                try:
                    await self._send_text(
                        instance_key,
                        user.platform_chat_id,
                        notify_text,
                        reply_markup=self._root_menu_markup(runtime.platform_metadata),
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
        """Create or discover a route-specific Chatwoot inbox for a Telegram Enterprise instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        route_cfg = self._require_route(runtime.platform_metadata, route_key)
        chatwoot = runtime.chatwoot
        account_id = chatwoot.get("account_id")
        inbox_name = str(route_cfg.get("inbox_name") or "").strip()
        if not account_id or not inbox_name:
            raise ValueError("chatwoot.account_id and the route inbox_name are required")

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
            routes = runtime.platform_metadata.get("enterprise_routes") or []
            for route in routes:
                if isinstance(route, dict) and route.get("route_key") == route_key:
                    route["inbox_id"] = int(inbox_id)
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
    ) -> list[EnterpriseTelegramSession]:
        """List enterprise sessions for an instance."""
        runtime = self._require_runtime_instance(db, instance_key)
        return self._sessions(db).list_by_instance(runtime.instance.id)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    async def _handle_root_actions(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle root menu actions."""
        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        normalized = self._normalize_action_text(text)

        # Dynamic route selection (match by display_name)
        routes = cfg.get("enterprise_routes") or []
        for route in routes:
            if isinstance(route, dict) and normalized == str(
                route.get("display_name") or ""
            ).strip():
                await self._enter_live_route(
                    db, runtime, user, chat_id, str(route["route_key"]).strip()
                )
                return {"message": "live_route_entered", "status": "ok"}

        manuals_label = self._label(
            cfg, "enterprise_manuals_button_label", MANUALS_LABEL
        )
        catalog_label = self._label(
            cfg, "enterprise_catalog_button_label", CATALOG_LABEL
        )
        address_label = self._label(
            cfg, "enterprise_address_button_label", ADDRESS_LABEL
        )

        if normalized == manuals_label:
            groups = EnterpriseManualGroupRepository(db).list_by_instance(
                runtime.instance.id, active_only=True
            )
            if groups:
                self._set_user_state(db, user, STATE_MANUAL_GROUP_MENU)
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
                    ),
                    reply_markup=self._manual_group_menu_markup(groups, cfg),
                )
                return {"message": "manual_group_menu_opened", "status": "ok"}
            else:
                self._set_user_state(db, user, STATE_MANUAL_MENU)
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
                    ),
                    reply_markup=self._manual_menu_markup(db, runtime.instance.id, cfg),
                )
                return {"message": "manual_menu_opened", "status": "ok"}

        if normalized == catalog_label:
            await self._send_catalog_and_root(db, runtime, user, chat_id)
            return {"message": "catalog_sent", "status": "ok"}

        if normalized == address_label:
            self._set_user_state(db, user, STATE_ADDRESS_MENU)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    cfg, "enterprise_address_prompt_text", ADDRESS_PROMPT_TEXT
                ),
                reply_markup=self._address_menu_markup(cfg),
            )
            return {"message": "address_menu_opened", "status": "ok"}

        await self._show_root_menu(
            runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
        )
        return {"message": "root_menu_resent", "detail": "unknown_action"}

    async def _handle_manual_menu(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle manual-menu and manual-group-menu selections."""
        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)

        if self._normalize_action_text(text) == back_label:
            user.current_group_id = None
            db.add(user)
            db.flush()
            await self._show_root_menu(
                runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
            )
            return {"message": "manual_menu_closed", "status": "ok"}

        if user.current_state == STATE_MANUAL_GROUP_MENU:
            groups = EnterpriseManualGroupRepository(db).list_by_instance(
                runtime.instance.id, active_only=True
            )
            selected_group = next(
                (g for g in groups if str(g.name).strip() == str(text or "").strip()),
                None,
            )
            if selected_group:
                user.current_group_id = selected_group.id
                self._set_user_state(db, user, STATE_MANUAL_MENU)
                manuals = EnterpriseDocumentAssetRepository(db).list_by_group(
                    selected_group.id, active_only=True
                )
                if not manuals:
                    await self._send_text(
                        runtime.instance.instance_key,
                        chat_id,
                        self._message_text(
                            cfg, "enterprise_no_manuals_text", NO_MANUALS_TEXT
                        ),
                        reply_markup=self._manual_menu_markup(
                            db, runtime.instance.id, cfg
                        ),
                    )
                else:
                    keyboard = [
                        [{"text": str(m.display_name or m.original_filename).strip()}]
                        for m in manuals
                    ]
                    keyboard.append([{"text": back_label}])
                    await self._send_text(
                        runtime.instance.instance_key,
                        chat_id,
                        self._message_text(
                            cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
                        ),
                        reply_markup={
                            "keyboard": keyboard,
                            "resize_keyboard": True,
                            "one_time_keyboard": True,
                        },
                    )
                return {"message": "manual_menu_opened_for_group", "status": "ok"}
            else:
                await self._send_text(
                    runtime.instance.instance_key,
                    chat_id,
                    self._message_text(
                        cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
                    ),
                    reply_markup=self._manual_group_menu_markup(groups, cfg),
                )
                return {"message": "group_not_found", "detail": "selection_invalid"}

        # manual_menu state
        if user.current_group_id:
            manuals = EnterpriseDocumentAssetRepository(db).list_by_group(
                user.current_group_id, active_only=True
            )
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
                        cfg, "enterprise_no_manuals_text", NO_MANUALS_TEXT
                    )
                    if not manuals
                    else self._message_text(
                        cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
                    )
                ),
                reply_markup=self._manual_menu_markup(db, runtime.instance.id, cfg),
            )
            return {"message": "manual_not_found", "detail": "selection_invalid"}

        resolved_link = str(selected.link_url or "").strip()
        if resolved_link:
            safe_url = resolved_link.replace(" ", "%20")
            display_name = (
                str(selected.display_name or "").strip()
                or str(selected.original_filename or "").strip()
                or safe_url
            )
            template = self._message_text(
                cfg,
                "enterprise_user_manual_link_template",
                USER_MANUAL_LINK_TEMPLATE,
            )
            message = (
                template.replace("{{user_manual_name}}", display_name)
                .replace("{{user_manual_url}}", safe_url)
            )
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                message,
                reply_markup=self._remove_keyboard_markup(),
            )
        else:
            asset_row, content = self._documents.read_asset_bytes(db, selected.id)
            await self._send_media(
                runtime.instance.instance_key,
                chat_id,
                content,
                asset_row.original_filename,
                caption=asset_row.display_name or None,
                reply_markup=self._remove_keyboard_markup(),
            )

        # Return to root after sending manual
        user.current_group_id = None
        db.add(user)
        db.flush()
        await self._show_root_menu(
            runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
        )
        return {"message": "manual_sent", "status": "ok"}

    async def _handle_address_menu(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        chat_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle address menu selections."""
        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)
        normalized = self._normalize_action_text(text)

        if normalized == back_label:
            await self._show_root_menu(
                runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
            )
            return {"message": "address_menu_closed", "status": "ok"}

        if normalized == ADDRESS_TEHRAN_LABEL:
            address_text = str(
                cfg.get("enterprise_address_tehran_alborz_text") or ""
            ).strip() or self._not_configured_text(cfg)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                address_text,
                reply_markup=self._remove_keyboard_markup(),
            )
            await self._show_root_menu(
                runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
            )
            return {"message": "address_sent", "status": "ok"}

        if normalized == ADDRESS_OTHER_LABEL:
            address_text = str(
                cfg.get("enterprise_address_other_provinces_text") or ""
            ).strip() or self._not_configured_text(cfg)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                address_text,
                reply_markup=self._remove_keyboard_markup(),
            )
            await self._show_root_menu(
                runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
            )
            return {"message": "address_sent", "status": "ok"}

        await self._send_text(
            runtime.instance.instance_key,
            chat_id,
            self._message_text(
                cfg, "enterprise_address_prompt_text", ADDRESS_PROMPT_TEXT
            ),
            reply_markup=self._address_menu_markup(cfg),
        )
        return {"message": "address_menu_resent", "detail": "unknown_selection"}

    async def _handle_live_session_menu_input(
        self,
        db: Session,
        *,
        runtime: Any,
        user: EnterpriseTelegramUser,
        session: EnterpriseTelegramSession,
        chat_id: str,
        text: str,
    ) -> Optional[dict[str, Any]]:
        """Block commands/menu selections while a live session is active."""
        normalized = self._normalize_action_text(text)
        if not normalized:
            return None

        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)

        if self._is_back_to_menu(normalized, back_label):
            await self._leave_live_route(
                runtime.instance.instance_key, user, session, chat_id, platform_metadata=cfg
            )
            return {"message": "left_live_route", "status": "ok"}

        command = self._normalize_command(normalized)
        if command and command not in {"/menu", "/back"}:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    cfg, "enterprise_live_session_locked_text", LIVE_SESSION_LOCKED_TEXT
                ),
                reply_markup=self._live_menu_markup(cfg),
            )
            return {"message": "live_session_restricted_input_blocked", "status": "ok"}

        known_labels = self._known_menu_button_labels(db, runtime.instance.id, cfg)
        if normalized in known_labels:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(
                    cfg, "enterprise_live_session_locked_text", LIVE_SESSION_LOCKED_TEXT
                ),
                reply_markup=self._live_menu_markup(cfg),
            )
            return {"message": "live_session_restricted_input_blocked", "status": "ok"}

        return None

    async def _enter_live_route(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        chat_id: str,
        route_key: str,
    ) -> None:
        """Enter or resume a route-specific live-chat session."""
        session, created_new = await self._get_or_open_route_session(
            db, runtime, user, route_key
        )
        self._set_user_state(db, user, route_key)

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
                    reply_markup=self._live_menu_markup(runtime.platform_metadata),
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
                        "enterprise_telegram._enter_live_route pending_delivery_failed instance=%s session_id=%s pending_id=%s error_type=%s error=%s",
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
            waiting_text = self._route_text(
                runtime.platform_metadata, route_key, "waiting"
            ) or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                waiting_text,
                reply_markup=self._live_menu_markup(runtime.platform_metadata),
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
            reply_markup=self._live_menu_markup(runtime.platform_metadata),
        )

    async def _leave_live_route(
        self,
        instance_key: str,
        user: EnterpriseTelegramUser,
        session: EnterpriseTelegramSession,
        chat_id: str,
        *,
        platform_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Leave a live route without resolving the Chatwoot conversation."""
        session.user_present = False
        session.status = EnterpriseSessionStatus.closed_by_user
        await self._show_root_menu(
            instance_key, user, chat_id, platform_metadata=platform_metadata
        )

    async def _forward_customer_message_to_chatwoot(
        self,
        db: Session,
        *,
        runtime: Any,
        user: EnterpriseTelegramUser,
        session: EnterpriseTelegramSession,
        text: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        """Post a customer message from Telegram Enterprise into Chatwoot."""
        session = await self._ensure_forwardable_route_session(db, runtime, user, session)
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
        session: EnterpriseTelegramSession,
        route_key: str,
        chat_id: str,
    ) -> None:
        """Send the accepted message once per session before operator content."""
        if session.accepted_notice_sent:
            return
        try:
            accepted_text = self._route_text(
                runtime.platform_metadata, route_key, "accepted"
            ) or self._not_configured_text(runtime.platform_metadata)
            await self._send_text(
                instance_key,
                chat_id,
                accepted_text,
                reply_markup=self._live_menu_markup(runtime.platform_metadata),
            )
            session.accepted_notice_sent = True
        except Exception as exc:
            logger.warning(
                "enterprise_telegram._ensure_accepted_notice failed instance=%s session_id=%s route=%s error_type=%s error=%s",
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
        """Deliver a Chatwoot operator payload to Telegram."""
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
                "enterprise_telegram._deliver_operator_payload failed instance=%s chat_id=%s error_type=%s error=%s",
                instance_key,
                chat_id,
                type(exc).__name__,
                str(exc),
            )

    async def _get_or_open_route_session(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        route_key: str,
    ) -> tuple[EnterpriseTelegramSession, bool]:
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

        row = EnterpriseTelegramSession(
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
        user: EnterpriseTelegramUser,
        session: EnterpriseTelegramSession,
    ) -> EnterpriseTelegramSession:
        """Validate a session is still forwardable; recreate if stale."""
        reusable = await self._resolve_reusable_route_session(db, runtime, session)
        if reusable is not None:
            return reusable
        replacement, _created_new = await self._get_or_open_route_session(
            db, runtime, user, str(session.route_key or "").strip()
        )
        return replacement

    async def _resolve_reusable_route_session(
        self,
        db: Session,
        runtime: Any,
        session: EnterpriseTelegramSession,
    ) -> Optional[EnterpriseTelegramSession]:
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
        session: EnterpriseTelegramSession,
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
            if self._is_missing_chatwoot_contact(exc.response):
                logger.info(
                    "enterprise contact missing remotely; route session will be recreated instance=%s account_id=%s contact_id=%s",
                    runtime.instance.instance_key,
                    account_id,
                    contact_id,
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
        user: EnterpriseTelegramUser,
        session: EnterpriseTelegramSession,
    ) -> EnterpriseTelegramSession:
        """Replace a missing Chatwoot conversation with a new route session."""
        session.status = EnterpriseSessionStatus.resolved
        session.user_present = False
        self._sessions(db).save(session)
        replacement, _created_new = await self._get_or_open_route_session(
            db, runtime, user, str(session.route_key or "").strip()
        )
        return replacement

    async def _ensure_route_inbox(
        self, db: Session, runtime: Any, route_key: str
    ) -> str:
        """Resolve or auto-create a route inbox."""
        route_cfg = self._require_route(runtime.platform_metadata, route_key)
        inbox_id = route_cfg.get("inbox_id")
        if inbox_id is not None and str(inbox_id).strip():
            return str(inbox_id)
        if bool(route_cfg.get("auto_create")):
            created = await self.create_route_inbox(
                db, runtime.instance.instance_key, route_key
            )
            resolved_id = created.get("inbox_id")
            if resolved_id:
                return str(resolved_id)
        raise ValueError(
            f"inbox_id is required for route {route_key} or auto_create must be enabled"
        )

    async def _get_or_create_contact(
        self,
        runtime: Any,
        user: EnterpriseTelegramUser,
        inbox_id: int,
    ) -> str:
        """Resolve a Chatwoot contact for a Telegram enterprise user."""
        client = self._get_chatwoot_client(runtime.chatwoot)
        account_id = int(runtime.chatwoot["account_id"])
        identifier = self._enterprise_source_id(
            runtime.instance.instance_key, user.platform_chat_id
        )
        resolved_name = str(user.display_name or user.platform_chat_id).strip() or str(
            user.platform_chat_id
        )

        current_contact = await self._find_contact_by_identifier(
            client, account_id, identifier
        )
        if current_contact:
            contact_id = self._extract_id(current_contact) or self._extract_id(
                (current_contact or {}).get("payload")
            )
            if not contact_id:
                raise RuntimeError("failed to resolve existing enterprise contact id")
            return str(contact_id)

        create_payload = {
            "inbox_id": int(inbox_id),
            "name": resolved_name,
            "identifier": identifier,
        }
        if user.phone_number:
            create_payload["phone_number"] = str(user.phone_number).strip()

        try:
            created = await client.create_contact(account_id, create_payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            retry_contact = await self._find_contact_by_identifier(
                client, account_id, identifier
            )
            if retry_contact:
                retry_id = self._extract_id(retry_contact) or self._extract_id(
                    (retry_contact or {}).get("payload")
                )
                if retry_id:
                    return str(retry_id)
            if user.phone_number and "phone_number" in create_payload:
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
            raise RuntimeError("failed to create enterprise contact")
        return str(contact_id)

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

    def _leave_live_session_if_needed(
        self, db: Session, user: EnterpriseTelegramUser
    ) -> None:
        """Close the current live session on explicit restart."""
        if not self._is_live_state(user.current_state):
            return
        session = self._sessions(db).get_unresolved_for_user_route(
            user.id, user.current_state
        )
        if not session:
            return
        session.user_present = False
        session.status = EnterpriseSessionStatus.closed_by_user
        self._sessions(db).save(session)

    def _active_live_session(
        self, db: Session, user: EnterpriseTelegramUser
    ) -> Optional[EnterpriseTelegramSession]:
        """Resolve the live session matching the user's current state (route_key)."""
        if not self._is_live_state(user.current_state):
            return None
        session = self._sessions(db).get_unresolved_for_user_route(
            user.id, user.current_state
        )
        if session:
            session.user_present = True
            self._sessions(db).save(session)
        return session

    async def _show_root_menu(
        self,
        instance_key: str,
        user: EnterpriseTelegramUser,
        chat_id: str,
        *,
        platform_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Render the root menu."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        user.current_state = STATE_ROOT
        user.current_group_id = None
        await self._send_text(
            instance_key,
            chat_id,
            self._message_text(
                cfg, "enterprise_menu_prompt_text", MENU_PROMPT_TEXT
            ),
            reply_markup=self._root_menu_markup(cfg),
        )

    async def _send_catalog_and_root(
        self,
        db: Session,
        runtime: Any,
        user: EnterpriseTelegramUser,
        chat_id: str,
    ) -> None:
        """Send the configured catalog and return to the root menu."""
        cfg = runtime.platform_metadata if isinstance(runtime.platform_metadata, dict) else {}
        catalog = self._documents.get_catalog(db, runtime.instance.instance_key)
        if not catalog:
            await self._send_text(
                runtime.instance.instance_key,
                chat_id,
                self._message_text(cfg, "enterprise_no_catalog_text", NO_CATALOG_TEXT),
                reply_markup=self._root_menu_markup(cfg),
            )
            return
        asset_row, content = self._documents.read_asset_bytes(db, catalog.id)
        await self._send_media(
            runtime.instance.instance_key,
            chat_id,
            content,
            asset_row.original_filename,
            caption=asset_row.display_name or None,
            reply_markup=self._remove_keyboard_markup(),
        )
        await self._show_root_menu(
            runtime.instance.instance_key, user, chat_id, platform_metadata=cfg
        )

    # ------------------------------------------------------------------
    # Keyboard markups
    # ------------------------------------------------------------------

    def _root_menu_markup(
        self, platform_metadata: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the root menu keyboard."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        manuals_label = self._label(
            cfg, "enterprise_manuals_button_label", MANUALS_LABEL
        )
        catalog_label = self._label(
            cfg, "enterprise_catalog_button_label", CATALOG_LABEL
        )
        address_label = self._label(
            cfg, "enterprise_address_button_label", ADDRESS_LABEL
        )

        keyboard = [
            [{"text": manuals_label}],
            [{"text": catalog_label}],
            [{"text": address_label}],
        ]
        routes = cfg.get("enterprise_routes") or []
        for route in routes:
            if isinstance(route, dict):
                display_name = str(route.get("display_name") or "").strip()
                if display_name:
                    keyboard.append([{"text": display_name}])

        return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

    def _address_menu_markup(
        self, platform_metadata: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the address-selection keyboard."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)
        return {
            "keyboard": [
                [{"text": ADDRESS_TEHRAN_LABEL}],
                [{"text": ADDRESS_OTHER_LABEL}],
                [{"text": back_label}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _live_menu_markup(
        self, platform_metadata: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the live-chat keyboard."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)
        return {
            "keyboard": [[{"text": back_label}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _manual_menu_markup(
        self,
        db: Session,
        instance_id: str,
        platform_metadata: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the manual-selection keyboard."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)
        manuals = EnterpriseDocumentAssetRepository(db).list_for_instance(
            instance_id,
            asset_type=EnterpriseDocumentAssetType.manual,
            active_only=True,
        )
        keyboard = [
            [{"text": str(m.display_name or m.original_filename).strip()}]
            for m in manuals
        ]
        keyboard.append([{"text": back_label}])
        return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

    def _manual_group_menu_markup(
        self,
        groups: list[Any],
        platform_metadata: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the manual-group selection keyboard."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        back_label = self._label(cfg, "enterprise_back_button_label", BACK_LABEL)
        keyboard = [[{"text": str(g.name).strip()}] for g in groups]
        keyboard.append([{"text": back_label}])
        return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": True}

    @staticmethod
    def _remove_keyboard_markup() -> dict[str, Any]:
        """Build a reply-markup payload that explicitly clears the current keyboard."""
        return {"remove_keyboard": True}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_live_state(self, state: str) -> bool:
        """Return whether a state string represents an active live-chat route."""
        return state not in {
            STATE_ROOT,
            STATE_MANUAL_GROUP_MENU,
            STATE_MANUAL_MENU,
            STATE_ADDRESS_MENU,
            "",
            None,
        }

    def _known_menu_button_labels(
        self,
        db: Session,
        instance_id: str,
        platform_metadata: Optional[dict[str, Any]],
    ) -> set[str]:
        """Collect visible enterprise keyboard labels that should never hit Chatwoot."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        labels: set[str] = set()
        for markup in (
            self._root_menu_markup(cfg),
            self._address_menu_markup(cfg),
            self._live_menu_markup(cfg),
        ):
            for row in markup.get("keyboard", []):
                for item in row:
                    text = item.get("text") if isinstance(item, dict) else str(item)
                    if text:
                        labels.add(str(text))
        groups = EnterpriseManualGroupRepository(db).list_by_instance(
            instance_id, active_only=True
        )
        for row in self._manual_group_menu_markup(groups, cfg).get("keyboard", []):
            for item in row:
                text = item.get("text") if isinstance(item, dict) else str(item)
                if text:
                    labels.add(str(text))
        manuals = EnterpriseDocumentAssetRepository(db).list_for_instance(
            instance_id,
            asset_type=EnterpriseDocumentAssetType.manual,
            active_only=True,
        )
        for m in manuals:
            labels.add(str(m.display_name or m.original_filename or "").strip())
        return labels

    def _get_or_create_user(
        self,
        db: Session,
        instance_id: str,
        platform_chat_id: str,
        display_name: Optional[str] = None,
    ) -> EnterpriseTelegramUser:
        """Resolve or create a Telegram enterprise user row."""
        repo = self._users(db)
        row = repo.get_by_platform_chat_id(instance_id, platform_chat_id)
        if not row:
            row = EnterpriseTelegramUser(
                instance_id=str(instance_id),
                platform_chat_id=str(platform_chat_id),
                display_name=str(display_name or "").strip() or None,
                current_state=STATE_ROOT,
            )
        elif display_name:
            row.display_name = str(display_name).strip() or row.display_name
        repo.save(row)
        return row

    def _set_user_state(
        self, db: Session, user: EnterpriseTelegramUser, state: str
    ) -> None:
        """Persist a user state transition."""
        user.current_state = str(state or STATE_ROOT)
        self._users(db).save(user)

    def _get_chatwoot_client(self, chatwoot: dict[str, Any]) -> ChatwootClient:
        """Get or create a cached Chatwoot client."""
        base_url = str(chatwoot.get("base_url") or "").strip()
        token = str(chatwoot.get("api_access_token") or "").strip()
        key = f"{base_url}|{token}"
        if key not in self._clients:
            self._clients[key] = ChatwootClient(base_url=base_url, token=token)
        return self._clients[key]

    def _require_runtime_instance(self, db: Session, instance_key: str):
        """Require a Telegram Enterprise runtime instance."""
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if not runtime:
            raise ValueError("instance not found")
        if str(runtime.platform_type.key or "").strip().lower() != "telegram_enterprise":
            raise ValueError("instance is not a telegram_enterprise instance")
        if not runtime.chatwoot.get("account_id"):
            raise ValueError("chatwoot.account_id is required")
        return runtime

    def _require_route(
        self, platform_metadata: dict[str, Any], route_key: str
    ) -> dict[str, Any]:
        """Resolve a configured enterprise route."""
        route = self._route_config(platform_metadata, route_key)
        if not route:
            raise ValueError(f"unsupported enterprise route {route_key}")
        return route

    def _route_text(
        self, platform_metadata: dict[str, Any], route_key: str, kind: str
    ) -> Optional[str]:
        """Resolve a route-specific configured text."""
        route = self._route_config(platform_metadata, route_key)
        if not route:
            return None
        key = f"{kind}_text"
        text = str((route or {}).get(key) or "").strip()
        return text or None

    @staticmethod
    def _route_config(
        platform_metadata: dict[str, Any], route_key: str
    ) -> Optional[dict[str, Any]]:
        """Find a route config by route_key in enterprise_routes array."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        routes = cfg.get("enterprise_routes") or []
        for route in routes:
            if isinstance(route, dict) and route.get("route_key") == route_key:
                return route
        return None

    @staticmethod
    def _message_text(
        platform_metadata: Optional[dict[str, Any]], field_name: str, default: str
    ) -> str:
        """Resolve a configurable enterprise message text with a static fallback."""
        text = str((platform_metadata or {}).get(field_name) or "").strip()
        return text or str(default or "").strip()

    @staticmethod
    def _label(
        platform_metadata: Optional[dict[str, Any]], field_name: str, default: str
    ) -> str:
        """Resolve a configurable button label with a static fallback."""
        text = str((platform_metadata or {}).get(field_name) or "").strip()
        return text or str(default or "").strip()

    @staticmethod
    def _not_configured_text(platform_metadata: Optional[dict[str, Any]]) -> str:
        """Resolve the configurable fallback for missing route/content configuration."""
        return EnterpriseTelegramService._message_text(
            platform_metadata,
            "enterprise_not_configured_text",
            NOT_CONFIGURED_TEXT,
        )

    async def _send_text(
        self, instance_key: str, chat_id: str, text: str, *, reply_markup: Any = False
    ) -> None:
        """Send text via the Telegram connector."""
        try:
            connector = connector_registry.get("telegram_enterprise")
            await connector.send_text(instance_key, chat_id, text, reply_markup=reply_markup)
        except Exception as exc:
            logger.warning(
                "enterprise_telegram._send_text failed instance=%s chat_id=%s text_len=%s error_type=%s error=%s",
                instance_key,
                chat_id,
                len(text or ""),
                type(exc).__name__,
                str(exc),
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
    ) -> None:
        """Send media via the Telegram connector."""
        try:
            connector = connector_registry.get("telegram_enterprise")
            await connector.send_media(
                instance_key,
                chat_id,
                media,
                filename,
                caption=caption,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning(
                "enterprise_telegram._send_media failed instance=%s chat_id=%s filename=%s error_type=%s error=%s",
                instance_key,
                chat_id,
                filename,
                type(exc).__name__,
                str(exc),
            )

    async def _extract_attachments(
        self, instance_key: str, message: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Download Telegram attachments into Chatwoot-ready payloads."""
        file_id, filename, content_type_hint = self._extract_file(message)
        if not file_id:
            return []
        connector = connector_registry.get("telegram_enterprise")
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
        """Extract a single Telegram attachment reference from a message."""
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
    def _extract_chat_id(message: dict[str, Any]) -> Optional[str]:
        """Extract a chat id from a Telegram update message."""
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        value = chat.get("id") or chat.get("username") or message.get("chat_id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_from_name(message: dict[str, Any]) -> Optional[str]:
        """Extract a sender display name from a Telegram update message."""
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
    def _is_back_to_menu(text: str, back_label: str) -> bool:
        """Check whether the user requested to leave the current menu or live session."""
        normalized = str(text or "").strip()
        return normalized == back_label or normalized in {"/menu", "/back"}

    @staticmethod
    def _enterprise_source_id(instance_key: str, chat_id: str) -> str:
        """Build a stable enterprise contact identifier."""
        return f"TELEGRAM_ENTERPRISE:{str(instance_key).strip()}:{str(chat_id).strip()}"

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
    def _is_forwardable_chatwoot_message(
        payload: dict[str, Any], event_name: str
    ) -> bool:
        """Decide whether an enterprise Chatwoot webhook should be forwarded to Telegram."""
        message_obj = (
            payload.get("message") if isinstance(payload.get("message"), dict) else {}
        )
        message_type = EnterpriseTelegramService._normalize_chatwoot_message_type(
            payload.get("message_type")
        )
        nested_type = EnterpriseTelegramService._normalize_chatwoot_message_type(
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
    def _normalize_chatwoot_message_type(value: Any) -> str:
        """Normalize Chatwoot webhook message_type values across enum/string shapes."""
        if isinstance(value, int):
            return {
                0: "incoming",
                1: "outgoing",
                2: "activity",
                3: "template",
            }.get(value, str(value))
        return str(value or "").strip().lower()

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
                EnterpriseTelegramService._extract_status_from_changed_attributes(payload)
                is not None
            )
        if event_name:
            return False
        return (
            EnterpriseTelegramService._extract_status_from_changed_attributes(payload)
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
        changed = EnterpriseTelegramService._extract_status_from_changed_attributes(
            payload
        )
        candidates = [
            changed,
            payload.get("event"),
            payload.get("status"),
            conversation.get("status"),
        ]
        for value in candidates:
            status_name = EnterpriseTelegramService._normalize_chatwoot_status(value)
            if status_name:
                return status_name
        return None

    @staticmethod
    def _extract_status_from_changed_attributes(
        payload: dict[str, Any],
    ) -> Optional[str]:
        """Extract status from changed_attributes containers."""
        return EnterpriseTelegramService._extract_status_from_change_container(
            payload.get("changed_attributes")
        )

    @staticmethod
    def _extract_status_from_change_container(value: Any) -> Optional[str]:
        """Walk nested change containers to find a status value."""
        if isinstance(value, dict):
            if "status" in value:
                return EnterpriseTelegramService._extract_status_from_change_value(
                    value.get("status")
                )
            for nested in value.values():
                status_name = EnterpriseTelegramService._extract_status_from_change_container(
                    nested
                )
                if status_name:
                    return status_name
            return None
        if isinstance(value, list):
            for item in value:
                status_name = EnterpriseTelegramService._extract_status_from_change_container(
                    item
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
            return EnterpriseTelegramService._normalize_chatwoot_status(value[-1])
        if isinstance(value, dict):
            for key in ("new", "current", "to", "after", "value"):
                if key in value:
                    status_name = EnterpriseTelegramService._normalize_chatwoot_status(
                        value.get(key)
                    )
                    if status_name:
                        return status_name
            return None
        return EnterpriseTelegramService._normalize_chatwoot_status(value)

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
    def _extract_id(obj: Any) -> Optional[str]:
        """Extract an id from a dict or scalar."""
        if isinstance(obj, dict):
            raw = obj.get("id")
            return str(raw) if raw is not None else None
        if obj is not None:
            return str(obj)
        return None

    @staticmethod
    def _is_missing_chatwoot_conversation(
        response: Optional[httpx.Response],
    ) -> bool:
        """Identify deleted or missing Chatwoot conversation responses."""
        return EnterpriseTelegramService._is_missing_chatwoot_resource(response)

    @staticmethod
    def _is_missing_chatwoot_contact(
        response: Optional[httpx.Response],
    ) -> bool:
        """Identify deleted or missing Chatwoot contact responses."""
        return EnterpriseTelegramService._is_missing_chatwoot_resource(response)

    @staticmethod
    def _is_missing_chatwoot_resource(
        response: Optional[httpx.Response],
    ) -> bool:
        """Identify deleted or missing Chatwoot resource responses."""
        if response is None or response.status_code != 404:
            return False
        body = ""
        try:
            body = str(response.text or "").strip().lower()
        except Exception:
            body = ""
        return "resource could not be found" in body or "not found" in body
