"""Cohesive platform synchronization workflows.

These objects own the iteration, rate limiting, and result accounting for the
legacy Bale-PV import operations.  The bridge service remains the compatibility
facade and supplies its existing collaborators for contact/conversation work.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.bridge")


class BalePvSyncWorkflow:
    """Synchronize Bale-PV contacts and dialogs into Chatwoot."""

    def __init__(self, service: Any) -> None:
        self._service = service

    async def sync_contacts(self, db: Session, instance_key: str, runtime: Any) -> dict[str, Any]:
        """Import all Bale contacts while preserving first-sync semantics."""
        from wootify.plugins.bale_pv.connector import bale_pv
        from wootify.infrastructure.persistence.repositories.runtime_state_repository import RuntimeStateRepository

        service = self._service
        platform_key = service._platform_key(runtime)
        if platform_key != "bale_pv_enterprise":
            return {"ok": False, "detail": "not_bale_pv_instance"}
        account_id = int(runtime.chatwoot.get("account_id", 0))
        inbox_id = int(runtime.chatwoot.get("inbox_id", 0))
        if not account_id or not inbox_id:
            return {"ok": False, "detail": "missing_account_or_inbox_id"}

        state_repo = RuntimeStateRepository(db)
        runtime_state = state_repo.get_or_create(str(runtime.instance.id))
        is_first_sync = runtime_state.contacts_first_synced_at is None
        skip_profile_sync = not is_first_sync
        await bale_pv.connect(instance_key, runtime.platform_metadata)
        result = await bale_pv.get_contacts(instance_key)
        if not result.get("ok"):
            return {"ok": False, "detail": result.get("description", "fetch_failed")}
        contacts = result.get("contacts", [])
        if not contacts:
            return {"ok": True, "created": 0, "updated": 0, "failed": 0, "detail": "no_contacts_found"}

        client = service._get_chatwoot_client(runtime.chatwoot)
        created = updated = failed = 0
        for contact in contacts:
            uid = contact.get("id")
            name = str(contact.get("name") or "").strip()
            if not uid:
                continue
            try:
                identifier = service._prefixed_identifier(platform_key, str(uid))
                found = await client.search_contacts(account_id, identifier)
                payload = found.get("payload") if isinstance(found, dict) else None
                was_existing = isinstance(payload, list) and payload and service._extract_id(payload[0])
                avatar_bytes: Optional[bytes] = None
                avatar_filename = "avatar.jpg"
                if is_first_sync and not was_existing:
                    try:
                        avatar_bytes, avatar_ct = await bale_pv.get_user_avatar_bytes(instance_key, int(uid))
                        if avatar_ct and "/" in avatar_ct:
                            ext = avatar_ct.split("/")[-1].split("+")[0]
                            if ext in ("jpeg", "jpg", "png", "gif", "webp"):
                                avatar_filename = f"avatar.{ext}"
                    except Exception as exc:
                        logger.debug("sync_bale_pv_contact_avatar_failed instance=%s uid=%s error=%s", instance_key, uid, exc)
                contact_id = await service._get_or_create_contact(
                    client, account_id=account_id, inbox_id=inbox_id, chat_id=str(uid),
                    platform_key=platform_key, from_name=name or None, first_name=name or None,
                    skip_profile_sync=skip_profile_sync,
                )
                if is_first_sync and avatar_bytes and contact_id:
                    try:
                        await client.update_contact_avatar(account_id, int(contact_id), avatar_bytes, filename=avatar_filename)
                    except Exception as exc:
                        logger.debug("sync_bale_pv_contact_avatar_update_failed instance=%s uid=%s error=%s", instance_key, uid, exc)
                if was_existing:
                    updated += 1
                else:
                    created += 1
            except Exception as exc:
                logger.warning("sync_bale_pv_contact_failed instance=%s uid=%s name=%s error=%s", instance_key, uid, name, exc)
                failed += 1
            await asyncio.sleep(2.0)
        if is_first_sync:
            runtime_state.contacts_first_synced_at = dt.datetime.now(dt.timezone.utc)
            state_repo.save(runtime_state)
            db.commit()
            logger.info("sync_bale_pv_contacts_first_sync_complete instance=%s total=%s created=%s updated=%s", instance_key, len(contacts), created, updated)
        return {"ok": True, "created": created, "updated": updated, "failed": failed, "total": len(contacts)}

    async def sync_dialogs(
        self, db: Session, instance_key: str, runtime: Any, *, load_history: bool = True, history_limit: int = 50
    ) -> dict[str, Any]:
        """Import Bale dialogs, conversations, and optional historical messages."""
        from wootify.plugins.bale_pv.connector import bale_pv

        service = self._service
        platform_key = service._platform_key(runtime)
        if platform_key != "bale_pv_enterprise":
            return {"ok": False, "detail": "not_bale_pv_instance"}
        account_id = int(runtime.chatwoot.get("account_id", 0))
        inbox_id = int(runtime.chatwoot.get("inbox_id", 0))
        if not account_id or not inbox_id:
            return {"ok": False, "detail": "missing_account_or_inbox_id"}
        await bale_pv.connect(instance_key, runtime.platform_metadata)
        result = await bale_pv.sync_bale_dialogs(instance_key, load_history=load_history, history_limit=history_limit)
        if not result.get("ok"):
            return {"ok": False, "detail": result.get("description", "load_dialogs_failed")}
        dialogs = result.get("dialogs", [])
        users_by_id = result.get("users_by_id", {})
        history_by_peer = result.get("history_by_peer", {}) if load_history else {}
        self_user_id = bale_pv.get_self_user_id(instance_key)
        client = service._get_chatwoot_client(runtime.chatwoot)
        created = updated = failed = messages_imported = 0
        for dlg in dialogs:
            peer_id = dlg.get("peer_id")
            peer_type = dlg.get("peer_type", 1)
            peer_type_label = dlg.get("peer_type_label", "user")
            display_name = dlg.get("display_name") or f"({peer_type_label}) {peer_id}"
            is_bot = dlg.get("is_bot", False)
            additional_attributes = {"bale_peer_type": peer_type_label, "bale_peer_id": peer_id, "bale_peer_type_code": peer_type, "bale_is_bot": is_bot}
            try:
                identifier = service._prefixed_identifier(platform_key, str(peer_id))
                found = await client.search_contacts(account_id, identifier)
                payload = found.get("payload") if isinstance(found, dict) else None
                was_existing = isinstance(payload, list) and payload and service._extract_id(payload[0])
                contact_id = await service._get_or_create_contact(
                    client, account_id=account_id, inbox_id=inbox_id, chat_id=str(peer_id),
                    platform_key=platform_key, from_name=display_name, first_name=display_name,
                    additional_attributes=additional_attributes,
                )
                conversation_id = await service._get_or_create_conversation_for_contact(
                    client, account_id=account_id, inbox_id=inbox_id, contact_id=contact_id
                )
                if conversation_id and load_history:
                    history = history_by_peer.get(f"{peer_type}|{peer_id}", [])
                    for msg in history:
                        await service._import_historical_message(
                            client, account_id=account_id, conversation_id=conversation_id, msg=msg,
                            self_user_id=self_user_id, peer_type_label=peer_type_label,
                        )
                        messages_imported += 1
                        await asyncio.sleep(0.2)
                if was_existing: updated += 1
                else: created += 1
            except Exception as exc:
                logger.warning("sync_bale_dialog_failed instance=%s peer=%s type=%s error=%s", instance_key, peer_id, peer_type_label, exc)
                failed += 1
            await asyncio.sleep(1.0)
        return {"ok": True, "created": created, "updated": updated, "failed": failed, "dialogs": len(dialogs), "messages_imported": messages_imported}
