"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.clients.chatwoot_client import ChatwootClient
from app.config import settings
from app.connectors.registry import connector_registry
from app.models import (
    BalePvPhoneResolvedUser,
    Instance,
    MessageDirection,
    MessageKind,
    MessageStatus,
)
from app.repositories.conversation_runtime_state_repository import ConversationRuntimeStateRepository
from app.services.chatwoot_db_service import ChatwootDatabaseService
from app.services.conversation_mapping_service import ConversationMappingService
from app.services.instance_service import InstanceService
from app.services.message_mapping_service import MessageMappingService
from app.utils.cache_utils import TTLCache
from app.utils.crypto_utils import encryptor
from app.utils.payload_utils import sanitize_payload

logger = logging.getLogger('app.services.bridge')


class BridgeService:
    """Service for bridge domain workflows."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._clients: TTLCache[ChatwootClient] = TTLCache(maxsize=50, ttl=3600)
        self._db_services: dict[str, ChatwootDatabaseService] = {}
        self._instances = InstanceService()
        self._conversations = ConversationMappingService()
        self._conversation_runtime_repo = ConversationRuntimeStateRepository
        self._messages = MessageMappingService()
        self._status_notify_recent: TTLCache[bool] = TTLCache(maxsize=5000, ttl=300)

    async def create_chatwoot_inbox(self, db: Session, instance_key: str) -> dict[str, Any]:
        """Create chatwoot inbox."""
        runtime = self._require_runtime_instance(db, instance_key)
        chatwoot = runtime.chatwoot
        account_id = chatwoot.get('account_id')
        inbox_name = chatwoot.get('inbox_name')

        if not account_id or not inbox_name:
            raise ValueError('chatwoot.account_id and chatwoot.inbox_name are required')

        client = self._get_chatwoot_client(chatwoot)
        inboxes = await client.list_inboxes(int(account_id))
        payload = inboxes.get('payload') if isinstance(inboxes, dict) else None
        existing = None
        if isinstance(payload, list):
            existing = next((item for item in payload if item.get('name') == inbox_name), None)

        created = False
        webhook_updated = False
        inbox_obj = existing
        webhook_url = self._chatwoot_webhook_url(instance_key)
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
                inbox_name=str(inbox_name).strip(),
                expected_webhook_url=webhook_url,
            )

        inbox_id = self._extract_id(inbox_obj) or self._extract_id((inbox_obj or {}).get('payload'))
        if inbox_id:
            chatwoot['inbox_id'] = int(inbox_id)
            runtime.instance.chatwoot_config_encrypted = encryptor.encrypt_json(chatwoot)
            db.add(runtime.instance)
            db.commit()

        return {
            'created': created,
            'webhook_updated': webhook_updated,
            'inbox_id': int(inbox_id) if inbox_id else None,
            'inbox': inbox_obj,
        }

    async def chatwoot_contact_has_phone(self, db: Session, instance_key: str, chat_id: str) -> Optional[bool]:
        """Chatwoot contact has phone."""
        runtime = self._require_runtime_instance(db, instance_key)
        platform_key = self._platform_key(runtime)
        account_id = runtime.chatwoot.get('account_id')
        if not account_id:
            return None

        client = self._get_chatwoot_client(runtime.chatwoot)
        normalized_chat_id = str(chat_id or '').strip()
        if not normalized_chat_id:
            return None

        contact_payloads: list[dict[str, Any]] = []
        lookup_failed = False
        looked_up = False

        conversation = self._conversations.get_by_platform_id(db, runtime.instance.id, normalized_chat_id)
        known_contact_id = str(conversation.chatwoot_contact_id or '').strip() if conversation else ''
        if known_contact_id.isdigit():
            looked_up = True
            try:
                fetched = await client.get_contact(int(account_id), int(known_contact_id))
                payload = self._extract_contact_payload(fetched)
                if payload:
                    contact_payloads.append(payload)
            except Exception as exc:
                lookup_failed = True
                logger.warning(
                    'failed to fetch chatwoot contact for phone check instance=%s account_id=%s contact_id=%s error=%s',
                    instance_key,
                    account_id,
                    known_contact_id,
                    str(exc),
                )

        if not contact_payloads:
            looked_up = True
            identifier = self._prefixed_identifier(platform_key, normalized_chat_id)
            try:
                found = await client.search_contacts(int(account_id), identifier)
                rows = found.get('payload') if isinstance(found, dict) else None
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        payload = self._extract_contact_payload(row) or row
                        if payload:
                            contact_payloads.append(payload)
            except Exception as exc:
                lookup_failed = True
                logger.warning(
                    'failed to search chatwoot contact for phone check instance=%s account_id=%s identifier=%s error=%s',
                    instance_key,
                    account_id,
                    identifier,
                    str(exc),
                )

        for payload in contact_payloads:
            normalized_phone = self._normalize_phone_number(payload.get('phone_number'))
            if normalized_phone:
                return True

        if contact_payloads:
            return False
        if lookup_failed:
            return None
        if looked_up:
            return False
        return None

    async def receive_chatwoot_webhook(self, db: Session, instance_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Receive chatwoot webhook."""
        runtime = self._require_runtime_instance(db, instance_key)
        platform_key = self._platform_key(runtime)
        connector = connector_registry.get(platform_key)
        if not runtime.instance.is_enabled:
            return {'message': 'ignored', 'detail': 'instance_disabled'}

        event_name = str(payload.get('event') or '').strip().lower()
        if self._is_chatwoot_status_event(payload, event_name):
            return {'message': 'ignored', 'detail': 'status_event_ignored'}

        # Handle Chatwoot message deletion → propagate to Bale
        if event_name == 'message_updated' and self._is_chatwoot_message_deleted(payload):
            delete_result = await self._handle_chatwoot_message_delete(db, instance_key, payload, runtime, connector)
            return delete_result

        if payload.get('private'):
            return {'message': 'ignored', 'detail': 'private_message'}

        if not self._is_forwardable_chatwoot_message(payload, event_name):
            return {'message': 'ignored', 'detail': 'message_type_not_outgoing'}

        chatwoot_conversation_id = self._extract_chatwoot_conversation_id(payload)
        if not chatwoot_conversation_id:
            return {'message': 'ignored', 'detail': 'chatwoot_conversation_id_missing'}

        mapped_conversation = self._conversations.get_by_chatwoot_id(db, runtime.instance.id, str(chatwoot_conversation_id))
        mapped_destination = (
            str(mapped_conversation.platform_conversation_id).strip()
            if mapped_conversation and mapped_conversation.platform_conversation_id
            else None
        )
        contact_id = self._extract_contact_id(payload)
        extracted_destination, source_id = self._extract_destination(payload, platform_key=platform_key)
        if mapped_conversation and extracted_destination:
            if self._is_probably_platform_message_id(db, mapped_conversation.id, extracted_destination):
                logger.warning(
                    'ignoring extracted destination that matches message id chatwoot_conversation_id=%s extracted=%s',
                    chatwoot_conversation_id,
                    extracted_destination,
                )
                extracted_destination = None

        mapped_is_probably_message_id = bool(
            mapped_conversation
            and mapped_destination
            and self._is_probably_platform_message_id(db, mapped_conversation.id, mapped_destination)
        )
        if mapped_is_probably_message_id:
            logger.warning(
                'mapped destination looks like message id chatwoot_conversation_id=%s mapped=%s',
                chatwoot_conversation_id,
                mapped_destination,
            )

        mapped_candidate = None if mapped_is_probably_message_id else mapped_destination
        destination_chat_id = self._choose_destination_chat_id(mapped_candidate, extracted_destination)
        if mapped_is_probably_message_id and (not destination_chat_id or destination_chat_id == mapped_destination):
            inferred_destination = self._infer_destination_from_contact_history(db, runtime.instance.id, contact_id)
            if inferred_destination and inferred_destination != mapped_destination:
                logger.warning(
                    'destination recovered from mapped-message-id fallback chatwoot_conversation_id=%s old=%s new=%s',
                    chatwoot_conversation_id,
                    mapped_destination,
                    inferred_destination,
                )
                destination_chat_id = inferred_destination
        if destination_chat_id and self._looks_like_uuid(destination_chat_id):
            inferred_destination = self._infer_destination_from_contact_history(db, runtime.instance.id, contact_id)
            if inferred_destination:
                logger.warning(
                    'destination recovered from contact history chatwoot_conversation_id=%s old=%s new=%s',
                    chatwoot_conversation_id,
                    destination_chat_id,
                    inferred_destination,
                )
                destination_chat_id = inferred_destination
        if destination_chat_id and self._looks_like_uuid(destination_chat_id):
            logger.warning(
                'ignoring outgoing message due to uuid-like destination chatwoot_conversation_id=%s destination=%s source_id=%s',
                chatwoot_conversation_id,
                destination_chat_id,
                source_id,
            )
            return {'message': 'ignored', 'detail': 'destination_invalid'}
        if not destination_chat_id:
            return {'message': 'ignored', 'detail': 'destination_not_found'}

        # Safety check: never send messages to the self user's own Bale ID.
        # This prevents webhook replies from being routed back to the agent account.
        self_user_id = getattr(connector, 'get_self_user_id', lambda _i: None)(instance_key)
        if self_user_id is not None and str(destination_chat_id) == str(self_user_id):
            logger.warning(
                'ignoring outgoing message destined for self user instance=%s chatwoot_conversation_id=%s destination=%s self_uid=%s',
                instance_key,
                chatwoot_conversation_id,
                destination_chat_id,
                self_user_id,
            )
            return {'message': 'ignored', 'detail': 'destination_is_self_user'}

        if (
            mapped_destination
            and extracted_destination
            and mapped_destination != extracted_destination
            and not self._looks_like_uuid(str(extracted_destination))
        ):
            logger.warning(
                'destination mismatch for chatwoot_conversation_id=%s mapped=%s extracted=%s source_id=%s',
                chatwoot_conversation_id,
                mapped_destination,
                extracted_destination,
                source_id,
            )

        mapped_is_invalid = bool(
            mapped_destination
            and (
                self._looks_like_uuid(mapped_destination)
                or mapped_is_probably_message_id
            )
        ) if mapped_conversation else False
        persisted_destination = mapped_destination or destination_chat_id
        if mapped_conversation and mapped_destination and destination_chat_id != mapped_destination:
            if mapped_is_invalid:
                existing_target = self._conversations.get_by_platform_id(db, runtime.instance.id, destination_chat_id)
                if existing_target and existing_target.id != mapped_conversation.id:
                    logger.warning(
                        'destination remap skipped due to conflict for chatwoot_conversation_id=%s current=%s target=%s',
                        chatwoot_conversation_id,
                        mapped_destination,
                        destination_chat_id,
                    )
                    persisted_destination = mapped_destination
                else:
                    persisted_destination = destination_chat_id
            else:
                persisted_destination = mapped_destination

        conversation = self._conversations.upsert(
            db,
            instance_id=runtime.instance.id,
            platform_conversation_id=str(persisted_destination),
            chatwoot_conversation_id=str(chatwoot_conversation_id),
            chatwoot_contact_id=contact_id,
            chatwoot_inbox_id=str(runtime.chatwoot.get('inbox_id') or '') or None,
        )
        conversation_id = conversation.id

        chatwoot_message_id = self._extract_chatwoot_message_id(payload)
        parent_chatwoot_message_id = self._extract_parent_chatwoot_message_id(payload)

        if chatwoot_message_id:
            existing = self._messages.get_by_chatwoot_message_id(db, conversation.id, str(chatwoot_message_id))
            if existing and existing.status in {MessageStatus.sent, MessageStatus.skipped}:
                return {'message': 'duplicate', 'status': existing.status.value}

        reply_to_platform_message_id: Optional[str] = None
        if parent_chatwoot_message_id and runtime.feature_flags.get('reply_sync', False):
            reply_to_platform_message_id = self._messages.find_platform_parent_for_chatwoot_parent(
                db,
                conversation.id,
                str(parent_chatwoot_message_id),
            )
            if not reply_to_platform_message_id:
                self._messages.upsert(
                    db,
                    conversation_id=conversation.id,
                    direction=MessageDirection.chatwoot_to_platform,
                    message_kind=MessageKind.text,
                    status=MessageStatus.skipped,
                    chatwoot_message_id=str(chatwoot_message_id) if chatwoot_message_id else None,
                    chatwoot_parent_message_id=str(parent_chatwoot_message_id),
                    error_code='reply_parent_not_mapped',
                    error_detail='Parent Chatwoot message does not have a mapped platform message id',
                    chatwoot_payload_json=self._payload_or_none(runtime, payload),
                    platform_payload_json=None,
                )
                db.commit()
                return {'message': 'skipped', 'status': 'skipped', 'detail': 'reply_parent_not_mapped'}

        content = self._extract_chatwoot_message_text(payload)
        attachments = self._extract_attachments(payload)
        quoted = {'id': str(reply_to_platform_message_id)} if reply_to_platform_message_id else None

        if db.new or db.dirty or db.deleted:
            db.commit()

        await connector.connect(instance_key, runtime.platform_metadata, runtime.proxy)

        phone_access_hash: Optional[int] = None
        if platform_key == 'bale_pv_enterprise' and self._is_phone_number_destination(str(destination_chat_id)):
            resolved_user = await self._resolve_bale_pv_phone(
                db, runtime, connector, instance_key, str(destination_chat_id)
            )
            await self._update_chatwoot_contact_for_bale_pv_phone(
                runtime, contact_id, resolved_user, str(destination_chat_id)
            )
            resolved_chat_id = str(resolved_user['id'])
            if conversation.platform_conversation_id != resolved_chat_id:
                conversation.platform_conversation_id = resolved_chat_id
                db.commit()
            destination_chat_id = resolved_chat_id
            if resolved_user.get('access_hash'):
                phone_access_hash = int(resolved_user['access_hash'])

        operator_name = self._extract_chatwoot_operator_name(payload)
        operator_note_text, operator_state_row, operator_state_value = self._resolve_operator_notification(
            db,
            conversation_id=conversation_id,
            operator_name=operator_name,
        )

        platform_response: dict[str, Any] = {}
        message_kind = MessageKind.text
        try:
            if operator_note_text:
                try:
                    await connector.send_text(instance_key, str(destination_chat_id), operator_note_text)
                    operator_state_row.last_operator_name = operator_state_value
                    self._conversation_runtime_repo(db).save(operator_state_row)
                except Exception as exc:
                    logger.warning(
                        'failed to send operator notification instance=%s conversation_id=%s operator=%s error=%s',
                        instance_key,
                        chatwoot_conversation_id,
                        operator_state_value,
                        exc,
                    )

            if attachments:
                message_kind = MessageKind.media
                for index, attachment in enumerate(attachments):
                    media = attachment.get('data_url') or attachment.get('content')
                    if isinstance(media, str) and media.startswith('/'):
                        media = f"{runtime.chatwoot.get('base_url', '').rstrip('/')}{media}"
                    filename = self._normalize_filename_for_platform(
                        attachment.get('file_name') or attachment.get('filename'),
                        attachment.get('file_type') or attachment.get('content_type'),
                        media if isinstance(media, str) else None,
                    )
                    result = await connector.send_media(
                        instance_key,
                        str(destination_chat_id),
                        media,
                        filename,
                        caption=content or None,
                        quoted=quoted,
                        access_hash=phone_access_hash,
                    )
                    if index == 0:
                        platform_response = result if isinstance(result, dict) else {}
            else:
                result = await connector.send_text(
                    instance_key,
                    str(destination_chat_id),
                    content,
                    quoted=quoted,
                    access_hash=phone_access_hash,
                )
                platform_response = result if isinstance(result, dict) else {}

            platform_message_id = (platform_response or {}).get('id')
            self._messages.upsert(
                db,
                conversation_id=conversation_id,
                direction=MessageDirection.chatwoot_to_platform,
                message_kind=message_kind,
                status=MessageStatus.sent,
                chatwoot_message_id=str(chatwoot_message_id) if chatwoot_message_id else None,
                platform_message_id=str(platform_message_id) if platform_message_id else None,
                chatwoot_parent_message_id=str(parent_chatwoot_message_id) if parent_chatwoot_message_id else None,
                platform_parent_message_id=str(reply_to_platform_message_id) if reply_to_platform_message_id else None,
                chatwoot_payload_json=self._payload_or_none(runtime, payload),
                platform_payload_json=self._payload_or_none(runtime, platform_response),
            )
            db.commit()
            return {
                'message': 'sent',
                'status': 'sent',
                'chatwoot_message_id': str(chatwoot_message_id) if chatwoot_message_id else None,
                'platform_message_id': str(platform_message_id) if platform_message_id else None,
                'source_id': source_id,
            }
        except Exception as exc:
            self._messages.upsert(
                db,
                conversation_id=conversation_id,
                direction=MessageDirection.chatwoot_to_platform,
                message_kind=message_kind,
                status=MessageStatus.failed,
                chatwoot_message_id=str(chatwoot_message_id) if chatwoot_message_id else None,
                chatwoot_parent_message_id=str(parent_chatwoot_message_id) if parent_chatwoot_message_id else None,
                platform_parent_message_id=str(reply_to_platform_message_id) if reply_to_platform_message_id else None,
                error_code='send_failed',
                error_detail=str(exc),
                chatwoot_payload_json=self._payload_or_none(runtime, payload),
            )
            db.commit()
            return {
                'message': 'delivery_failed',
                'status': 'failed',
                'detail': str(exc) or type(exc).__name__,
                'chatwoot_message_id': str(chatwoot_message_id) if chatwoot_message_id else None,
                'source_id': source_id,
            }

    async def ingest_platform_event(self, db: Session, instance_key: str, event: dict[str, Any]) -> dict[str, Any]:
        """Ingest platform event."""
        runtime = self._require_runtime_instance(db, instance_key)
        platform_key = self._platform_key(runtime)
        if not runtime.instance.is_enabled:
            return {'message': 'ignored', 'detail': 'instance_disabled'}

        chat_id = str(event.get('chat_id') or '').strip()
        if not chat_id:
            raise ValueError('chat_id is required')

        platform_message_id = str(event.get('message_id') or event.get('platform_message_id') or '').strip() or None
        parent_platform_message_id = str(event.get('parent_platform_message_id') or '').strip() or None

        if not runtime.chatwoot.get('account_id'):
            raise ValueError('chatwoot.account_id is missing')
        if not runtime.chatwoot.get('inbox_id'):
            raise ValueError('chatwoot.inbox_id is missing')

        account_id = int(runtime.chatwoot['account_id'])
        inbox_id = int(runtime.chatwoot['inbox_id'])
        # For Bale PV (personal account), default to reopening conversations
        # because it's 1:1 messaging — not ticket-based support.
        raw_reopen = runtime.chatwoot.get('reopen_conversation')
        if raw_reopen is None and platform_key == 'bale_pv_enterprise':
            reopen_conversation = True
        else:
            reopen_conversation = bool(raw_reopen)
        client = self._get_chatwoot_client(runtime.chatwoot)
        event_contact = event.get('contact') if isinstance(event.get('contact'), dict) else {}
        event_text = str(event.get('text') or '').strip()
        shared_phone_number = self._normalize_phone_number(event_contact.get('phone_number'))
        if not shared_phone_number:
            shared_phone_number = self._extract_phone_from_shared_text(event_text)
        shared_first_name = str(event_contact.get('first_name') or '').strip() or None
        shared_last_name = str(event_contact.get('last_name') or '').strip() or None

        # Build additional_attributes for Chatwoot contact details
        additional_attributes: dict[str, Any] = {}
        display_first_name = shared_first_name or event.get('from_name') or None
        if display_first_name:
            additional_attributes['first_name'] = str(display_first_name).strip()
        if shared_last_name:
            additional_attributes['last_name'] = str(shared_last_name).strip()

        conversation = self._conversations.get_by_platform_id(db, runtime.instance.id, chat_id)
        if not conversation:
            contact_id = await self._get_or_create_contact(
                client,
                account_id=account_id,
                inbox_id=inbox_id,
                chat_id=chat_id,
                platform_key=platform_key,
                from_name=event.get('from_name'),
                phone_number=shared_phone_number,
                first_name=shared_first_name,
                last_name=shared_last_name,
                additional_attributes=additional_attributes if additional_attributes else None,
                chat_type=event.get('chat_type'),
            )
            remote_contact_conversations = await self._list_contact_conversations(
                client,
                account_id=account_id,
                contact_id=int(contact_id),
            )
            remote_inbox_conversations = [
                item
                for item in remote_contact_conversations
                if str(item.get('inbox_id') or '').strip() == str(inbox_id)
            ]
            reusable_remote_conversation = self._select_reusable_contact_conversation(
                remote_contact_conversations,
                inbox_id=inbox_id,
                reopen_conversation=reopen_conversation,
            )
            existing_contact_conversation = self._find_existing_contact_conversation(
                db,
                instance_id=runtime.instance.id,
                chatwoot_contact_id=str(contact_id),
                chatwoot_inbox_id=str(inbox_id),
                chat_id=chat_id,
            )
            if reusable_remote_conversation:
                chatwoot_conversation_id = self._extract_id(reusable_remote_conversation)
                if not chatwoot_conversation_id:
                    raise RuntimeError('Failed to resolve reusable Chatwoot conversation')
                logger.warning(
                    'reusing remote chatwoot conversation for contact instance=%s contact_id=%s chat_id=%s conversation_id=%s',
                    instance_key,
                    contact_id,
                    chat_id,
                    chatwoot_conversation_id,
                )
                await self._maybe_reopen_contact_conversation(
                    client,
                    account_id=account_id,
                    conversation_id=int(chatwoot_conversation_id),
                    conversation_payload=reusable_remote_conversation,
                    instance_key=instance_key,
                    reopen_conversation=reopen_conversation,
                )
            elif existing_contact_conversation and not remote_inbox_conversations:
                chatwoot_conversation_id = existing_contact_conversation.chatwoot_conversation_id
                logger.warning(
                    'reusing locally mapped chatwoot conversation for contact instance=%s contact_id=%s chat_id=%s conversation_id=%s',
                    instance_key,
                    contact_id,
                    chat_id,
                    chatwoot_conversation_id,
                )
            else:
                created = await client.create_conversation(
                    account_id,
                    {
                        'contact_id': str(contact_id),
                        'inbox_id': str(inbox_id),
                    },
                )
                chatwoot_conversation_id = self._extract_id(created) or self._extract_id((created or {}).get('payload'))
                if not chatwoot_conversation_id:
                    raise RuntimeError('Failed to create Chatwoot conversation')

            conversation = self._conversations.upsert(
                db,
                instance_id=runtime.instance.id,
                platform_conversation_id=chat_id,
                chatwoot_conversation_id=str(chatwoot_conversation_id),
                chatwoot_contact_id=str(contact_id),
                chatwoot_inbox_id=str(inbox_id),
            )
        else:
            chatwoot_conversation_id = conversation.chatwoot_conversation_id
            existing_contact_id = str(conversation.chatwoot_contact_id or '').strip()
            if not existing_contact_id.isdigit():
                resolved_contact_id = await self._get_or_create_contact(
                    client,
                    account_id=account_id,
                    inbox_id=inbox_id,
                    chat_id=chat_id,
                    platform_key=platform_key,
                    from_name=event.get('from_name'),
                    phone_number=shared_phone_number,
                    first_name=shared_first_name,
                    last_name=shared_last_name,
                    additional_attributes=additional_attributes if additional_attributes else None,
                    chat_type=event.get('chat_type'),
                )
                existing_contact_id = str(resolved_contact_id)
                conversation = self._conversations.upsert(
                    db,
                    instance_id=runtime.instance.id,
                    platform_conversation_id=chat_id,
                    chatwoot_conversation_id=str(chatwoot_conversation_id),
                    chatwoot_contact_id=existing_contact_id,
                    chatwoot_inbox_id=str(inbox_id),
                )
            if existing_contact_id.isdigit():
                # Re-sync contact name in case it changed on the platform side.
                try:
                    fetched_contact = await client.get_contact(account_id, int(existing_contact_id))
                    fetched_payload = self._extract_contact_payload(fetched_contact) or {}
                    await self._sync_contact_name_if_needed(
                        client,
                        account_id=account_id,
                        contact_id=int(existing_contact_id),
                        current_contact=fetched_payload,
                        new_name=self._resolve_contact_name_from_event(event, platform_key, chat_id),
                        additional_attributes=additional_attributes if additional_attributes else None,
                    )
                except Exception as exc:
                    logger.debug(
                        'failed to sync existing contact name instance=%s contact_id=%s error=%s',
                        instance_key,
                        existing_contact_id,
                        exc,
                    )
            if shared_phone_number and existing_contact_id.isdigit():
                await self._sync_contact_phone_if_needed(
                    client,
                    account_id=account_id,
                    contact_id=int(existing_contact_id),
                    current_contact={},
                    phone_number=shared_phone_number,
                    fallback_name=f'{self._source_prefix(platform_key).title()} {existing_contact_id}',
                )
            if existing_contact_id.isdigit():
                remote_contact_conversations = await self._list_contact_conversations(
                    client,
                    account_id=account_id,
                    contact_id=int(existing_contact_id),
                )
                mapped_remote_conversation = self._find_contact_conversation_by_id(
                    remote_contact_conversations,
                    conversation_id=int(chatwoot_conversation_id),
                    inbox_id=inbox_id,
                )
                mapped_remote_status = self._normalize_chatwoot_status(
                    mapped_remote_conversation.get('status') if mapped_remote_conversation else None
                )
                selected_remote_conversation = mapped_remote_conversation
                if not reopen_conversation and mapped_remote_status == 'resolved':
                    selected_remote_conversation = self._select_reusable_contact_conversation(
                        remote_contact_conversations,
                        inbox_id=inbox_id,
                        reopen_conversation=False,
                        excluded_conversation_id=str(chatwoot_conversation_id),
                    )
                    selected_remote_conversation_id = self._extract_id(selected_remote_conversation)
                    if selected_remote_conversation_id:
                        logger.warning(
                            'switching platform conversation to non-resolved chatwoot conversation instance=%s chat_id=%s old_conversation_id=%s new_conversation_id=%s',
                            instance_key,
                            chat_id,
                            chatwoot_conversation_id,
                            selected_remote_conversation_id,
                        )
                        chatwoot_conversation_id = str(selected_remote_conversation_id)
                        conversation = self._conversations.upsert(
                            db,
                            instance_id=runtime.instance.id,
                            platform_conversation_id=chat_id,
                            chatwoot_conversation_id=str(chatwoot_conversation_id),
                            chatwoot_contact_id=existing_contact_id,
                            chatwoot_inbox_id=str(inbox_id),
                        )
                    else:
                        created = await client.create_conversation(
                            account_id,
                            {
                                'contact_id': str(existing_contact_id),
                                'inbox_id': str(inbox_id),
                            },
                        )
                        new_chatwoot_conversation_id = self._extract_id(created) or self._extract_id((created or {}).get('payload'))
                        if not new_chatwoot_conversation_id:
                            raise RuntimeError('Failed to create Chatwoot conversation')
                        logger.warning(
                            'created new chatwoot conversation because current one is resolved and reopen is disabled instance=%s chat_id=%s old_conversation_id=%s new_conversation_id=%s',
                            instance_key,
                            chat_id,
                            chatwoot_conversation_id,
                            new_chatwoot_conversation_id,
                        )
                        chatwoot_conversation_id = str(new_chatwoot_conversation_id)
                        conversation = self._conversations.upsert(
                            db,
                            instance_id=runtime.instance.id,
                            platform_conversation_id=chat_id,
                            chatwoot_conversation_id=str(chatwoot_conversation_id),
                            chatwoot_contact_id=existing_contact_id,
                            chatwoot_inbox_id=str(inbox_id),
                        )
                        selected_remote_conversation = None
                else:
                    selected_remote_conversation = mapped_remote_conversation or self._select_reusable_contact_conversation(
                        remote_contact_conversations,
                        inbox_id=inbox_id,
                        reopen_conversation=reopen_conversation,
                    )
                    selected_remote_conversation_id = self._extract_id(selected_remote_conversation)
                    if selected_remote_conversation_id and str(selected_remote_conversation_id) != str(chatwoot_conversation_id):
                        logger.warning(
                            'remapping platform conversation to current chatwoot conversation instance=%s chat_id=%s old_conversation_id=%s new_conversation_id=%s',
                            instance_key,
                            chat_id,
                            chatwoot_conversation_id,
                            selected_remote_conversation_id,
                        )
                        chatwoot_conversation_id = str(selected_remote_conversation_id)
                        conversation = self._conversations.upsert(
                            db,
                            instance_id=runtime.instance.id,
                            platform_conversation_id=chat_id,
                            chatwoot_conversation_id=str(chatwoot_conversation_id),
                            chatwoot_contact_id=existing_contact_id,
                            chatwoot_inbox_id=str(inbox_id),
                        )
                await self._maybe_reopen_contact_conversation(
                    client,
                    account_id=account_id,
                    conversation_id=int(chatwoot_conversation_id),
                    conversation_payload=selected_remote_conversation,
                    instance_key=instance_key,
                    reopen_conversation=reopen_conversation,
                )
        conversation_id = conversation.id

        # Handle Bale → Chatwoot delete
        if event.get('_deleted') and platform_message_id:
            return await self._handle_platform_message_delete(
                db=db,
                instance_key=instance_key,
                runtime=runtime,
                conversation=conversation,
                platform_message_id=platform_message_id,
            )

        if platform_message_id:
            existing = self._messages.get_by_platform_message_id(db, conversation_id, platform_message_id)
            if existing and existing.status == MessageStatus.sent:
                # Detect Bale message edits: same platform_message_id but different content
                existing_text = ""
                if existing.platform_payload_json and isinstance(existing.platform_payload_json, dict):
                    existing_text = str(existing.platform_payload_json.get('text') or '')
                incoming_text = str(event.get('text') or '')
                if existing_text and incoming_text and existing_text != incoming_text:
                    edit_result = await self._handle_platform_message_edit(
                        db=db,
                        instance_key=instance_key,
                        runtime=runtime,
                        existing_mapping=existing,
                        new_text=incoming_text,
                        event=event,
                    )
                    return edit_result
                return {'message': 'duplicate', 'status': 'sent'}

        chatwoot_parent_message_id: Optional[str] = None
        if parent_platform_message_id and runtime.feature_flags.get('reply_sync', False):
            chatwoot_parent_message_id = self._messages.find_chatwoot_parent_for_platform_parent(
                db,
                conversation_id,
                parent_platform_message_id,
            )

        text = str(event.get('text') or '')
        source_id = connector_registry.prefixed_source_id(platform_key, chat_id)
        attachments = event.get('attachments') or []

        # Chatwoot requires non-empty content; use a fallback label for media-only messages
        content = text or ('📎 Attachment' if attachments else '')
        is_outgoing = bool(event.get('outgoing'))
        data: dict[str, Any] = {
            'content': content,
            'message_type': 'outgoing' if is_outgoing else 'incoming',
            'private': False,
            'source_id': source_id,
        }

        if chatwoot_parent_message_id and str(chatwoot_parent_message_id).isdigit():
            data['content_attributes'] = {'in_reply_to': int(chatwoot_parent_message_id)}
        if db.new or db.dirty or db.deleted:
            db.commit()
        message_kind = MessageKind.media if attachments else MessageKind.text
        conversation, chatwoot_conversation_id, response, chatwoot_parent_message_id = await self._post_platform_message_to_chatwoot(
            db=db,
            runtime=runtime,
            client=client,
            instance_key=instance_key,
            platform_key=platform_key,
            account_id=account_id,
            inbox_id=inbox_id,
            reopen_conversation=reopen_conversation,
            conversation=conversation,
            chat_id=chat_id,
            from_name=event.get('from_name'),
            shared_phone_number=shared_phone_number,
            shared_first_name=shared_first_name,
            shared_last_name=shared_last_name,
            additional_attributes=additional_attributes if additional_attributes else None,
            chatwoot_parent_message_id=chatwoot_parent_message_id,
            chat_type=event.get("chat_type"),
            data=data,
            attachments=attachments,
        )
        conversation_id = conversation.id

        chatwoot_message_id = self._extract_id(response) or self._extract_id((response or {}).get('payload'))
        self._messages.upsert(
            db,
            conversation_id=conversation_id,
            direction=MessageDirection.platform_to_chatwoot,
            message_kind=message_kind,
            status=MessageStatus.sent,
            chatwoot_message_id=str(chatwoot_message_id) if chatwoot_message_id else None,
            platform_message_id=platform_message_id,
            chatwoot_parent_message_id=chatwoot_parent_message_id,
            platform_parent_message_id=parent_platform_message_id,
            chatwoot_payload_json=self._payload_or_none(runtime, response),
            platform_payload_json=self._payload_or_none(runtime, event),
        )
        db.commit()

        return {
            'message': 'ingested',
            'status': 'sent',
            'chatwoot_conversation_id': str(chatwoot_conversation_id),
            'chatwoot_message_id': str(chatwoot_message_id) if chatwoot_message_id else None,
            'platform_message_id': platform_message_id,
        }

    async def _list_contact_conversations(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        contact_id: int,
    ) -> list[dict[str, Any]]:
        """Internal helper to list contact conversations safely."""
        try:
            response = await client.list_contact_conversations(account_id, contact_id)
        except Exception:
            logger.exception(
                'failed to list chatwoot contact conversations account_id=%s contact_id=%s',
                account_id,
                contact_id,
            )
            return []

        payload = response.get('payload') if isinstance(response, dict) else None
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _select_reusable_contact_conversation(
        self,
        conversations: list[dict[str, Any]],
        *,
        inbox_id: int,
        reopen_conversation: bool,
        excluded_conversation_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Internal helper to select a reusable Chatwoot conversation for a contact."""
        inbox_rows = [
            item
            for item in conversations
            if str(item.get('inbox_id') or '').strip() == str(inbox_id)
            and str(self._extract_id(item) or '').strip() != str(excluded_conversation_id or '').strip()
        ]
        if not inbox_rows:
            return None
        if reopen_conversation:
            return inbox_rows[0]

        for item in inbox_rows:
            if self._normalize_chatwoot_status(item.get('status')) != 'resolved':
                return item
        return None

    def _find_contact_conversation_by_id(
        self,
        conversations: list[dict[str, Any]],
        *,
        conversation_id: int,
        inbox_id: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Internal helper to find a Chatwoot conversation payload by id."""
        expected_id = str(conversation_id)
        expected_inbox = str(inbox_id) if inbox_id is not None else None
        for item in conversations:
            if str(self._extract_id(item) or '').strip() != expected_id:
                continue
            if expected_inbox is not None and str(item.get('inbox_id') or '').strip() != expected_inbox:
                continue
            return item
        return None

    async def _maybe_reopen_contact_conversation(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        conversation_id: int,
        conversation_payload: Optional[dict[str, Any]],
        instance_key: str,
        reopen_conversation: bool,
    ) -> None:
        """Internal helper to reopen a resolved Chatwoot conversation when configured."""
        if not reopen_conversation or not conversation_payload:
            return

        status_name = self._normalize_chatwoot_status(conversation_payload.get('status'))
        if status_name != 'resolved':
            return

        try:
            await client.toggle_conversation_status(account_id, conversation_id, 'open')
            logger.info(
                'reopened resolved chatwoot conversation instance=%s account_id=%s conversation_id=%s',
                instance_key,
                account_id,
                conversation_id,
            )
        except Exception:
            logger.exception(
                'failed to reopen resolved chatwoot conversation instance=%s account_id=%s conversation_id=%s',
                instance_key,
                account_id,
                conversation_id,
            )

    async def _post_platform_message_to_chatwoot(
        self,
        *,
        db: Session,
        runtime: Any,
        client: ChatwootClient,
        instance_key: str,
        platform_key: str,
        account_id: int,
        inbox_id: int,
        reopen_conversation: bool,
        conversation: Any,
        chat_id: str,
        from_name: Optional[str],
        shared_phone_number: Optional[str],
        shared_first_name: Optional[str],
        shared_last_name: Optional[str],
        additional_attributes: Optional[dict[str, Any]] = None,
        chatwoot_parent_message_id: Optional[str],
        chat_type: Optional[str] = None,
        data: dict[str, Any],
        attachments: list[Any],
    ) -> tuple[Any, str, Any, Optional[str]]:
        """Internal helper to post a platform message and recover from deleted Chatwoot conversations."""
        chatwoot_conversation_id = str(conversation.chatwoot_conversation_id or '').strip()
        try:
            response = await self._send_platform_message_to_chatwoot(
                client,
                account_id=account_id,
                chatwoot_conversation_id=chatwoot_conversation_id,
                data=data,
                attachments=attachments,
            )
            return conversation, chatwoot_conversation_id, response, chatwoot_parent_message_id
        except Exception as exc:
            if not self._is_chatwoot_missing_conversation_error(exc):
                raise

        recovered_conversation = await self._recover_deleted_chatwoot_conversation(
            db=db,
            runtime=runtime,
            client=client,
            instance_key=instance_key,
            platform_key=platform_key,
            account_id=account_id,
            inbox_id=inbox_id,
            reopen_conversation=reopen_conversation,
            conversation=conversation,
            chat_id=chat_id,
            from_name=from_name,
            shared_phone_number=shared_phone_number,
            shared_first_name=shared_first_name,
            shared_last_name=shared_last_name,
            additional_attributes=additional_attributes if additional_attributes else None,
            chat_type=chat_type,
        )
        recovered_conversation_id = str(recovered_conversation.chatwoot_conversation_id or '').strip()
        retry_data = dict(data)
        if recovered_conversation_id != chatwoot_conversation_id:
            retry_data.pop('content_attributes', None)
            chatwoot_parent_message_id = None
        response = await self._send_platform_message_to_chatwoot(
            client,
            account_id=account_id,
            chatwoot_conversation_id=recovered_conversation_id,
            data=retry_data,
            attachments=attachments,
        )
        return recovered_conversation, recovered_conversation_id, response, chatwoot_parent_message_id

    async def _send_platform_message_to_chatwoot(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        chatwoot_conversation_id: str,
        data: dict[str, Any],
        attachments: list[Any],
    ) -> Any:
        """Internal helper to send a platform message to a Chatwoot conversation."""
        if attachments:
            files = []
            for att in attachments:
                content = att.get('content')
                if not isinstance(content, (bytes, bytearray)):
                    continue
                normalized_name, normalized_type = self._normalize_attachment_for_chatwoot(
                    filename=att.get('filename'),
                    content_type=att.get('content_type'),
                    content=bytes(content),
                )
                files.append((normalized_name, bytes(content), normalized_type))
            return await client.post_message_with_attachments(
                account_id,
                int(chatwoot_conversation_id),
                data,
                files,
            )

        return await client.post_message(
            account_id,
            int(chatwoot_conversation_id),
            data,
        )

    async def _recover_deleted_chatwoot_conversation(
        self,
        *,
        db: Session,
        runtime: Any,
        client: ChatwootClient,
        instance_key: str,
        platform_key: str,
        account_id: int,
        inbox_id: int,
        reopen_conversation: bool,
        conversation: Any,
        chat_id: str,
        from_name: Optional[str],
        shared_phone_number: Optional[str],
        shared_first_name: Optional[str],
        shared_last_name: Optional[str],
        additional_attributes: Optional[dict[str, Any]] = None,
        chat_type: Optional[str] = None,
    ):
        """Internal helper to recover when a mapped Chatwoot conversation has been deleted."""
        previous_chatwoot_conversation_id = str(conversation.chatwoot_conversation_id or '').strip()
        existing_contact_id = str(conversation.chatwoot_contact_id or '').strip()

        if existing_contact_id.isdigit():
            contact_id = int(existing_contact_id)
            if shared_phone_number:
                await self._sync_contact_phone_if_needed(
                    client,
                    account_id=account_id,
                    contact_id=contact_id,
                    current_contact={},
                    phone_number=shared_phone_number,
                    fallback_name=f'{self._source_prefix(platform_key).title()} {contact_id}',
                )
        else:
            contact_id = await self._get_or_create_contact(
                client,
                account_id=account_id,
                inbox_id=inbox_id,
                chat_id=chat_id,
                platform_key=platform_key,
                from_name=from_name,
                phone_number=shared_phone_number,
                first_name=shared_first_name,
                last_name=shared_last_name,
                additional_attributes=additional_attributes,
                chat_type=chat_type,
            )

        remote_contact_conversations = await self._list_contact_conversations(
            client,
            account_id=account_id,
            contact_id=int(contact_id),
        )
        reusable_remote_conversation = self._select_reusable_contact_conversation(
            remote_contact_conversations,
            inbox_id=inbox_id,
            reopen_conversation=reopen_conversation,
            excluded_conversation_id=previous_chatwoot_conversation_id,
        )
        if reusable_remote_conversation:
            recovered_chatwoot_conversation_id = self._extract_id(reusable_remote_conversation)
            if not recovered_chatwoot_conversation_id:
                raise RuntimeError('Failed to resolve replacement Chatwoot conversation')
            await self._maybe_reopen_contact_conversation(
                client,
                account_id=account_id,
                conversation_id=int(recovered_chatwoot_conversation_id),
                conversation_payload=reusable_remote_conversation,
                instance_key=instance_key,
                reopen_conversation=reopen_conversation,
            )
        else:
            created = await client.create_conversation(
                account_id,
                {
                    'contact_id': str(contact_id),
                    'inbox_id': str(inbox_id),
                },
            )
            recovered_chatwoot_conversation_id = self._extract_id(created) or self._extract_id((created or {}).get('payload'))
            if not recovered_chatwoot_conversation_id:
                raise RuntimeError('Failed to create replacement Chatwoot conversation')

        recovered_conversation = self._conversations.upsert(
            db,
            instance_id=runtime.instance.id,
            platform_conversation_id=chat_id,
            chatwoot_conversation_id=str(recovered_chatwoot_conversation_id),
            chatwoot_contact_id=str(contact_id),
            chatwoot_inbox_id=str(inbox_id),
        )
        db.commit()
        logger.warning(
            'recovered deleted chatwoot conversation instance=%s chat_id=%s old_conversation_id=%s new_conversation_id=%s',
            instance_key,
            chat_id,
            previous_chatwoot_conversation_id,
            recovered_chatwoot_conversation_id,
        )
        return recovered_conversation

    async def sync_bale_pv_contacts(
        self,
        db: Session,
        instance_key: str,
        runtime: Any,
    ) -> dict[str, Any]:
        """Fetch all Bale PV contacts and ensure each exists in Chatwoot.

        On the first run (``contacts_first_synced_at`` is null), names, phones
        and avatars are synced. On later runs only missing contacts are created
        and phones are reconciled; names/avatars are left alone so manual edits
        in Chatwoot are preserved.

        Returns a summary dict with counts of created, updated, and failed contacts.
        """
        import datetime as _dt

        from app.connectors.bale_pv_connector import bale_pv
        from app.repositories.runtime_state_repository import RuntimeStateRepository

        platform_key = self._platform_key(runtime)
        if platform_key != 'bale_pv_enterprise':
            return {'ok': False, 'detail': 'not_bale_pv_instance'}

        account_id = int(runtime.chatwoot.get('account_id', 0))
        inbox_id = int(runtime.chatwoot.get('inbox_id', 0))
        if not account_id or not inbox_id:
            return {'ok': False, 'detail': 'missing_account_or_inbox_id'}

        state_repo = RuntimeStateRepository(db)
        runtime_state = state_repo.get_or_create(str(runtime.instance.id))
        is_first_sync = runtime_state.contacts_first_synced_at is None
        skip_profile_sync = not is_first_sync

        # Ensure connector is initialized
        await bale_pv.connect(instance_key, runtime.platform_metadata)
        result = await bale_pv.get_contacts(instance_key)
        if not result.get('ok'):
            return {'ok': False, 'detail': result.get('description', 'fetch_failed')}

        contacts = result.get('contacts', [])
        if not contacts:
            return {'ok': True, 'created': 0, 'updated': 0, 'failed': 0, 'detail': 'no_contacts_found'}

        client = self._get_chatwoot_client(runtime.chatwoot)
        created = 0
        updated = 0
        failed = 0

        import asyncio

        for contact in contacts:
            uid = contact.get('id')
            name = str(contact.get('name') or '').strip()
            if not uid:
                continue
            try:
                # Search by identifier first to determine if this is create or update
                identifier = self._prefixed_identifier(platform_key, str(uid))
                found = await client.search_contacts(account_id, identifier)
                payload = found.get('payload') if isinstance(found, dict) else None
                was_existing = isinstance(payload, list) and payload and self._extract_id(payload[0])

                avatar_bytes: Optional[bytes] = None
                avatar_filename = 'avatar.jpg'
                if is_first_sync and not was_existing:
                    try:
                        avatar_bytes, avatar_ct = await bale_pv.get_user_avatar_bytes(
                            instance_key, int(uid)
                        )
                        if avatar_ct and '/' in avatar_ct:
                            ext = avatar_ct.split('/')[-1].split('+')[0]
                            if ext in ('jpeg', 'jpg', 'png', 'gif', 'webp'):
                                avatar_filename = f"avatar.{ext}"
                    except Exception as exc:
                        logger.debug(
                            'sync_bale_pv_contact_avatar_failed instance=%s uid=%s error=%s',
                            instance_key,
                            uid,
                            exc,
                        )

                contact_id = await self._get_or_create_contact(
                    client,
                    account_id=account_id,
                    inbox_id=inbox_id,
                    chat_id=str(uid),
                    platform_key=platform_key,
                    from_name=name or None,
                    first_name=name or None,
                    skip_profile_sync=skip_profile_sync,
                )

                # On first sync, upload the Bale avatar for both new and existing
                # contacts. On later runs we skip avatars to preserve edits.
                if is_first_sync and avatar_bytes and contact_id:
                    try:
                        await client.update_contact_avatar(
                            account_id, int(contact_id), avatar_bytes, filename=avatar_filename
                        )
                    except Exception as exc:
                        logger.debug(
                            'sync_bale_pv_contact_avatar_update_failed instance=%s uid=%s error=%s',
                            instance_key,
                            uid,
                            exc,
                        )

                if was_existing:
                    updated += 1
                else:
                    created += 1
            except Exception as exc:
                logger.warning(
                    'sync_bale_pv_contact_failed instance=%s uid=%s name=%s error=%s',
                    instance_key,
                    uid,
                    name,
                    exc,
                )
                failed += 1
            # Rate-limit: pause 2.0s between contacts to avoid hammering Chatwoot
            await asyncio.sleep(2.0)

        if is_first_sync:
            runtime_state.contacts_first_synced_at = _dt.datetime.now(_dt.timezone.utc)
            state_repo.save(runtime_state)
            db.commit()
            logger.info(
                'sync_bale_pv_contacts_first_sync_complete instance=%s total=%s created=%s updated=%s',
                instance_key,
                len(contacts),
                created,
                updated,
            )

        return {
            'ok': True,
            'created': created,
            'updated': updated,
            'failed': failed,
            'total': len(contacts),
        }

    async def remove_bale_pv_contacts_from_chatwoot(
        self,
        db: Session,
        instance_key: str,
        runtime: Any,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete Chatwoot contacts whose identifier matches a Bale PV contact.

        Fetches the current Bale PV contact list and the set of Chatwoot contacts
        created from Bale PV (identifier prefix ``BALE_PV:``). Any Chatwoot contact
        whose UID is still present in the Bale contact list is deleted.

        Args:
            db: SQLAlchemy session.
            instance_key: Instance key.
            runtime: Runtime instance.
            dry_run: If True, report what would be deleted without deleting.

        Returns:
            Summary dict with ``deleted``, ``failed``, ``skipped``, ``total_bale``,
            ``total_chatwoot``, and ``dry_run`` flags.
        """
        from app.connectors.bale_pv_connector import bale_pv

        platform_key = self._platform_key(runtime)
        if platform_key != 'bale_pv_enterprise':
            return {'ok': False, 'detail': 'not_bale_pv_instance'}

        account_id = int(runtime.chatwoot.get('account_id', 0))
        inbox_id = int(runtime.chatwoot.get('inbox_id', 0))
        if not account_id or not inbox_id:
            return {'ok': False, 'detail': 'missing_account_or_inbox_id'}

        await bale_pv.connect(instance_key, runtime.platform_metadata)
        bale_result = await bale_pv.get_contacts(instance_key)
        if not bale_result.get('ok'):
            return {'ok': False, 'detail': bale_result.get('description', 'fetch_failed')}

        bale_uids = {
            str(contact.get('id')).strip()
            for contact in bale_result.get('contacts', [])
            if contact.get('id') is not None
        }
        if not bale_uids:
            return {
                'ok': True,
                'deleted': 0,
                'failed': 0,
                'skipped': 0,
                'total_bale': 0,
                'total_chatwoot': 0,
                'dry_run': dry_run,
                'detail': 'no_bale_contacts',
            }

        client = self._get_chatwoot_client(runtime.chatwoot)
        prefix = connector_registry.prefix(platform_key)
        deleted = 0
        failed = 0
        skipped = 0
        chatwoot_checked = 0
        page = 1

        import asyncio

        while True:
            try:
                search_result = await client.search_contacts(account_id, prefix, page=page)
            except Exception as exc:
                logger.warning(
                    'remove_bale_pv_contacts_search_failed instance=%s page=%s error=%s',
                    instance_key,
                    page,
                    exc,
                )
                return {
                    'ok': False,
                    'detail': f'search_failed_page_{page}',
                    'deleted': deleted,
                    'failed': failed,
                    'skipped': skipped,
                }

            payload = search_result.get('payload') if isinstance(search_result, dict) else None
            if not isinstance(payload, list):
                break

            for contact in payload:
                chatwoot_checked += 1
                if not isinstance(contact, dict):
                    continue
                identifier = str(contact.get('identifier') or '').strip()
                contact_id = self._extract_id(contact)
                if not identifier or not contact_id:
                    continue

                # Only consider contacts with our prefix
                if not identifier.startswith(f'{prefix}:'):
                    continue

                uid = identifier.split(':', 1)[1]
                if uid not in bale_uids:
                    skipped += 1
                    continue

                if dry_run:
                    deleted += 1
                    continue

                try:
                    await client.delete_contact(account_id, int(contact_id))
                    deleted += 1
                    logger.info(
                        'remove_bale_pv_contact_deleted instance=%s contact_id=%s uid=%s',
                        instance_key,
                        contact_id,
                        uid,
                    )
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        'remove_bale_pv_contact_failed instance=%s contact_id=%s uid=%s error=%s',
                        instance_key,
                        contact_id,
                        uid,
                        exc,
                    )
                await asyncio.sleep(0.5)

            meta = search_result.get('meta') if isinstance(search_result, dict) else None
            total_pages = meta.get('total_pages') if isinstance(meta, dict) else None
            if total_pages and page >= int(total_pages):
                break
            if len(payload) == 0:
                break
            page += 1

        return {
            'ok': True,
            'deleted': deleted,
            'failed': failed,
            'skipped': skipped,
            'total_bale': len(bale_uids),
            'total_chatwoot': chatwoot_checked,
            'dry_run': dry_run,
        }

    async def sync_bale_dialogs_to_chatwoot(
        self,
        db: Session,
        instance_key: str,
        runtime: Any,
        *,
        load_history: bool = True,
        history_limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch Bale dialogs and sync them (with optional history) to Chatwoot.

        Contact naming convention:
          - regular user:  '(user) Name'
          - bot:           '(bot) Name'
          - group:         '(group) Title'
          - channel:       '(channel) Title'

        Group/channel historical messages are prefixed with the sender name so
        individual members are distinguishable in Chatwoot.
        """
        from app.connectors.bale_pv_connector import bale_pv

        platform_key = self._platform_key(runtime)
        if platform_key != 'bale_pv_enterprise':
            return {'ok': False, 'detail': 'not_bale_pv_instance'}

        account_id = int(runtime.chatwoot.get('account_id', 0))
        inbox_id = int(runtime.chatwoot.get('inbox_id', 0))
        if not account_id or not inbox_id:
            return {'ok': False, 'detail': 'missing_account_or_inbox_id'}

        await bale_pv.connect(instance_key, runtime.platform_metadata)
        result = await bale_pv.sync_bale_dialogs(
            instance_key,
            load_history=load_history,
            history_limit=history_limit,
        )
        if not result.get('ok'):
            return {'ok': False, 'detail': result.get('description', 'load_dialogs_failed')}

        dialogs = result.get('dialogs', [])
        users_by_id = result.get('users_by_id', {})
        history_by_peer = result.get('history_by_peer', {}) if load_history else {}
        self_user_id = bale_pv.get_self_user_id(instance_key)

        client = self._get_chatwoot_client(runtime.chatwoot)
        created = 0
        updated = 0
        failed = 0
        messages_imported = 0

        import asyncio

        for dlg in dialogs:
            peer_id = dlg.get('peer_id')
            peer_type = dlg.get('peer_type', 1)
            peer_type_label = dlg.get('peer_type_label', 'user')
            display_name = dlg.get('display_name') or f'({peer_type_label}) {peer_id}'
            raw_name = dlg.get('raw_name') or str(peer_id)
            is_bot = dlg.get('is_bot', False)

            additional_attributes = {
                'bale_peer_type': peer_type_label,
                'bale_peer_id': peer_id,
                'bale_peer_type_code': peer_type,
                'bale_is_bot': is_bot,
            }

            try:
                identifier = self._prefixed_identifier(platform_key, str(peer_id))
                found = await client.search_contacts(account_id, identifier)
                payload = found.get('payload') if isinstance(found, dict) else None
                was_existing = isinstance(payload, list) and payload and self._extract_id(payload[0])

                contact_id = await self._get_or_create_contact(
                    client,
                    account_id=account_id,
                    inbox_id=inbox_id,
                    chat_id=str(peer_id),
                    platform_key=platform_key,
                    from_name=display_name,
                    first_name=display_name,
                    additional_attributes=additional_attributes,
                )

                # Find or create conversation
                conversation_id = await self._get_or_create_conversation_for_contact(
                    client,
                    account_id=account_id,
                    inbox_id=inbox_id,
                    contact_id=contact_id,
                )

                if conversation_id and load_history:
                    peer_key = f'{peer_type}|{peer_id}'
                    history = history_by_peer.get(peer_key, [])
                    for msg in history:
                        await self._import_historical_message(
                            client,
                            account_id=account_id,
                            conversation_id=conversation_id,
                            msg=msg,
                            self_user_id=self_user_id,
                            peer_type_label=peer_type_label,
                        )
                        messages_imported += 1
                        await asyncio.sleep(0.2)

                if was_existing:
                    updated += 1
                else:
                    created += 1
            except Exception as exc:
                logger.warning(
                    'sync_bale_dialog_failed instance=%s peer=%s type=%s error=%s',
                    instance_key,
                    peer_id,
                    peer_type_label,
                    exc,
                )
                failed += 1
            await asyncio.sleep(1.0)

        return {
            'ok': True,
            'created': created,
            'updated': updated,
            'failed': failed,
            'dialogs': len(dialogs),
            'messages_imported': messages_imported,
        }

    async def _get_or_create_conversation_for_contact(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        inbox_id: int,
        contact_id: int,
    ) -> Optional[int]:
        """Find an existing conversation for a contact in an inbox, or create one."""
        try:
            resp = await client.list_contact_conversations(account_id, contact_id)
            payload = resp.get('payload') if isinstance(resp, dict) else None
            if isinstance(payload, list):
                for conv in payload:
                    if not isinstance(conv, dict):
                        continue
                    if conv.get('inbox_id') == inbox_id:
                        cid = self._extract_id(conv)
                        if cid:
                            return int(cid)
        except Exception:
            pass

        try:
            created = await client.create_conversation(
                account_id,
                {
                    'inbox_id': inbox_id,
                    'contact_id': contact_id,
                    'status': 'open',
                },
            )
            cid = self._extract_id(created) or self._extract_id((created or {}).get('payload'))
            if cid:
                return int(cid)
        except Exception as exc:
            logger.warning('create_conversation_failed account_id=%s contact_id=%s error=%s', account_id, contact_id, exc)
        return None

    async def _import_historical_message(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        conversation_id: int,
        msg: dict[str, Any],
        self_user_id: Optional[int],
        peer_type_label: str,
    ) -> None:
        """Post a single Bale historical message into a Chatwoot conversation."""
        content = ''
        message_data = msg.get('message') or {}
        if message_data.get('type') == 'text':
            content = message_data.get('text') or ''
        elif message_data.get('type') == 'document':
            parts = []
            if message_data.get('caption'):
                parts.append(message_data['caption'])
            name = message_data.get('name') or 'file'
            parts.append(f'[Attachment: {name}]')
            content = '\n'.join(parts)

        if not content:
            return

        sender_uid = msg.get('sender_uid')
        sender_name = msg.get('sender_name') or str(sender_uid)
        is_outgoing = self_user_id is not None and sender_uid == self_user_id

        # For groups/channels, prefix with sender name so members are distinguishable
        if peer_type_label in ('group', 'channel') and not is_outgoing:
            content = f'{sender_name}: {content}'

        data = {
            'content': content,
            'message_type': 'outgoing' if is_outgoing else 'incoming',
            'private': False,
        }
        await client.post_message(account_id, conversation_id, data)

    async def _get_or_create_contact(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        inbox_id: int,
        chat_id: str,
        platform_key: str,
        from_name: Optional[str],
        phone_number: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        additional_attributes: Optional[dict[str, Any]] = None,
        chat_type: Optional[str] = None,
        skip_profile_sync: bool = False,
    ) -> int:
        """Internal helper to get or create contact.

        When ``skip_profile_sync`` is True, existing contacts are not renamed
        and additional attributes are not refreshed; only phone sync and
        missing-contact creation are performed.
        """
        identifier = self._prefixed_identifier(platform_key, chat_id)
        normalized_phone = self._normalize_phone_number(phone_number)

        chat_kind = str(chat_type or 'user').strip().lower()
        if chat_kind in ('group', 'channel'):
            label = 'Group' if chat_kind == 'group' else 'Channel'
            title = str(first_name or from_name or '').strip()
            # Avoid using a raw numeric ID as the display name.
            if not title or title == str(chat_id):
                source = self._source_prefix(platform_key).title()
                resolved_name = f'{source} {label} {chat_id}'
            elif (
                f'({chat_kind})' in title
                or f'({label.lower()})' in title
                or title.lower().startswith(label.lower())
            ):
                # Already carries a group/channel label (e.g. sync_bale_dialogs format)
                resolved_name = title
            else:
                resolved_name = f'({label.lower()}) {title}'
        else:
            resolved_name = str(first_name or from_name or chat_id).strip() or str(chat_id)
            # Avoid using a raw numeric ID as the display name.
            if resolved_name == str(chat_id) and str(chat_id).isdigit():
                source = self._source_prefix(platform_key).title()
                resolved_name = f'{source} User {chat_id}'
        try:
            found = await client.search_contacts(account_id, identifier)
            payload = found.get('payload') if isinstance(found, dict) else None
            if isinstance(payload, list) and payload:
                first = payload[0] if isinstance(payload[0], dict) else {}
                cid = self._extract_id(first)
                if cid:
                    if not skip_profile_sync:
                        await self._sync_contact_name_if_needed(
                            client,
                            account_id=account_id,
                            contact_id=int(cid),
                            current_contact=first,
                            new_name=resolved_name,
                            additional_attributes=additional_attributes,
                        )
                    await self._sync_contact_phone_if_needed(
                        client,
                        account_id=account_id,
                        contact_id=int(cid),
                        current_contact=first,
                        phone_number=normalized_phone,
                        fallback_name=resolved_name,
                    )
                    return int(cid)
        except Exception:
            pass

        if normalized_phone:
            phone_contact = await self._find_contact_by_phone(
                client,
                account_id=account_id,
                phone_number=normalized_phone,
            )
            phone_contact_id = self._extract_id(phone_contact)
            if phone_contact_id:
                logger.info(
                    'reusing chatwoot contact by phone account_id=%s contact_id=%s phone=%s',
                    account_id,
                    phone_contact_id,
                    normalized_phone,
                )
                return int(phone_contact_id)

        create_payload: dict[str, Any] = {
            'inbox_id': int(inbox_id),
            'name': resolved_name,
            'identifier': identifier,
        }
        if normalized_phone:
            create_payload['phone_number'] = normalized_phone
        if first_name:
            create_payload['name'] = str(first_name).strip()
            if last_name:
                create_payload['name'] = f"{create_payload['name']} {str(last_name).strip()}".strip()
        if additional_attributes:
            create_payload['additional_attributes'] = additional_attributes

        created = await client.create_contact(
            account_id,
            create_payload,
        )
        cid = self._extract_id(created) or self._extract_id((created or {}).get('payload'))
        if not cid:
            cid = self._extract_id(((created or {}).get('payload') or {}).get('contact'))
        if not cid:
            raise RuntimeError('Failed to create Chatwoot contact')
        await self._sync_contact_phone_if_needed(
            client,
            account_id=account_id,
            contact_id=int(cid),
            current_contact=(created.get('payload') if isinstance(created, dict) else {}) or {},
            phone_number=normalized_phone,
            fallback_name=resolved_name,
        )
        return int(cid)

    async def _sync_contact_name_if_needed(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        contact_id: int,
        current_contact: dict[str, Any],
        new_name: str,
        additional_attributes: Optional[dict[str, Any]] = None,
    ) -> None:
        """Update contact name when it has changed on the platform side.

        Updates in two cases:
        1. Current Chatwoot name is generic (raw ID, "Bale User 123", etc.).
        2. Current name differs from the new resolved name and the new name is
           not generic (i.e. a real name/username was discovered).

        This keeps Chatwoot contacts in sync with Bale display-name changes
        without overwriting manually-set names with generic fallbacks.
        """
        current_name = str(current_contact.get('name') or '').strip()
        if not new_name or new_name == current_name:
            return

        generic_pattern = re.compile(r'^(Bale\s+)?(User|Group|Channel)\s+\d+$', re.IGNORECASE)
        is_generic_name = lambda value: (
            not value
            or value.isdigit()
            or bool(generic_pattern.match(value))
            or re.fullmatch(r'\(group\)\s+\d+|\(channel\)\s+\d+', value, re.IGNORECASE) is not None
            or re.fullmatch(r'User\s+\d+', value, re.IGNORECASE) is not None
        )

        is_current_generic = is_generic_name(current_name)
        is_new_generic = is_generic_name(new_name)

        # Never overwrite with a generic fallback.
        if is_new_generic:
            return

        # Update if current is generic or if the platform name actually changed.
        if not is_current_generic and current_name:
            logger.info(
                'chatwoot contact name changed account_id=%s contact_id=%s old_name=%s new_name=%s',
                account_id,
                contact_id,
                current_name,
                new_name,
            )

        update_payload: dict[str, Any] = {'name': new_name}
        if additional_attributes:
            update_payload['additional_attributes'] = additional_attributes
        try:
            await client.update_contact(account_id, int(contact_id), update_payload)
            logger.info(
                'updated chatwoot contact name account_id=%s contact_id=%s old_name=%s new_name=%s',
                account_id,
                contact_id,
                current_name,
                new_name,
            )
        except Exception as exc:
            logger.warning(
                'failed to update chatwoot contact name account_id=%s contact_id=%s error=%s',
                account_id,
                contact_id,
                str(exc),
            )

    async def _sync_contact_phone_if_needed(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        contact_id: int,
        current_contact: dict[str, Any],
        phone_number: Optional[str],
        fallback_name: Optional[str] = None,
    ) -> None:
        """Internal helper to sync contact phone if needed."""
        normalized_phone = self._normalize_phone_number(phone_number)
        if not normalized_phone:
            return

        payload = self._extract_contact_payload(current_contact)
        if not payload:
            try:
                fetched = await client.get_contact(account_id, int(contact_id))
                payload = self._extract_contact_payload(fetched)
            except Exception as exc:
                logger.warning(
                    'failed to load chatwoot contact before phone update account_id=%s contact_id=%s error=%s',
                    account_id,
                    contact_id,
                    str(exc),
                )
                payload = {}

        current_phone = self._normalize_phone_number(payload.get('phone_number'))
        if current_phone and current_phone.lstrip('+') == normalized_phone.lstrip('+'):
            return

        conflicting_contact = await self._find_contact_by_phone(
            client,
            account_id=account_id,
            phone_number=normalized_phone,
        )
        conflicting_contact_id = self._extract_id(conflicting_contact)
        if conflicting_contact_id and int(conflicting_contact_id) != int(contact_id):
            logger.info(
                'skipping chatwoot contact phone update because phone belongs to another contact account_id=%s contact_id=%s conflicting_contact_id=%s phone=%s',
                account_id,
                contact_id,
                conflicting_contact_id,
                normalized_phone,
            )
            return

        update_payload = self._build_contact_update_payload(
            payload,
            normalized_phone,
            fallback_name=str(fallback_name or '').strip() or f'Contact {contact_id}',
        )
        try:
            await client.update_contact(
                account_id,
                int(contact_id),
                update_payload,
            )
        except Exception as exc:
            if self._is_chatwoot_duplicate_phone_error(exc):
                logger.info(
                    'skipping chatwoot contact phone update because phone is already assigned account_id=%s contact_id=%s phone=%s',
                    account_id,
                    contact_id,
                    normalized_phone,
                )
                return
            plus_candidate = self._to_plus_phone_candidate(normalized_phone)
            if plus_candidate and plus_candidate != normalized_phone:
                retry_payload = dict(update_payload)
                retry_payload['phone_number'] = plus_candidate
                try:
                    await client.update_contact(
                        account_id,
                        int(contact_id),
                        retry_payload,
                    )
                    logger.info(
                        'updated chatwoot contact phone with + prefix account_id=%s contact_id=%s phone=%s',
                        account_id,
                        contact_id,
                        plus_candidate,
                    )
                    return
                except Exception as retry_exc:
                    logger.warning(
                        'failed to update chatwoot contact phone account_id=%s contact_id=%s error=%s retry_error=%s',
                        account_id,
                        contact_id,
                        str(exc),
                        str(retry_exc),
                    )
                    return
            logger.warning(
                'failed to update chatwoot contact phone account_id=%s contact_id=%s error=%s',
                account_id,
                contact_id,
                str(exc),
            )

    async def _find_contact_by_phone(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        phone_number: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Internal helper to find a Chatwoot contact by phone number."""
        normalized_phone = self._normalize_phone_number(phone_number)
        if not normalized_phone:
            return None

        seen_queries: set[str] = set()
        for query in self._phone_search_candidates(normalized_phone):
            if query in seen_queries:
                continue
            seen_queries.add(query)
            try:
                found = await client.search_contacts(account_id, query)
            except Exception as exc:
                logger.warning(
                    'failed to search chatwoot contacts by phone account_id=%s query=%s error=%s',
                    account_id,
                    query,
                    str(exc),
                )
                continue

            payload = found.get('payload') if isinstance(found, dict) else None
            if not isinstance(payload, list):
                continue

            for item in payload:
                contact_payload = self._extract_contact_payload(item) or (item if isinstance(item, dict) else {})
                candidate_phone = self._normalize_phone_number(contact_payload.get('phone_number'))
                if candidate_phone and candidate_phone.lstrip('+') == normalized_phone.lstrip('+'):
                    return contact_payload
            # Small pause between phone-search candidates to avoid burst requests
            await asyncio.sleep(0.8)

        return None

    def _phone_search_candidates(self, phone_number: str) -> list[str]:
        """Internal helper to build search candidates for Chatwoot phone lookups."""
        normalized_phone = self._normalize_phone_number(phone_number)
        if not normalized_phone:
            return []

        digits = re.sub(r'\D', '', normalized_phone)
        candidates = [normalized_phone]
        plus_candidate = self._to_plus_phone_candidate(normalized_phone)
        if plus_candidate:
            candidates.append(plus_candidate)
        if digits:
            candidates.append(digits)
        return candidates

    @staticmethod
    def _is_chatwoot_duplicate_phone_error(exc: Exception) -> bool:
        """Internal helper to detect duplicate-phone Chatwoot validation errors."""
        if not isinstance(exc, httpx.HTTPStatusError):
            return False

        response = exc.response
        if response is None or response.status_code != 422:
            return False

        try:
            payload = response.json()
        except Exception:
            return False

        if not isinstance(payload, dict):
            return False

        message = str(payload.get('message') or '').strip().lower()
        attributes = payload.get('attributes')
        normalized_attributes = {str(item).strip().lower() for item in attributes} if isinstance(attributes, list) else set()
        return (
            'phone number' in message
            and 'already been taken' in message
            and 'phone_number' in normalized_attributes
        )

    @staticmethod
    def _is_chatwoot_missing_conversation_error(exc: Exception) -> bool:
        """Internal helper to detect deleted or missing Chatwoot conversation errors."""
        if not isinstance(exc, httpx.HTTPStatusError):
            return False

        response = exc.response
        if response is None or response.status_code not in {404, 410}:
            return False

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            message = ' '.join(str(payload.get(key) or '') for key in ('message', 'error', 'description')).strip().lower()
            if not message:
                return True
            return (
                'conversation' in message
                or 'record' in message
                or 'not found' in message
                or 'could not be found' in message
                or 'resource could not be found' in message
            )

        return True

    def _require_runtime_instance(self, db: Session, instance_key: str):
        """Internal helper to require runtime instance."""
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if not runtime:
            raise ValueError(f"Instance '{instance_key}' not found")
        return runtime

    def _payload_or_none(self, runtime, value: Any) -> Optional[dict[str, Any]]:
        """Internal helper to payload or none."""
        if not runtime.feature_flags.get('payload_debug_store', False):
            return None
        if isinstance(value, dict):
            return sanitize_payload(value)
        return {'value': sanitize_payload(value)}

    def _get_chatwoot_client(self, chatwoot_cfg: dict[str, Any]) -> ChatwootClient:
        """Internal helper to get chatwoot client."""
        base_url = str(chatwoot_cfg.get('base_url') or settings.CHATWOOT_BASE_URL).rstrip('/')
        token = str(chatwoot_cfg.get('api_access_token') or settings.CHATWOOT_API_TOKEN).strip()
        key = f'{base_url}::{token}'
        client = self._clients.get(key)
        if client is None:
            client = ChatwootClient(base_url=base_url, token=token)
            self._clients.set(key, client)
        return client

    def _get_chatwoot_db_service(
        self,
        chatwoot_cfg: dict[str, Any],
    ) -> Optional[ChatwootDatabaseService]:
        """Return a direct DB accessor if credentials are configured.

        Expects ``chatwoot_cfg['db_direct']`` to be a dict with:
        ``host``, ``port``, ``database``, ``user``, ``password``.
        """
        db_cfg = chatwoot_cfg.get('db_direct')
        if not isinstance(db_cfg, dict):
            return None
        host = str(db_cfg.get('host') or '').strip()
        if not host:
            return None
        key = f"{host}:{db_cfg.get('port', 5432)}/{db_cfg.get('database', 'chatwoot_production')}"
        svc = self._db_services.get(key)
        if svc is None:
            svc = ChatwootDatabaseService(
                host=host,
                port=int(db_cfg.get('port', 5432)),
                database=str(db_cfg.get('database', 'chatwoot_production')).strip(),
                user=str(db_cfg.get('user', '')).strip(),
                password=str(db_cfg.get('password', '')).strip(),
            )
            self._db_services[key] = svc
        return svc

    async def _handle_platform_message_delete(
        self,
        *,
        db: Session,
        instance_key: str,
        runtime: Any,
        conversation: Any,
        platform_message_id: str,
    ) -> dict[str, Any]:
        """Sync a Bale message delete to Chatwoot via the REST API."""
        existing = self._messages.get_by_platform_message_id(db, conversation.id, platform_message_id)
        if not existing or not existing.chatwoot_message_id:
            logger.warning(
                'platform_delete_no_mapping instance=%s platform_message_id=%s',
                instance_key,
                platform_message_id,
            )
            return {'message': 'ignored', 'status': 'delete_ignored', 'detail': 'no_mapping'}

        chatwoot_message_id = str(existing.chatwoot_message_id).strip()
        if not chatwoot_message_id.isdigit():
            return {'message': 'ignored', 'status': 'delete_ignored', 'detail': 'invalid_chatwoot_id'}

        client = self._get_chatwoot_client(runtime.chatwoot)
        account_id = int(runtime.chatwoot.get('account_id', 0))
        chatwoot_conversation_id = str(conversation.chatwoot_conversation_id or '').strip()
        if not chatwoot_conversation_id.isdigit():
            return {'message': 'ignored', 'status': 'delete_ignored', 'detail': 'invalid_conversation_id'}

        try:
            await client.delete_message(
                account_id=account_id,
                conversation_id=int(chatwoot_conversation_id),
                message_id=int(chatwoot_message_id),
            )
            logger.info(
                'platform_delete_synced instance=%s chatwoot_message_id=%s platform_message_id=%s',
                instance_key,
                chatwoot_message_id,
                platform_message_id,
            )
            return {
                'message': 'deleted',
                'status': 'synced',
                'chatwoot_message_id': chatwoot_message_id,
                'platform_message_id': platform_message_id,
            }
        except Exception as exc:
            logger.warning(
                'platform_delete_failed instance=%s chatwoot_message_id=%s error=%s',
                instance_key,
                chatwoot_message_id,
                exc,
            )
            return {'message': 'failed', 'status': 'delete_failed', 'detail': str(exc)}

    async def _handle_platform_message_edit(
        self,
        *,
        db: Session,
        instance_key: str,
        runtime: Any,
        existing_mapping: Any,
        new_text: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Sync a Bale message edit to Chatwoot via direct PostgreSQL UPDATE.

        Chatwoot has no content-edit REST API, so we bypass it and write
        directly to the ``messages`` table. This requires ``db_direct``
        credentials in the instance's ``chatwoot`` config.
        """
        chatwoot_message_id = existing_mapping.chatwoot_message_id
        if not chatwoot_message_id or not str(chatwoot_message_id).strip().isdigit():
            logger.warning(
                'platform_edit_no_chatwoot_id instance=%s platform_message_id=%s',
                instance_key,
                existing_mapping.platform_message_id,
            )
            return {'message': 'ignored', 'status': 'edit_ignored', 'detail': 'no_chatwoot_message_id'}

        db_svc = self._get_chatwoot_db_service(runtime.chatwoot)
        if db_svc is None:
            logger.warning(
                'platform_edit_no_db_direct instance=%s chatwoot_message_id=%s',
                instance_key,
                chatwoot_message_id,
            )
            return {'message': 'ignored', 'status': 'edit_ignored', 'detail': 'db_direct_not_configured'}

        try:
            await db_svc.connect()
            updated = await db_svc.update_message_content(
                message_id=int(chatwoot_message_id),
                new_content=new_text,
            )
            if updated:
                # Refresh our mapping record with the new payload
                self._messages.upsert(
                    db,
                    conversation_id=existing_mapping.conversation_id,
                    direction=existing_mapping.direction,
                    message_kind=existing_mapping.message_kind,
                    status=MessageStatus.sent,
                    chatwoot_message_id=str(chatwoot_message_id),
                    platform_message_id=existing_mapping.platform_message_id,
                    chatwoot_parent_message_id=existing_mapping.chatwoot_parent_message_id,
                    platform_parent_message_id=existing_mapping.platform_parent_message_id,
                    chatwoot_payload_json=existing_mapping.chatwoot_payload_json,
                    platform_payload_json=self._payload_or_none(runtime, event),
                )
                db.commit()
                logger.info(
                    'platform_edit_synced instance=%s chatwoot_message_id=%s platform_message_id=%s',
                    instance_key,
                    chatwoot_message_id,
                    existing_mapping.platform_message_id,
                )
                return {
                    'message': 'edited',
                    'status': 'synced',
                    'chatwoot_message_id': str(chatwoot_message_id),
                    'platform_message_id': str(existing_mapping.platform_message_id),
                }
            else:
                logger.warning(
                    'platform_edit_db_no_row instance=%s chatwoot_message_id=%s',
                    instance_key,
                    chatwoot_message_id,
                )
                return {'message': 'failed', 'status': 'edit_failed', 'detail': 'db_row_not_found'}
        except Exception as exc:
            logger.exception(
                'platform_edit_sync_error instance=%s chatwoot_message_id=%s error=%s',
                instance_key,
                chatwoot_message_id,
                exc,
            )
            return {'message': 'failed', 'status': 'edit_failed', 'detail': str(exc)}

    async def _handle_chatwoot_status_event(
        self,
        *,
        db: Session,
        instance_key: str,
        runtime: Any,
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Internal helper to handle chatwoot status event."""
        platform_key = self._platform_key(runtime)
        connector = connector_registry.get(platform_key)
        event_name = str(payload.get('event') or '').strip().lower()
        if not self._is_chatwoot_status_event(payload, event_name):
            return None
        if not self._status_notification_enabled(runtime.platform_metadata):
            return {'message': 'ignored', 'detail': 'status_notification_disabled'}

        chatwoot_conversation_id = self._extract_chatwoot_status_conversation_id(payload)
        if not chatwoot_conversation_id:
            logger.warning('status event ignored: conversation id missing instance=%s event=%s', instance_key, event_name)
            return {'message': 'ignored', 'detail': 'chatwoot_conversation_id_missing'}

        status_name = self._extract_chatwoot_status_name(payload)
        if not status_name:
            logger.warning(
                'status event ignored: status missing instance=%s event=%s conversation_id=%s',
                instance_key,
                event_name,
                chatwoot_conversation_id,
            )
            return {'message': 'ignored', 'detail': 'chatwoot_status_missing'}

        mapped = self._conversations.get_by_chatwoot_id(db, runtime.instance.id, str(chatwoot_conversation_id))
        mapped_destination = str(mapped.platform_conversation_id).strip() if mapped and mapped.platform_conversation_id else None
        contact_id = self._extract_contact_id(payload)
        extracted_destination, _source_marker = self._extract_destination(payload, platform_key=platform_key)
        mapped_is_probably_message_id = bool(
            mapped
            and mapped_destination
            and self._is_probably_platform_message_id(db, mapped.id, mapped_destination)
        )
        mapped_candidate = None if mapped_is_probably_message_id else mapped_destination
        destination_chat_id = self._choose_destination_chat_id(mapped_candidate, extracted_destination)

        if mapped_is_probably_message_id and (not destination_chat_id or destination_chat_id == mapped_destination):
            inferred_destination = self._infer_destination_from_contact_history(db, runtime.instance.id, contact_id)
            if inferred_destination and inferred_destination != mapped_destination:
                logger.warning(
                    'status destination recovered from mapped-message-id fallback instance=%s conversation_id=%s old=%s new=%s',
                    instance_key,
                    chatwoot_conversation_id,
                    mapped_destination,
                    inferred_destination,
                )
                destination_chat_id = inferred_destination

        if destination_chat_id and self._looks_like_uuid(destination_chat_id):
            inferred_destination = self._infer_destination_from_contact_history(db, runtime.instance.id, contact_id)
            if inferred_destination and inferred_destination != destination_chat_id:
                logger.warning(
                    'status destination recovered from contact history instance=%s conversation_id=%s old=%s new=%s',
                    instance_key,
                    chatwoot_conversation_id,
                    destination_chat_id,
                    inferred_destination,
                )
                destination_chat_id = inferred_destination

        if not mapped and destination_chat_id and not self._looks_like_uuid(destination_chat_id):
            conversation = self._conversations.upsert(
                db,
                instance_id=runtime.instance.id,
                platform_conversation_id=str(destination_chat_id),
                chatwoot_conversation_id=str(chatwoot_conversation_id),
                chatwoot_contact_id=contact_id,
                chatwoot_inbox_id=str(runtime.chatwoot.get('inbox_id') or '') or None,
            )
            db.commit()
            destination_chat_id = str(conversation.platform_conversation_id or '').strip() or destination_chat_id
        if not destination_chat_id:
            logger.warning(
                'status event ignored: destination not found instance=%s event=%s conversation_id=%s',
                instance_key,
                event_name,
                chatwoot_conversation_id,
            )
            return {'message': 'ignored', 'detail': 'status_destination_not_found'}
        if self._looks_like_uuid(destination_chat_id):
            logger.warning(
                'status event ignored: destination invalid instance=%s event=%s conversation_id=%s destination=%s',
                instance_key,
                event_name,
                chatwoot_conversation_id,
                destination_chat_id,
            )
            return {'message': 'ignored', 'detail': 'status_destination_invalid'}

        operator_name = self._extract_operator_name(payload) if status_name == 'open' else None
        text = self._status_notification_text(
            status_name,
            operator_name=operator_name,
            platform_metadata=runtime.platform_metadata,
        )
        if not text:
            logger.warning(
                'status event ignored: template missing instance=%s event=%s conversation_id=%s status=%s',
                instance_key,
                event_name,
                chatwoot_conversation_id,
                status_name,
            )
            return {'message': 'ignored', 'detail': 'status_template_missing'}
        if self._is_duplicate_status_notification(instance_key, chatwoot_conversation_id, status_name):
            logger.info(
                'status event deduplicated instance=%s conversation_id=%s status=%s event=%s',
                instance_key,
                chatwoot_conversation_id,
                status_name,
                event_name or 'conversation_status_changed',
            )
            return {'message': 'ignored', 'detail': 'status_duplicate', 'status': status_name}

        await connector.connect(instance_key, runtime.platform_metadata, runtime.proxy)
        await connector.send_text(instance_key, destination_chat_id, text)
        self._mark_status_notification(instance_key, chatwoot_conversation_id, status_name)
        logger.info(
            'sent status notification to platform instance=%s platform=%s conversation_id=%s destination=%s status=%s event=%s',
            instance_key,
            platform_key,
            chatwoot_conversation_id,
            destination_chat_id,
            status_name,
            event_name or 'conversation_status_changed',
        )
        return {
            'message': 'status_notified',
            'status': status_name,
            'detail': event_name or 'conversation_status_changed',
        }

    @staticmethod
    def _extract_chatwoot_status_conversation_id(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract chatwoot status conversation id."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
        candidates = [
            conversation.get('id'),
            payload.get('conversation_id'),
            payload.get('conversationId'),
            conversation.get('conversation_id'),
            payload.get('id'),
            conversation.get('display_id'),
            payload.get('display_id'),
        ]
        for value in candidates:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _is_chatwoot_status_event(payload: dict[str, Any], event_name: str) -> bool:
        """Internal helper to is chatwoot status event."""
        if event_name in {
            'conversation_status_changed',
            'conversation_resolved',
            'conversation_opened',
            'conversation_pending',
            'conversation_snoozed',
            'conversation_unsnoozed',
            'conversation_reopened',
        }:
            return True
        if event_name == 'conversation_updated':
            return BridgeService._extract_status_from_changed_attributes(payload) is not None
        if event_name:
            return False
        return BridgeService._extract_status_from_changed_attributes(payload) is not None

    @staticmethod
    def _extract_chatwoot_status_name(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract chatwoot status name."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
        changed = BridgeService._extract_status_from_changed_attributes(payload)
        candidates = [
            changed,
            payload.get('event'),
            payload.get('status'),
            conversation.get('status'),
        ]
        for value in candidates:
            status = BridgeService._normalize_chatwoot_status(value)
            if status:
                return status
        return None

    def _status_notification_text(
        self,
        status_name: str,
        *,
        operator_name: Optional[str] = None,
        platform_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Internal helper to status notification text."""
        templates = self._status_notification_templates(platform_metadata)
        if status_name == 'open' and operator_name:
            template = templates.get('open_by_operator')
            if template:
                try:
                    rendered = template.format(operator_name=operator_name)
                except Exception:
                    rendered = f'Your chat has been opened by {operator_name}.'
                rendered_text = str(rendered or '').strip()
                if rendered_text:
                    return rendered_text

        mapping = {
            'open': templates.get('open'),
            'resolved': templates.get('resolved'),
            'pending': templates.get('pending'),
            'snoozed': templates.get('snoozed'),
        }
        text = str(mapping.get(status_name) or '').strip()
        return text or None

    @staticmethod
    def _status_notification_enabled(platform_metadata: Optional[dict[str, Any]]) -> bool:
        """Internal helper to status notification enabled."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        raw = cfg.get('chatwoot_status_notify_to_platform')
        if raw is None:
            raw = cfg.get('chatwoot_status_notify_to_bale')
        if raw is None:
            return bool(settings.CHATWOOT_STATUS_NOTIFY_TO_BALE)
        return bool(raw)

    @staticmethod
    def _status_notification_templates(platform_metadata: Optional[dict[str, Any]]) -> dict[str, Optional[str]]:
        """Internal helper to status notification templates."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        return {
            'open': BridgeService._string_or_none(
                cfg.get('chatwoot_status_message_open'),
                fallback=settings.CHATWOOT_STATUS_MESSAGE_OPEN,
            ),
            'open_by_operator': BridgeService._string_or_none(
                cfg.get('chatwoot_status_message_open_by_operator'),
                fallback=settings.CHATWOOT_STATUS_MESSAGE_OPEN_BY_OPERATOR,
            ),
            'resolved': BridgeService._string_or_none(
                cfg.get('chatwoot_status_message_resolved'),
                fallback=settings.CHATWOOT_STATUS_MESSAGE_RESOLVED,
            ),
            'pending': BridgeService._string_or_none(
                cfg.get('chatwoot_status_message_pending'),
                fallback=settings.CHATWOOT_STATUS_MESSAGE_PENDING,
            ),
            'snoozed': BridgeService._string_or_none(
                cfg.get('chatwoot_status_message_snoozed'),
                fallback=settings.CHATWOOT_STATUS_MESSAGE_SNOOZED,
            ),
        }

    @staticmethod
    def _string_or_none(value: Any, *, fallback: str = '') -> Optional[str]:
        """Internal helper to string or none."""
        if value is None:
            text = str(fallback or '').strip()
            return text or None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_status_from_changed_attributes(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract status from changed attributes."""
        changed = payload.get('changed_attributes')
        return BridgeService._extract_status_from_change_container(changed)

    @staticmethod
    def _extract_status_from_change_container(value: Any) -> Optional[str]:
        """Internal helper to extract status from change container."""
        if isinstance(value, dict):
            if 'status' in value:
                return BridgeService._extract_status_from_change_value(value.get('status'))
            for nested in value.values():
                status = BridgeService._extract_status_from_change_container(nested)
                if status:
                    return status
            return None
        if isinstance(value, list):
            for item in value:
                status = BridgeService._extract_status_from_change_container(item)
                if status:
                    return status
            return None
        return None

    @staticmethod
    def _extract_status_from_change_value(value: Any) -> Optional[str]:
        """Internal helper to extract status from change value."""
        if isinstance(value, list):
            if not value:
                return None
            return BridgeService._normalize_chatwoot_status(value[-1])
        if isinstance(value, dict):
            for key in ('new', 'current', 'to', 'after', 'value'):
                if key in value:
                    status = BridgeService._normalize_chatwoot_status(value.get(key))
                    if status:
                        return status
            for nested in value.values():
                status = BridgeService._extract_status_from_change_value(nested)
                if status:
                    return status
            return None
        return BridgeService._normalize_chatwoot_status(value)

    @staticmethod
    def _normalize_chatwoot_status(value: Any) -> Optional[str]:
        """Internal helper to normalize chatwoot status."""
        mapping = {
            0: 'open',
            1: 'resolved',
            2: 'pending',
            3: 'snoozed',
        }
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return mapping.get(value)

        text = str(value or '').strip().lower()
        if not text:
            return None
        if text.isdigit():
            return mapping.get(int(text))

        aliases = {
            'open': 'open',
            'opened': 'open',
            'reopened': 'open',
            'conversation_opened': 'open',
            'resolved': 'resolved',
            'conversation_resolved': 'resolved',
            'pending': 'pending',
            'conversation_pending': 'pending',
            'snoozed': 'snoozed',
            'snooze': 'snoozed',
            'conversation_snoozed': 'snoozed',
            'unsnoozed': 'open',
            'conversation_unsnoozed': 'open',
        }
        return aliases.get(text)

    def _is_duplicate_status_notification(self, instance_key: str, conversation_id: str, status_name: str) -> bool:
        """Internal helper to is duplicate status notification."""
        now = time.monotonic()
        ttl_seconds = 8.0
        self._prune_status_notification_cache(now, ttl_seconds)

        key = '|'.join([str(instance_key), str(conversation_id), str(status_name)])
        previous = self._status_notify_recent.get(key)
        return previous is not None and (now - previous) <= ttl_seconds

    def _mark_status_notification(self, instance_key: str, conversation_id: str, status_name: str) -> None:
        """Internal helper to mark status notification."""
        now = time.monotonic()
        ttl_seconds = 8.0
        self._prune_status_notification_cache(now, ttl_seconds)

        key = '|'.join([str(instance_key), str(conversation_id), str(status_name)])
        self._status_notify_recent.set(key, now)

    def _prune_status_notification_cache(self, now: float, ttl_seconds: float) -> None:
        """Internal helper to prune status notification cache."""
        # TTLCache auto-expires entries on access; manual prune is a no-op here.
        pass

    def _extract_chatwoot_operator_name(self, payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract chatwoot operator name."""
        message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        top_meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        message_meta = message.get('meta') if isinstance(message.get('meta'), dict) else {}

        candidates = [
            payload.get('sender'),
            message.get('sender'),
            top_meta.get('sender'),
            message_meta.get('sender'),
            payload.get('created_by'),
            message.get('created_by'),
            payload.get('user'),
            message.get('user'),
            payload.get('actor'),
        ]

        for candidate in candidates:
            text = self._extract_display_name(candidate)
            if text:
                return text

        return self._extract_operator_name(payload)

    def _resolve_operator_notification(
        self,
        db: Session,
        *,
        conversation_id: str,
        operator_name: Optional[str],
    ) -> tuple[Optional[str], Any, Optional[str]]:
        """Internal helper to resolve operator notification."""
        resolved_name = self._normalize_operator_name(operator_name)
        if not resolved_name:
            return None, None, None

        repo = self._conversation_runtime_repo(db)
        row = repo.get(conversation_id)
        previous_name = self._normalize_operator_name(row.last_operator_name if row else None)

        if not previous_name:
            if not row:
                row = repo.get_or_create(conversation_id)
            return resolved_name, row, resolved_name

        if previous_name.casefold() == resolved_name.casefold():
            return None, row, previous_name

        return f'Operator changed: {resolved_name}', row, resolved_name

    @staticmethod
    def _normalize_operator_name(value: Any) -> Optional[str]:
        """Internal helper to normalize operator name."""
        text = str(value or '').strip()
        return text or None

    @staticmethod
    def _extract_operator_name(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract operator name."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
        conversation_meta = conversation.get('meta') if isinstance(conversation.get('meta'), dict) else {}
        top_meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}

        candidates = [
            conversation_meta.get('assignee'),
            top_meta.get('assignee'),
            conversation.get('assignee'),
            payload.get('assignee'),
            payload.get('changed_by'),
            payload.get('performed_by'),
            payload.get('user'),
            payload.get('actor'),
            payload.get('sender'),
            conversation_meta.get('sender'),
            top_meta.get('sender'),
            conversation_meta,
            top_meta,
        ]

        for candidate in candidates:
            text = BridgeService._extract_display_name(candidate)
            if text:
                return text

        return None

    @staticmethod
    def _extract_display_name(candidate: Any) -> Optional[str]:
        """Internal helper to extract display name."""
        if isinstance(candidate, str):
            text = candidate.strip()
            return text or None

        if isinstance(candidate, dict):
            for key in ('name', 'available_name', 'display_name', 'full_name'):
                text = str(candidate.get(key) or '').strip()
                if text:
                    return text

            first = str(candidate.get('first_name') or '').strip()
            last = str(candidate.get('last_name') or '').strip()
            full = f'{first} {last}'.strip()
            if full:
                return full

            for key in ('username', 'email', 'identifier'):
                text = str(candidate.get(key) or '').strip()
                if text:
                    return text

            for nested_key in ('user', 'assignee', 'sender', 'actor'):
                nested = candidate.get(nested_key)
                nested_name = BridgeService._extract_display_name(nested)
                if nested_name:
                    return nested_name
            return None

        if isinstance(candidate, (list, tuple)):
            for item in candidate:
                text = BridgeService._extract_display_name(item)
                if text:
                    return text

        return None

    def _resolve_contact_name_from_event(
        self,
        event: dict[str, Any],
        platform_key: str,
        chat_id: str,
    ) -> str:
        """Resolve the best contact name for a Chatwoot contact from an incoming event."""
        chat_type = str(event.get('chat_type') or 'user').strip().lower()
        from_name = str(event.get('from_name') or '').strip()
        source_prefix = self._source_prefix(platform_key).title()

        if chat_type in ('group', 'channel'):
            label = 'Group' if chat_type == 'group' else 'Channel'
            if not from_name or from_name == str(chat_id):
                return f'{source_prefix} {label} {chat_id}'
            if (
                f'({chat_type})' in from_name
                or f'({label.lower()})' in from_name
                or from_name.lower().startswith(label.lower())
            ):
                return from_name
            return f'({label.lower()}) {from_name}'

        resolved_name = from_name or str(chat_id)
        if resolved_name == str(chat_id) and str(chat_id).isdigit():
            return f'{source_prefix} User {chat_id}'
        return resolved_name

    @staticmethod
    def _platform_key(runtime: Any) -> str:
        """Internal helper to platform key."""
        platform_type = getattr(runtime, 'platform_type', None)
        key = getattr(platform_type, 'key', '') if platform_type is not None else ''
        return str(key or '').strip().lower()

    @staticmethod
    def _source_prefix(platform_key: str) -> str:
        """Internal helper to source prefix."""
        return connector_registry.prefix(platform_key)

    def _prefixed_identifier(self, platform_key: str, chat_id: str) -> str:
        """Internal helper to prefixed identifier."""
        return connector_registry.prefixed_source_id(platform_key, chat_id)

    @staticmethod
    def _split_prefixed_source_id(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """Internal helper to split prefixed source id."""
        raw = str(value or '').strip()
        if not raw or ':' not in raw:
            return None, None
        prefix, remainder = raw.split(':', 1)
        prefix = str(prefix or '').strip().upper()
        remainder = str(remainder or '').strip()
        if not prefix or not remainder:
            return None, None
        return prefix, remainder

    def _extract_destination(
        self,
        payload: dict[str, Any],
        *,
        platform_key: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Internal helper to extract destination."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}

        contact_inbox = conversation.get('contact_inbox') if isinstance(conversation.get('contact_inbox'), dict) else {}
        sender_meta = (conversation.get('meta') or {}).get('sender') if isinstance(conversation.get('meta'), dict) else {}
        source_id = str(contact_inbox.get('source_id') or '').strip() or None
        identifier = str(sender_meta.get('identifier') or '').strip() or None

        expected_prefix = self._source_prefix(platform_key) if platform_key else None
        known_prefixes = connector_registry.all_prefixes()
        for raw in (source_id, identifier):
            prefix, value = self._split_prefixed_source_id(raw)
            if not prefix or not value:
                continue
            if expected_prefix and prefix == expected_prefix:
                return value, raw

        for raw in (source_id, identifier):
            prefix, value = self._split_prefixed_source_id(raw)
            if not prefix or not value:
                continue
            if not expected_prefix and prefix in known_prefixes:
                return value, raw

        for raw in (source_id, identifier):
            prefix, _value = self._split_prefixed_source_id(raw)
            if prefix and expected_prefix and prefix != expected_prefix:
                continue
            if prefix and not expected_prefix and prefix in known_prefixes:
                continue
            if raw and not self._looks_like_uuid(raw):
                return raw, raw

        if source_id:
            source_prefix, _source_value = self._split_prefixed_source_id(source_id)
            if source_prefix and expected_prefix and source_prefix != expected_prefix:
                return None, None
            return source_id, source_id
        if identifier:
            identifier_prefix, _identifier_value = self._split_prefixed_source_id(identifier)
            if identifier_prefix and expected_prefix and identifier_prefix != expected_prefix:
                return None, None
            return identifier, identifier

        return None, None

    def _infer_destination_from_contact_history(
        self,
        db: Session,
        instance_id: str,
        chatwoot_contact_id: Optional[str],
    ) -> Optional[str]:
        """Internal helper to infer destination from contact history."""
        contact_id = str(chatwoot_contact_id or '').strip()
        if not contact_id:
            return None

        rows = self._conversations.list_for_instance(db, instance_id)
        for row in rows:
            if str(row.chatwoot_contact_id or '').strip() != contact_id:
                continue
            candidate = str(row.platform_conversation_id or '').strip()
            if candidate and not self._looks_like_uuid(candidate):
                if self._is_probably_platform_message_id(db, row.id, candidate):
                    continue
                return candidate
        return None

    @staticmethod
    def _is_phone_number_destination(value: Optional[str]) -> bool:
        """Return True if the destination looks like a raw phone number."""
        if not value:
            return False
        digits = re.sub(r"\D", "", str(value))
        # Iranian mobile numbers, possibly international
        return bool(re.match(r"^\+?98\d{10}$", str(value).strip())) or bool(
            re.match(r"^98\d{10}$", digits)
        )

    def _normalize_bale_pv_phone(self, phone: str) -> str:
        """Normalize phone number to 98XXXXXXXXXX digits."""
        digits = re.sub(r"\D", "", str(phone or "").strip())
        if digits.startswith("0") and len(digits) == 11:
            digits = "98" + digits[1:]
        return digits

    async def _resolve_bale_pv_phone(
        self,
        db: Session,
        runtime: Any,
        connector: Any,
        instance_key: str,
        phone_number: str,
    ) -> Dict[str, Any]:
        """Resolve a phone number to a Bale user, caching the result locally."""
        from bale_grpc_client.dialog_parser import parse_import_contacts_response

        normalized = self._normalize_bale_pv_phone(phone_number)
        cached = (
            db.query(BalePvPhoneResolvedUser)
            .filter_by(instance_id=runtime.instance.id, phone_number=normalized)
            .first()
        )
        if cached:
            return {
                "id": cached.bale_user_id,
                "access_hash": cached.access_hash,
                "name": cached.name,
                "nick": cached.nick,
            }

        user = await connector.resolve_phone_to_user(instance_key, normalized)
        if not user or not user.get("id"):
            raise RuntimeError(f"Could not resolve Bale user for phone {normalized}")

        access_hash = user.get("access_hash")
        access_hash_str = str(access_hash) if access_hash is not None else None
        cached = BalePvPhoneResolvedUser(
            instance_id=runtime.instance.id,
            phone_number=normalized,
            bale_user_id=int(user["id"]),
            access_hash=access_hash_str,
            name=user.get("name"),
            nick=user.get("nick"),
        )
        db.add(cached)
        db.commit()
        return {
            "id": int(user["id"]),
            "access_hash": access_hash_str,
            "name": user.get("name"),
            "nick": user.get("nick"),
        }

    async def _update_chatwoot_contact_for_bale_pv_phone(
        self,
        runtime: Any,
        chatwoot_contact_id: Optional[str],
        resolved_user: Dict[str, Any],
        phone_number: str,
    ) -> None:
        """Update the Chatwoot contact with the resolved Bale identifier and name."""
        if not chatwoot_contact_id:
            return
        try:
            client = self._get_chatwoot_client(runtime.chatwoot)
            account_id = int(runtime.chatwoot.get("account_id"))
            contact_id = int(chatwoot_contact_id)
            name = resolved_user.get("name") or resolved_user.get("nick") or f"User {resolved_user['id']}"
            identifier = connector_registry.prefixed_source_id("bale_pv_enterprise", str(resolved_user["id"]))
            await client.update_contact(
                account_id,
                contact_id,
                {
                    "name": name,
                    "identifier": identifier,
                    "phone_number": self._normalize_bale_pv_phone(phone_number),
                },
            )
        except Exception as exc:
            logger.warning(
                "failed to update chatwoot contact for bale pv phone instance=%s contact_id=%s error=%s",
                runtime.instance.instance_key,
                chatwoot_contact_id,
                exc,
            )

    def _find_existing_contact_conversation(
        self,
        db: Session,
        *,
        instance_id: str,
        chatwoot_contact_id: str,
        chatwoot_inbox_id: Optional[str],
        chat_id: str,
    ):
        """Internal helper to find existing contact conversation."""
        rows = self._conversations.list_by_contact(
            db,
            instance_id,
            chatwoot_contact_id,
            chatwoot_inbox_id,
        )
        if not rows:
            return None

        for row in rows:
            if str(row.platform_conversation_id or '').strip() == str(chat_id):
                return row

        for row in rows:
            candidate = str(row.platform_conversation_id or '').strip()
            if candidate and not self._looks_like_uuid(candidate):
                if self._is_probably_platform_message_id(db, row.id, candidate):
                    continue
                return row

        return rows[0]

    def _is_probably_platform_message_id(self, db: Session, conversation_id: str, candidate: Optional[str]) -> bool:
        """Internal helper to is probably platform message id."""
        value = str(candidate or '').strip()
        if not value:
            return False
        return self._messages.get_by_platform_message_id(db, conversation_id, value) is not None

    def _normalize_attachment_for_chatwoot(
        self,
        *,
        filename: Optional[str],
        content_type: Optional[str],
        content: bytes,
    ) -> tuple[str, Optional[str]]:
        """Internal helper to normalize attachment for chatwoot."""
        name = str(filename or '').strip() or 'file'
        ctype = str(content_type or '').strip().lower()

        if not ctype or ctype == 'application/octet-stream':
            guessed = mimetypes.guess_type(name)[0]
            if guessed:
                ctype = guessed.lower()
            else:
                ctype = self._guess_content_type_from_bytes(content) or ctype

        if '.' not in name.rsplit('/', 1)[-1] and ctype:
            ext = self._preferred_extension_for_content_type(ctype) or (mimetypes.guess_extension(ctype) or '')
            if ext:
                name = f'{name}{ext}'

        return name, ctype or None

    @staticmethod
    def _normalize_filename_for_platform(
        filename: Optional[str],
        content_type: Optional[str],
        url: Optional[str],
    ) -> str:
        """Ensure a filename has an extension for platform delivery.

        Derives the extension from the content_type or the URL path if the
        raw filename lacks one.
        """
        name = str(filename or '').strip() or 'file'
        # Already has an extension?
        if '.' in name.rsplit('/', 1)[-1]:
            return name

        ctype = str(content_type or '').strip().lower()
        ext = ''
        if ctype:
            ext = BridgeService._preferred_extension_for_content_type(ctype) or (mimetypes.guess_extension(ctype) or '')
        if not ext and url:
            guessed, _ = mimetypes.guess_type(url)
            if guessed:
                ext = BridgeService._preferred_extension_for_content_type(guessed) or (mimetypes.guess_extension(guessed) or '')
        if ext:
            name = f'{name}{ext}'
        return name

    @staticmethod
    def _preferred_extension_for_content_type(content_type: str) -> Optional[str]:
        """Internal helper to preferred extension for content type."""
        mapping = {
            'audio/ogg': '.ogg',
            'audio/mpeg': '.mp3',
            'video/mp4': '.mp4',
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
        }
        return mapping.get(str(content_type or '').strip().lower())

    @staticmethod
    def _guess_content_type_from_bytes(content: bytes) -> Optional[str]:
        """Internal helper to guess content type from bytes."""
        if not content:
            return None
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if content.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if content.startswith((b'GIF87a', b'GIF89a')):
            return 'image/gif'
        if len(content) > 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
            return 'image/webp'
        if content.startswith(b'OggS'):
            return 'audio/ogg'
        if len(content) > 12 and content[:4] == b'RIFF' and content[8:12] == b'WAVE':
            return 'audio/wav'
        if content.startswith(b'ID3') or (len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0):
            return 'audio/mpeg'
        if len(content) > 8 and content[4:8] == b'ftyp':
            return 'video/mp4'
        return None

    @staticmethod
    def _choose_destination_chat_id(
        mapped_destination: Optional[str],
        extracted_destination: Optional[str],
    ) -> Optional[str]:
        """Internal helper to choose destination chat id."""
        mapped = str(mapped_destination or '').strip() or None
        extracted = str(extracted_destination or '').strip() or None

        if mapped and not BridgeService._looks_like_uuid(mapped):
            return mapped
        if extracted and not BridgeService._looks_like_uuid(extracted):
            return extracted
        return extracted or mapped

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        """Internal helper to looks like uuid."""
        return bool(
            re.match(
                r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
                str(value or '').strip(),
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Internal helper to extract attachments."""
        direct = payload.get('attachments')
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = (payload.get('message') or {}).get('attachments')
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_chatwoot_message_text(payload: dict[str, Any]) -> str:
        """Internal helper to extract chatwoot message text."""
        message_obj = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        candidates = [
            payload.get('content'),
            message_obj.get('content'),
            payload.get('processed_message_content'),
            message_obj.get('processed_message_content'),
        ]
        for value in candidates:
            text = str(value or '').strip()
            if text:
                return text
        return ''

    @staticmethod
    def _normalize_chatwoot_message_type(value: Any) -> str:
        """Internal helper to normalize chatwoot message type."""
        if isinstance(value, int):
            # Chatwoot numeric enum compatibility.
            return {0: 'incoming', 1: 'outgoing', 2: 'activity', 3: 'template'}.get(value, str(value))
        return str(value or '').strip().lower()

    @staticmethod
    def _is_forwardable_chatwoot_message(payload: dict[str, Any], event_name: str) -> bool:
        """Internal helper to decide whether a Chatwoot webhook message should be forwarded."""
        message_obj = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        message_type = BridgeService._normalize_chatwoot_message_type(payload.get('message_type'))
        nested_type = BridgeService._normalize_chatwoot_message_type(message_obj.get('message_type'))
        event = str(event_name or '').strip().lower()

        if message_type == 'outgoing' or nested_type == 'outgoing':
            return True

        # Chatwoot automations (welcome/working-hours) can arrive as template messages.
        if event == 'message_created' and (message_type == 'template' or nested_type == 'template'):
            return True

        return False

    @staticmethod
    def _is_chatwoot_message_deleted(payload: dict[str, Any]) -> bool:
        """Detect whether a Chatwoot message_updated webhook signals deletion.

        Chatwoot's ``destroy`` action sets ``content_attributes['deleted'] = true``
        and fires a ``message_updated`` event rather than a dedicated delete webhook.
        """
        content_attributes = payload.get('content_attributes') if isinstance(payload.get('content_attributes'), dict) else {}
        message_obj = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        msg_content_attributes = (
            message_obj.get('content_attributes') if isinstance(message_obj.get('content_attributes'), dict) else {}
        )
        return bool(
            content_attributes.get('deleted')
            or msg_content_attributes.get('deleted')
        )

    async def _handle_chatwoot_message_delete(
        self,
        db: Session,
        instance_key: str,
        payload: dict[str, Any],
        runtime: Any,
        connector: Any,
    ) -> dict[str, Any]:
        """Propagate a Chatwoot message deletion to the Bale platform."""
        chatwoot_message_id = self._extract_chatwoot_message_id(payload)
        if not chatwoot_message_id:
            return {'message': 'ignored', 'detail': 'chatwoot_message_id_missing'}

        chatwoot_conversation_id = self._extract_chatwoot_conversation_id(payload)
        if not chatwoot_conversation_id:
            return {'message': 'ignored', 'detail': 'chatwoot_conversation_id_missing'}

        mapped_conversation = self._conversations.get_by_chatwoot_id(
            db, runtime.instance.id, str(chatwoot_conversation_id)
        )
        if not mapped_conversation:
            return {'message': 'ignored', 'detail': 'conversation_not_mapped'}

        platform_message_id = None
        if mapped_conversation.id:
            mapping = self._messages.get_by_chatwoot_message_id(
                db, mapped_conversation.id, str(chatwoot_message_id)
            )
            if mapping:
                platform_message_id = mapping.platform_message_id

        if not platform_message_id:
            logger.warning(
                'chatwoot_delete_no_platform_mapping instance=%s chatwoot_message_id=%s conversation_id=%s',
                instance_key,
                chatwoot_message_id,
                chatwoot_conversation_id,
            )
            return {'message': 'ignored', 'detail': 'platform_message_not_mapped'}

        destination_chat_id = mapped_conversation.platform_conversation_id
        if not destination_chat_id:
            return {'message': 'ignored', 'detail': 'destination_not_found'}

        try:
            await connector.connect(instance_key, runtime.platform_metadata, runtime.proxy)
            await connector.delete_message(instance_key, str(destination_chat_id), str(platform_message_id))
            logger.info(
                'chatwoot_delete_propagated instance=%s chatwoot_message_id=%s platform_message_id=%s chat_id=%s',
                instance_key,
                chatwoot_message_id,
                platform_message_id,
                destination_chat_id,
            )
            return {
                'message': 'deleted',
                'status': 'propagated',
                'chatwoot_message_id': str(chatwoot_message_id),
                'platform_message_id': str(platform_message_id),
            }
        except Exception as exc:
            logger.warning(
                'chatwoot_delete_propagation_failed instance=%s chatwoot_message_id=%s platform_message_id=%s error=%s',
                instance_key,
                chatwoot_message_id,
                platform_message_id,
                exc,
            )
            return {
                'message': 'failed',
                'status': 'propagation_failed',
                'chatwoot_message_id': str(chatwoot_message_id),
                'platform_message_id': str(platform_message_id),
                'detail': str(exc),
            }

    @staticmethod
    def _extract_chatwoot_conversation_id(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract chatwoot conversation id."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
        cid = conversation.get('id') or payload.get('conversation_id') or payload.get('conversationId')
        return str(cid) if cid is not None and str(cid).strip() else None

    @staticmethod
    def _extract_chatwoot_message_id(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract chatwoot message id."""
        candidate = payload.get('id')
        if candidate is None:
            message_obj = payload.get('message') if isinstance(payload.get('message'), dict) else {}
            candidate = message_obj.get('id')
        return str(candidate) if candidate is not None and str(candidate).strip() else None

    @staticmethod
    def _extract_parent_chatwoot_message_id(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract parent chatwoot message id."""
        content_attributes = payload.get('content_attributes') if isinstance(payload.get('content_attributes'), dict) else {}
        message_obj = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        msg_content_attributes = (
            message_obj.get('content_attributes') if isinstance(message_obj.get('content_attributes'), dict) else {}
        )

        candidates = [
            content_attributes.get('in_reply_to'),
            content_attributes.get('in_reply_to_message_id'),
            msg_content_attributes.get('in_reply_to'),
            msg_content_attributes.get('in_reply_to_message_id'),
            payload.get('in_reply_to'),
            message_obj.get('in_reply_to'),
            payload.get('reply_to_message_id'),
            message_obj.get('reply_to_message_id'),
        ]

        for value in candidates:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _extract_contact_id(payload: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract contact id."""
        conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
        meta = conversation.get('meta') if isinstance(conversation.get('meta'), dict) else {}
        sender = meta.get('sender') if isinstance(meta.get('sender'), dict) else {}
        value = sender.get('id')
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_phone_number(value: Any) -> Optional[str]:
        """Internal helper to normalize phone number."""
        text = str(value or '').strip()
        if not text:
            return None
        compact = re.sub(r'\s+', '', text)
        if compact.startswith('+'):
            digits = re.sub(r'\D', '', compact[1:])
            return f'+{digits}' if digits else None
        if compact.startswith('00'):
            digits = re.sub(r'\D', '', compact[2:])
            return f'+{digits}' if digits else None
        digits = re.sub(r'\D', '', compact)
        if not digits:
            return None
        if 8 <= len(digits) <= 15 and not digits.startswith('0'):
            return f'+{digits}'
        return digits

    @staticmethod
    def _extract_phone_from_shared_text(value: Any) -> Optional[str]:
        """Internal helper to extract phone from shared text."""
        text = str(value or '').strip()
        if not text:
            return None

        labeled = re.search(r'(?i)shared\s+phone\s+number\s*:\s*([+\d][\d\-\s().]{5,})', text)
        candidate = labeled.group(1) if labeled else None
        if not candidate:
            generic = re.search(r'(?<!\d)(?:\+|00)?\d[\d\-\s().]{6,}\d(?!\d)', text)
            candidate = generic.group(0) if generic else None
        if not candidate:
            return None

        return BridgeService._normalize_phone_number(candidate)

    @staticmethod
    def _extract_contact_payload(value: Any) -> dict[str, Any]:
        """Internal helper to extract contact payload."""
        payload = value if isinstance(value, dict) else {}
        if isinstance(payload.get('payload'), dict):
            payload = payload.get('payload') or {}
        if isinstance(payload.get('contact'), dict):
            payload = payload.get('contact') or {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _build_contact_update_payload(
        current_contact: dict[str, Any],
        normalized_phone: str,
        *,
        fallback_name: str,
    ) -> dict[str, Any]:
        """Internal helper to build contact update payload."""
        payload = {
            'name': str(current_contact.get('name') or '').strip() or fallback_name,
            'phone_number': normalized_phone,
        }
        identifier = str(current_contact.get('identifier') or '').strip()
        if identifier:
            payload['identifier'] = identifier
        email = str(current_contact.get('email') or '').strip()
        if email:
            payload['email'] = email
        return payload

    @staticmethod
    def _to_plus_phone_candidate(phone: Optional[str]) -> Optional[str]:
        """Internal helper to to plus phone candidate."""
        text = str(phone or '').strip()
        if not text or text.startswith('+'):
            return None
        digits = re.sub(r'\D', '', text)
        if not digits:
            return None
        if digits.startswith('0'):
            return None
        if len(digits) < 8 or len(digits) > 15:
            return None
        return f'+{digits}'

    @staticmethod
    def _extract_id(obj: Any) -> Optional[int]:
        """Internal helper to extract id."""
        if isinstance(obj, dict):
            value = obj.get('id')
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    @staticmethod
    def _chatwoot_webhook_url(instance_key: str) -> str:
        """Internal helper to build the expected Chatwoot webhook URL."""
        return f"{settings.SERVER_BASE_URL.rstrip('/')}/api/v1/webhooks/chatwoot/{str(instance_key).strip()}"

    @staticmethod
    def _build_chatwoot_api_inbox_payload(inbox_name: str, webhook_url: str) -> dict[str, Any]:
        """Internal helper to build a Chatwoot API inbox payload."""
        return {
            'name': str(inbox_name).strip(),
            'callback_webhook_url': str(webhook_url).strip(),
            'channel': {
                'type': 'api',
                'webhook_url': str(webhook_url).strip(),
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
        """Internal helper to repair stale Chatwoot inbox webhook URLs."""
        inbox_id = self._extract_id(inbox_obj) or self._extract_id((inbox_obj or {}).get('payload'))
        if not inbox_id:
            return inbox_obj, False

        current_webhook_url = ChatwootClient.extract_inbox_webhook_url(inbox_obj)
        if str(current_webhook_url or '').strip() == str(expected_webhook_url).strip():
            return inbox_obj, False

        logger.warning(
            'repairing chatwoot inbox webhook instance=%s inbox_id=%s inbox_name=%s old=%s new=%s',
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
                'name': str(inbox_name).strip(),
                'callback_webhook_url': str(expected_webhook_url).strip(),
            },
        )
        normalized = updated if isinstance(updated, dict) else dict(inbox_obj)
        normalized.setdefault('id', int(inbox_id))
        target = normalized.get('payload') if isinstance(normalized.get('payload'), dict) else normalized
        target['callback_webhook_url'] = str(expected_webhook_url).strip()
        channel = target.get('channel') if isinstance(target.get('channel'), dict) else {}
        channel['webhook_url'] = str(expected_webhook_url).strip()
        target['channel'] = channel
        return normalized, True

