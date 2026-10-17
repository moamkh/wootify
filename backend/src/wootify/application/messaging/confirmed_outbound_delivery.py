"""Checkpoint Bale/Eitaa acceptance and keep Chatwoot delivery status truthful."""
from __future__ import annotations

import copy
import asyncio
import weakref
from typing import Any, Awaitable, Callable

from wootify.infrastructure.persistence.models import (
    Conversation, MessageDirection, MessageKind, MessageMapping, MessageStatus,
)
from wootify.application.messaging.payload_parser import MessagePayloadParser

CONFIRMED_DELIVERY_PLATFORMS = frozenset({'bale_pv_enterprise', 'eitaa_pv_enterprise'})


class DeliveryStatusSyncError(RuntimeError):
    """A Chatwoot status/note operation failed; no platform resend is implied."""
    retryable = True
    delivery_uncertain = False


class FailureNoteScanPending(DeliveryStatusSyncError):
    """Resume a bounded note-metadata scan on the next bookkeeping attempt."""
    def __init__(self, note_ids: list[int], before: int) -> None:
        super().__init__('Failure-note recovery scan needs another page batch')
        self.note_ids = note_ids
        self.before = before


def safe_delivery_retry(error: BaseException) -> bool:
    """Unknown/possibly-dispatched sends are never automatically repeated."""
    seen: set[int] = set()
    current: BaseException | None = error
    retryable = False
    terminal_rejection = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, 'delivery_uncertain', False):
            return False
        terminal_rejection = terminal_rejection or (getattr(current, 'delivery_rejected', False) is True
                                                   and getattr(current, 'retryable', None) is False)
        if hasattr(current, 'retryable'):
            retryable = retryable or getattr(current, 'retryable') is True
        current = current.__cause__
    return retryable and not terminal_rejection


def definitely_not_accepted(error: BaseException) -> bool:
    """Clear dispatch uncertainty only for a pre-send error or negative ACK."""
    seen: set[int] = set()
    current: BaseException | None = error
    rejected = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, 'delivery_uncertain', False):
            return False
        rejected = rejected or getattr(current, 'delivery_rejected', False) is True
        current = current.__cause__
    return rejected or safe_delivery_retry(error)


