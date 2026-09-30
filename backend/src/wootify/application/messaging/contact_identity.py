"""Recoverable contact resolution without owning Chatwoot contact.identifier.

Chatwoot's API channel creates a contact and its contact_inbox in one database
transaction, with a unique (inbox_id, source_id). A saved UUID for that inbox
association lets us reconcile a timed-out POST or a failed local commit. The
UUID is not written to the account-global contact.identifier or a WhatsApp inbox.
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wootify.application.messaging.payload_parser import MessagePayloadParser, ChatwootPayloadParser
from wootify.infrastructure.persistence.models import ContactCreation, Conversation
from wootify.infrastructure.persistence.repositories.contact_mapping_repository import (
    ContactMappingConflict, ContactMappingRepository,
)


def contact_scope(client: Any, account_id: int) -> str:
    base_url = client.base_url
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("contact_resolution_requires_chatwoot_base_url")
    return hashlib.sha256(f"{base_url.rstrip('/')}|{int(account_id)}".encode()).hexdigest()


class ContactIdentityService:
    """Fail closed on uncertain lookups; reconcile creations by a durable key."""

    @staticmethod
    def _contact(value: Any) -> dict[str, Any]:
        contact = MessagePayloadParser.extract_contact_payload(value)
        if not str(contact.get("id") or "").isdigit() or int(contact["id"]) <= 0:
            raise RuntimeError("invalid_chatwoot_contact_response")
        return contact

    async def _get(self, client: Any, account_id: int, contact_id: str) -> dict[str, Any] | None:
        try:
            contact = self._contact(await client.get_contact(account_id, int(contact_id)))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if str(contact["id"]) != str(contact_id):
            raise ContactMappingConflict("chatwoot_contact_response_id_mismatch")
        return contact

    async def _source(self, client: Any, account_id: int, inbox_id: int, source_id: str) -> dict[str, Any] | None:
        try:
            return self._contact(await client.get_contact_by_source(account_id, inbox_id, source_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def _exact(self, client: Any, account_id: int, query: str, field: str) -> dict[str, Any] | None:
        """Check every search page; lookup failure never means contact absent."""
        normalize = ChatwootPayloadParser.normalize_bale_pv_phone if field == "phone_number" else str
        expected = normalize(query)
        matches: dict[str, dict[str, Any]] = {}
        previous_ids: set[str] = set()
        for page in range(1, 1001):
            result = await client.search_contacts(account_id, query, page=page)
            rows = result.get("payload") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("invalid_chatwoot_contact_search_response")
            page_ids: set[str] = set()
            for value in rows:
                contact = self._contact(value)
                page_ids.add(str(contact["id"]))
                if normalize(contact.get(field) or "") == expected:
                    matches[str(contact["id"])] = contact
            if len(matches) > 1:
                raise ContactMappingConflict(f"ambiguous_exact_contact_{field}")
            # Chatwoot's account contact search uses 15 entries per page.
            if len(rows) < 15:
                return next(iter(matches.values()), None)
            if page_ids <= previous_ids:
                raise RuntimeError("chatwoot_contact_search_pagination_stalled")
            previous_ids.update(page_ids)
        raise RuntimeError("chatwoot_contact_search_page_limit")

    @staticmethod
    def _invalidate(db: Session, instance_id: str, peer_id: str, contact_id: str) -> None:
        mappings = ContactMappingRepository(db)
        current = mappings.get(instance_id, peer_id)
        if current and str(current.chatwoot_contact_id) == contact_id:
            mappings.delete(instance_id, peer_id)
        db.query(Conversation).filter(
            Conversation.instance_id == instance_id,
            Conversation.platform_conversation_id == peer_id,
            Conversation.chatwoot_contact_id == contact_id,
        ).update({Conversation.is_active: False}, synchronize_session="fetch")
        db.commit()

    @staticmethod
    def _finish(
        db: Session, instance_id: str, peer_id: str, scope: str,
        contact: dict[str, Any], peer_type: str | None, anchor_id: str | None = None,
    ) -> int:
        contact_id = str(contact["id"])
        try:
            ContactMappingRepository(db).save(
                instance_id, peer_id, contact_id,
                platform_contact_type=peer_type, chatwoot_scope=scope,
            )
            if anchor_id:
                db.get(ContactCreation, anchor_id).chatwoot_contact_id = contact_id
            db.commit()
        except Exception:
            db.rollback()
            raise
        return int(contact_id)

    @staticmethod
    def _reserve(db: Session, instance_id: str, peer_id: str, scope: str, inbox_id: int) -> ContactCreation:
        def existing() -> ContactCreation | None:
            return db.query(ContactCreation).filter_by(
                instance_id=instance_id, platform_contact_id=peer_id, chatwoot_scope=scope,
            ).one_or_none()

        anchor = existing()
        if anchor is None:
            try:
                with db.begin_nested():
                    anchor = ContactCreation(
                        instance_id=instance_id, platform_contact_id=peer_id,
                        chatwoot_scope=scope, chatwoot_inbox_id=str(inbox_id),
                    )
                    db.add(anchor)
                    db.flush()
            except IntegrityError:
                anchor = existing()
                if anchor is None:
                    raise
        # Commit the winning anchor before any remote request. Simultaneous
        # Enterprise routes share the first inbox selected for this peer.
        db.commit()
        return anchor

    async def resolve(
        self, db: Session, client: Any, *, instance_id: str, account_id: int,
        inbox_id: int, peer_id: str, payload: dict[str, Any],
        legacy_identifiers: tuple[str, ...] = (), peer_type: str | None = None,
    ) -> tuple[int, bool]:
        instance_id, peer_id = str(instance_id), str(peer_id)
        scope = contact_scope(client, account_id)
        mappings = ContactMappingRepository(db)
        missing: set[str] = set()
        mapped = mappings.get(instance_id, peer_id)
        if mapped:
            if mapped.chatwoot_scope and mapped.chatwoot_scope != scope:
                raise ContactMappingConflict("chatwoot_account_changed_for_contact_mapping")
            contact_id = str(mapped.chatwoot_contact_id)
            db.commit()
            contact = await self._get(client, account_id, contact_id)
            if contact:
                return self._finish(db, instance_id, peer_id, scope, contact, peer_type), False
            missing.add(contact_id)
            self._invalidate(db, instance_id, peer_id, contact_id)

        # Recover older releases from their instance-owned conversation rows.
        candidates = db.query(Conversation.chatwoot_contact_id).filter(
            Conversation.instance_id == instance_id,
            Conversation.platform_conversation_id == peer_id,
            Conversation.chatwoot_contact_id.isnot(None),
        ).order_by(Conversation.updated_at.desc()).all()
        db.commit()
        for (candidate_id,) in candidates:
            contact_id = str(candidate_id)
            if contact_id in missing:
                continue
            contact = await self._get(client, account_id, contact_id)
            if contact:
                return self._finish(db, instance_id, peer_id, scope, contact, peer_type), False
            missing.add(contact_id)
            self._invalidate(db, instance_id, peer_id, contact_id)

        anchor = self._reserve(db, instance_id, peer_id, scope, inbox_id)
        anchor_id, anchor_inbox = str(anchor.id), int(anchor.chatwoot_inbox_id)
        anchor_contact = str(anchor.chatwoot_contact_id or "")
        db.commit()
        recovered = await self._source(client, account_id, anchor_inbox, anchor_id)
        if recovered:
            return self._finish(db, instance_id, peer_id, scope, recovered, peer_type, anchor_id), False
        if anchor_contact and anchor_contact not in missing:
            recovered = await self._get(client, account_id, anchor_contact)
            if recovered:
                return self._finish(db, instance_id, peer_id, scope, recovered, peer_type, anchor_id), False

        # Read-only import of historical contact identifiers; no write-back.
        for identifier in dict.fromkeys(legacy_identifiers):
            contact = await self._exact(client, account_id, identifier, "identifier")
            if contact:
                return self._finish(db, instance_id, peer_id, scope, contact, peer_type, anchor_id), False

        phone = str(payload.get("phone_number") or "").strip()
        normalized_phone = ChatwootPayloadParser.normalize_bale_pv_phone(phone)
        phone_queries = tuple(dict.fromkeys((phone, f"+{normalized_phone}"))) if normalized_phone else ()
        if peer_type in ("group", "channel"):
            phone_queries = ()
        for query in phone_queries:
            contact = await self._exact(client, account_id, query, "phone_number")
            if contact:
                return self._finish(db, instance_id, peer_id, scope, contact, peer_type, anchor_id), False

        # Only API inboxes guarantee that a source-ID collision rolls back
        # contact creation. WhatsApp/email builders may rewrite their source ID.
        inbox = await client.get_inbox(account_id, anchor_inbox)
        if isinstance(inbox, dict) and isinstance(inbox.get("payload"), dict):
            inbox = inbox["payload"]
        if not isinstance(inbox, dict) or inbox.get("channel_type") != "Channel::Api":
            raise RuntimeError("contact_creation_requires_wootify_api_inbox")
        create_payload = {key: value for key, value in payload.items() if key != "identifier"}
        create_payload.update(inbox_id=anchor_inbox, source_id=anchor_id)
        try:
            created = self._contact(await client.create_contact(account_id, create_payload))
        except (httpx.HTTPError, RuntimeError):
            # The remote transaction may have committed even though its reply
            # was lost. Reconcile first; never convert a failed lookup to absent.
            recovered = await self._source(client, account_id, anchor_inbox, anchor_id)
            if recovered:
                return self._finish(db, instance_id, peer_id, scope, recovered, peer_type, anchor_id), False
            # Another connector might have won a unique-phone race.
            for query in phone_queries:
                contact = await self._exact(client, account_id, query, "phone_number")
                if contact:
                    return self._finish(db, instance_id, peer_id, scope, contact, peer_type, anchor_id), False
            raise
        return self._finish(db, instance_id, peer_id, scope, created, peer_type, anchor_id), True


contact_identity = ContactIdentityService()