def delivery_retry_after(error: BaseException) -> int | None:
    """Respect an explicit server flood-wait instead of hammering its endpoint."""
    delays = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        value = getattr(current, 'retry_after_seconds', None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            delays.append(value)
        current = current.__cause__
    return max(delays) if delays else None


class ConfirmedOutboundDelivery:
    """One existing message mapping holds a durable, per-part send ledger."""
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    @staticmethod
    def platform_key(instance: Any) -> str:
        return str(getattr(getattr(instance, 'platform_type', None), 'key', '') or '')

    @classmethod
    def platform_name(cls, instance: Any) -> str:
        return 'Eitaa PV' if cls.platform_key(instance) == 'eitaa_pv_enterprise' else 'Bale PV'

    @staticmethod
    def plan(attachments: list[dict[str, Any]], content: str, *, eitaa: bool) -> list[tuple[str, Any]]:
        plan = [(f"media:{att['id']}" if att.get('id') else f'media:index:{index}', att)
                for index, att in enumerate(attachments)] if attachments else [('text', None)]
        if eitaa and attachments and content:
            plan.append(('text', None))
        if len({part for part, _ in plan}) != len(plan):
            raise RuntimeError('delivery_payload_invalid: attachment keys must be distinct')
        return plan

    def message_lock(self, instance: Any, payload: dict[str, Any]) -> asyncio.Lock:
        key = f'{instance.id}:{MessagePayloadParser.extract_conversation_id(payload)}:{MessagePayloadParser.extract_message_id(payload)}'
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def report_failure_or_confirm(self, db: Any, instance: Any, client: Any, config: dict[str, Any],
                                        payload: dict[str, Any], report: Callable[[], Awaitable[None]]) -> bool:
        # Serialize reporting with native confirmations. A missing RPC ACK must
        # not undo a receipt that arrived through an outgoing update meanwhile.
        async with self.message_lock(instance, payload):
            row = self.mapping(db, instance, payload)
            if row is not None:
                db.refresh(row)
                if self.state(row).get('complete') is True:
                    await self.finalize(db, instance, client, config, payload, row)
                    return True
            await report()
            return False

    @staticmethod
    def state(row: MessageMapping) -> dict[str, Any]:
        payload = row.platform_payload_json or {}
        return copy.deepcopy(payload.get('bale_delivery') or payload.get('eitaa_delivery') or {})

    @staticmethod
    def save(db: Any, row: MessageMapping, state: dict[str, Any]) -> None:
        key = 'eitaa_delivery' if state.get('platform_key') == 'eitaa_pv_enterprise' else 'bale_delivery'
        row.platform_payload_json = {
            **(row.platform_payload_json or {}), key: state,
            'delivery_complete': state.get('complete') is True,
        }
        db.add(row)
        db.commit()

    def mapping(self, db: Any, instance: Any, payload: dict[str, Any]) -> MessageMapping | None:
        return (db.query(MessageMapping).join(Conversation)
                .filter(Conversation.instance_id == str(instance.id),
                        Conversation.chatwoot_conversation_id == str(MessagePayloadParser.extract_conversation_id(payload)),
                        MessageMapping.direction == MessageDirection.chatwoot_to_platform,
                        MessageMapping.chatwoot_message_id == str(MessagePayloadParser.extract_message_id(payload))).first())

    def import_eitaa_legacy(self, db: Any, instance: Any, payload: dict[str, Any],
                            content: str, attachments: list[dict[str, Any]]) -> None:
        """Keep positively checkpointed old batch parts; never replay them on upgrade."""
        row = self.mapping(db, instance, payload)
        if row is None or self.state(row):
            return
        message_id = str(MessagePayloadParser.extract_message_id(payload))
        snapshot = row.platform_payload_json or {}
        if snapshot.get('batch_message_id') != message_id or not snapshot.get('batch_part'):
            return  # Legacy single sends without receipts still require verification.
        plan = self.plan(attachments, content, eitaa=True)
        parts = [part for part, _ in plan]
        primary_part = 'text' if content else parts[0]
        members = [member for member in db.query(MessageMapping).filter_by(conversation_id=row.conversation_id,
                    direction=MessageDirection.chatwoot_to_platform).all()
                   if (member.platform_payload_json or {}).get('batch_message_id') == message_id
                   and member.status == MessageStatus.sent and member.platform_message_id]
        receipts = {member.platform_payload_json['batch_part']: str(member.platform_message_id) for member in members}
        if not receipts or any(part not in parts for part in receipts):
            raise RuntimeError('legacy_send_confirmation_missing: Eitaa batch parts cannot be matched safely')
        if snapshot['batch_part'] != primary_part:
            # The old worker committed every ACK before transferring the
            # Chatwoot ID to the caption. A crash in between leaves a perfectly
            # valid caption row that must be reused, not inserted a second time.
            primary = next((member for member in members if member.platform_payload_json.get('batch_part') == primary_part), None)
            if primary is not None and primary.chatwoot_message_id not in (None, message_id):
                raise RuntimeError('legacy_send_confirmation_missing: Eitaa primary receipt belongs to another Chatwoot message')
            row.chatwoot_message_id = None
            db.flush()
            if primary is not None:
                row = primary
                row.chatwoot_message_id = message_id
            else:
                row = MessageMapping(conversation_id=row.conversation_id, direction=MessageDirection.chatwoot_to_platform,
                    message_kind=MessageKind.text if primary_part == 'text' else MessageKind.media,
                    chatwoot_message_id=message_id, platform_message_id=receipts.get(primary_part),
                    status=MessageStatus.sent if primary_part in receipts else MessageStatus.pending,
                    platform_payload_json={'text': content, 'batch_message_id': message_id, 'batch_part': primary_part})
            db.add(row)
        state = {'plan': parts, 'receipts': receipts, 'inflight': {}, 'complete': all(part in receipts for part in parts),
                 'content': content, 'platform_key': 'eitaa_pv_enterprise', 'primary_part': primary_part}
        self.save(db, row, state)

    async def deliver(self, db: Any, instance: Any, runtime: Any, client: Any,
                      config: dict[str, Any], peer_id: str, payload: dict[str, Any], *,
                      content: str, attachments: list[dict[str, Any]], reply_to: str | None) -> list[dict[str, Any]]:
        message_id = str(MessagePayloadParser.extract_message_id(payload) or '')
        conversation_id = str(MessagePayloadParser.extract_conversation_id(payload) or '')
        conversation = db.query(Conversation).filter_by(instance_id=instance.id, chatwoot_conversation_id=conversation_id).one()
        row = self.mapping(db, instance, payload)
        eitaa = self.platform_key(instance) == 'eitaa_pv_enterprise'
        name = self.platform_name(instance)
        plan = self.plan(attachments, content, eitaa=eitaa)
        if not row:
            row = MessageMapping(conversation_id=conversation.id, direction=MessageDirection.chatwoot_to_platform,
                                 message_kind=MessageKind.media if attachments else MessageKind.text,
                                 chatwoot_message_id=message_id, status=MessageStatus.pending,
                                 chatwoot_parent_message_id=MessagePayloadParser.extract_parent_message_id(payload),
                                 platform_parent_message_id=reply_to,
                                 platform_payload_json={'text': content, 'delivery_complete': False})
            db.add(row)
            db.commit()
        state = self.state(row)
        if state.get('cancelled'):
            raise RuntimeError('outgoing_delivery_cancelled: this Chatwoot message was deleted')
        if not state:
            if row.status == MessageStatus.sent:
                raise RuntimeError(f'legacy_send_confirmation_missing: verify the existing {name} message before resending')
            state = {'plan': [part for part, _ in plan], 'receipts': {}, 'inflight': {}, 'complete': False,
                     'content': content, 'reply_to': reply_to, 'platform_key': self.platform_key(instance),
                     'primary_part': 'text' if eitaa and content else plan[0][0]}
            self.save(db, row, state)
        if state.get('plan') != [part for part, _ in plan]:
            raise RuntimeError('delivery_payload_changed: attachment plan differs from the original attempt')
        if state.get('content', content) != content or state.get('reply_to', reply_to) != reply_to:
            raise RuntimeError('delivery_payload_changed: retry content differs from the original attempt')
        if state.get('inflight'):
            # Includes process interruption after dispatch but before the ACK
            # checkpoint. Native outgoing updates can reconcile these exact RIDs.
            raise RuntimeError(f'delivery_unconfirmed: {name} may have accepted this send; awaiting its original message confirmation')

        try:
            await client.update_message_status(int(config['account_id']), int(conversation_id), int(message_id),
                                               status='failed', external_error=f'{name}: waiting for confirmed delivery')
        except Exception as exc:
            raise DeliveryStatusSyncError(f'Cannot mark the unconfirmed {name} message pending/failed in Chatwoot') from exc
        sent = []
        for part, attachment in plan:
            state = self.state(row)
            receipt = state.get('receipts', {}).get(part)
            if receipt:
                sent.append({'ok': True, 'confirmed': True, 'result': {'message_id' if eitaa else 'rid': receipt}})
                continue

            dispatched_request_id = None
            async def before_dispatch(rid: int, *, current_part: str = part) -> None:
                nonlocal dispatched_request_id
                checkpoint = self.state(row)
                checkpoint.setdefault('inflight', {})[current_part] = str(rid)
                if not eitaa and row.platform_message_id is None:
                    row.platform_message_id = str(rid)
                try:
                    self.save(db, row, checkpoint)
                    dispatched_request_id = str(rid)
                except Exception as exc:
                    db.rollback()
                    raise DeliveryStatusSyncError(f'Cannot persist {name} dispatch checkpoint; message was not dispatched') from exc

            await self.bridge._prepare_adapter_outbound(runtime.adapter, peer_id)
            try:
                options = {'reply_to': reply_to, 'mirror_echo': False,
                           'idempotency_key': f'{instance.instance_key}:{peer_id if eitaa else conversation_id}:{message_id}:{part}',
                           'before_dispatch': before_dispatch}
                if attachment is None:
                    result = await runtime.adapter.send_text(peer_id, content, **options)
                else:
                    url = attachment.get('data_url') or attachment.get('file_url')
                    if not url:
                        raise RuntimeError('attachment_url_missing: cannot confirm a skipped attachment')
                    if str(url).startswith('/'):
                        url = str(config.get('base_url') or '').rstrip('/') + str(url)
                    result = await runtime.adapter.send_media(peer_id, url,
                        filename=self.bridge._attachment_filename(attachment, str(url)), caption=None if eitaa else content or None, **options)
                rid = self.bridge._extract_platform_rid(result)
                if not isinstance(result, dict) or result.get('confirmed') is not True or result.get('ok') is not True or not rid:
                    raise RuntimeError(f'delivery_unconfirmed: {name} send response did not contain an acknowledged message')
                request_id = str((result.get('result') or {}).get('random_id') or '') if eitaa else None
                expected = self.state(row).get('inflight', {}).get(part)
                if eitaa and (not expected or request_id != expected):
                    raise RuntimeError('delivery_ack_mismatch: Eitaa request confirmation does not match the dispatch checkpoint')
                # Checkpoint acceptance before any awaited typing cleanup. A
                # timeout in that cosmetic step must not discard a real ACK.
                self.accept_receipt(db, instance, row, part, str(rid), content if attachment is None or not eitaa else '')
                sent.append(result)
            except Exception:
                # A native correlated receipt can beat the failed HTTP/WS ACK.
                # Use that positive evidence, including for an intermediate
                # batch part, instead of turning it into a new terminal failure.
                db.refresh(row)
                checkpoint = self.state(row)
                accepted = checkpoint.get('receipts', {}).get(part)
                if (not dispatched_request_id or not accepted or checkpoint.get('cancelled')
                        or checkpoint.get('confirmed_requests', {}).get(part) != dispatched_request_id):
                    raise
                sent.append({'ok': True, 'confirmed': True, 'result': {'message_id' if eitaa else 'rid': accepted}})
            finally:
                await self.bridge._finish_adapter_outbound(runtime.adapter, peer_id)
        state = self.state(row)
        state['complete'] = all(part in state.get('receipts', {}) for part in state['plan'])
        self.save(db, row, state)
        await self.finalize(db, instance, client, config, payload, row)
        return sent

    def accept_receipt(self, db: Any, instance: Any, row: MessageMapping, part: str, rid: str, text: str) -> None:
        state = self.state(row)
        expected = state.get('inflight', {}).get(part)
        if state.get('receipts', {}).get(part) not in (None, rid):
            raise RuntimeError('delivery_ack_mismatch: a confirmed message part cannot change its server identity')
        if expected and expected != rid and state.get('platform_key') != 'eitaa_pv_enterprise':
            raise RuntimeError('delivery_ack_mismatch: Bale receipt belongs to another send')
        state.setdefault('receipts', {})[part] = rid
        if expected:
            state.setdefault('confirmed_requests', {})[part] = expected
        state.setdefault('inflight', {}).pop(part, None)
        state['complete'] = all(key in state['receipts'] for key in state['plan'])
        # The first accepted part owns the Chatwoot parent reference; subsequent
        # parts retain batch metadata so echoes/replies stay linked to this row.
        if part == state.get('primary_part', state['plan'][0]):
            row.platform_message_id = rid
            row.status = MessageStatus.sent
            row.message_kind = MessageKind.text if part == 'text' else MessageKind.media
            row.platform_payload_json = {**(row.platform_payload_json or {}), 'text': text,
                                         'batch_message_id': row.chatwoot_message_id, 'batch_part': part}
        else:
            self.bridge._persist_mapping(db, instance=instance, conversation_id=row.conversation_id,
                direction=MessageDirection.chatwoot_to_platform, message_kind=MessageKind.text if part == 'text' else MessageKind.media,
                chatwoot_message_id=None, platform_message_id=rid, status=MessageStatus.sent,
                platform_payload_json={'text': text, 'batch_message_id': row.chatwoot_message_id, 'batch_part': part})
        self.save(db, row, state)
        if state.get('complete'):
            for member in db.query(MessageMapping).filter_by(conversation_id=row.conversation_id, direction=MessageDirection.chatwoot_to_platform).all():
                if (member.platform_payload_json or {}).get('batch_message_id') == row.chatwoot_message_id:
                    member.platform_payload_json = {**(member.platform_payload_json or {}), 'delivery_complete': True}
            db.commit()

    async def finalize(self, db: Any, instance: Any, client: Any, config: dict[str, Any],
                       payload: dict[str, Any], row: MessageMapping) -> None:
        if self.state(row).get('complete') is not True:
            raise RuntimeError(f'delivery_unconfirmed: not all message parts were accepted by {self.platform_name(instance)}')
        if self.state(row).get('cancelled'):
            return
        try:
            await self.bridge._remove_delivery_failure_notes(db, instance, client, config, payload)
            await client.update_message_status(int(config['account_id']), int(MessagePayloadParser.extract_conversation_id(payload)),
                int(MessagePayloadParser.extract_message_id(payload)), status='sent', external_error='')
            for delivery in self.bridge._delivery_rows(db, instance.instance_key, payload):
                if delivery.status == 'failed' and not (delivery.payload_json or {}).get('_wootify_cancelled'):
                    db.delete(delivery)
            db.commit()
        except Exception as exc:
            raise DeliveryStatusSyncError(f'{self.platform_name(instance)} accepted this message; Chatwoot status/note reconciliation needs retry') from exc

    async def reconcile_echo(self, db: Any, instance: Any, client: Any, config: dict[str, Any],
                             event: dict[str, Any]) -> dict[str, Any] | None:
        if not event.get('outgoing'):
            return None
        rid = str(event.get('platform_message_id') or event.get('message_id') or '')
        candidates = (db.query(MessageMapping).join(Conversation)
                      .filter(Conversation.instance_id == instance.id,
                              Conversation.platform_conversation_id == str(event.get('chat_id')),
                              Conversation.chatwoot_inbox_id == str(config.get('inbox_id')),
                              MessageMapping.direction == MessageDirection.chatwoot_to_platform,
                              MessageMapping.chatwoot_message_id.is_not(None)).all())
        for row in candidates:
            conversation = db.get(Conversation, row.conversation_id)
            payload = {'id': int(row.chatwoot_message_id), 'conversation': {'id': int(conversation.chatwoot_conversation_id)}}
            async with self.message_lock(instance, payload):
                db.refresh(row)
                state = self.state(row)
                part = next((key for key, value in state.get('inflight', {}).items() if value == rid), None)
                if part is None and rid not in state.get('receipts', {}).values():
                    continue
                if part is None:
                    # Already-confirmed updates still belong to the existing
                    # normal duplicate/edit path, not an ACK-only short circuit.
                    if state.get('complete'):
                        await self.finalize(db, instance, client, config, payload, row)
                    return None
                if part is not None:
                    self.accept_receipt(db, instance, row, part, rid, str((row.platform_payload_json or {}).get('text') or ''))
                if self.state(row).get('complete'):
                    await self.finalize(db, instance, client, config, payload, row)
                if event.get('text') is not None and str(event['text']).strip() != str((row.platform_payload_json or {}).get('text') or '').strip():
                    return None
                return {'ok': True, 'duplicate': True, 'chatwoot_message_id': row.chatwoot_message_id, 'confirmation_reconciled': True}
        return None

    async def reconcile_eitaa_confirmation(self, db: Any, instance: Any, client: Any, config: dict[str, Any],
                                           event: dict[str, Any]) -> dict[str, Any]:
        """Only a correlated server update can resolve an Eitaa lost HTTP ACK."""
        request_id = str(event.get('request_id') or '')
        message_id = str(event.get('platform_message_id') or '')
        if not request_id.isdigit() or not message_id.isdigit() or int(message_id) <= 0:
            return {'ok': True, 'ignored': True, 'reason': 'invalid_delivery_confirmation'}
        candidates = (db.query(MessageMapping).join(Conversation)
                      .filter(Conversation.instance_id == instance.id,
                              Conversation.chatwoot_inbox_id == str(config.get('inbox_id')),
                              MessageMapping.direction == MessageDirection.chatwoot_to_platform,
                              MessageMapping.chatwoot_message_id.is_not(None)).all())
        matches = []
        for row in candidates:
            state = self.state(row)
            conversation = db.get(Conversation, row.conversation_id)
            if state.get('platform_key') != 'eitaa_pv_enterprise' or state.get('cancelled'):
                continue
            if event.get('chat_id') and str(event['chat_id']) != conversation.platform_conversation_id:
                continue
            for part, pending_request in state.get('inflight', {}).items():
                if pending_request == request_id:
                    matches.append((row, conversation, part))
            for part, confirmed_request in state.get('confirmed_requests', {}).items():
                if confirmed_request == request_id and state.get('receipts', {}).get(part) == message_id:
                    matches.append((row, conversation, part))
        if len(matches) != 1:
            return {'ok': True, 'ignored': True, 'reason': 'unmatched_delivery_confirmation'}
        row, conversation, part = matches[0]
        payload = {'id': int(row.chatwoot_message_id), 'conversation': {'id': int(conversation.chatwoot_conversation_id)}}
        async with self.message_lock(instance, payload):
            db.refresh(row)
            state = self.state(row)
            pending = state.get('inflight', {}).get(part) == request_id
            acknowledged = state.get('confirmed_requests', {}).get(part) == request_id and state.get('receipts', {}).get(part) == message_id
            if state.get('cancelled') or not (pending or acknowledged):
                return {'ok': True, 'ignored': True, 'reason': 'stale_delivery_confirmation'}
            if pending:
                self.accept_receipt(db, instance, row, part, message_id, str(state.get('content') or '') if part == 'text' else '')
            if self.state(row).get('complete'):
                await self.finalize(db, instance, client, config, payload, row)
            elif not self.state(row).get('inflight'):
                # The uncertain part is now proven sent. Resume only remaining
                # unsent parts, retaining the original bounded attempt budget.
                import datetime as dt
                for delivery in self.bridge._delivery_rows(db, instance.instance_key, payload):
                    if delivery.status == 'failed' and not (delivery.payload_json or {}).get('_wootify_cancelled') and int(delivery.attempts or 0) < 10:
                        stored = dict(delivery.payload_json or {})
                        stored.pop('_wootify_terminal_failure', None)
                        delivery.payload_json = stored
                        delivery.status = 'pending'
                        delivery.next_attempt_at = dt.datetime.utcnow()
                        delivery.last_error = None
                db.commit()
            return {'ok': True, 'confirmation_reconciled': True, 'chatwoot_message_id': row.chatwoot_message_id}
