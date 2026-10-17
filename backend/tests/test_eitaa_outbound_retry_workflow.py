"""Eitaa outbound parity: controlled ACKs, safe queue retries and truthful UI."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import wootify.presentation.http.routers._handler_controller as controller
import wootify.services.chatwoot_bridge_service as bridge_module
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
from wootify.plugins.eitaa_pv.connector import EitaaSendRejectedError
from wootify.infrastructure.persistence.models import (
    Base, ChatwootWebhookDelivery, Conversation, Instance, MessageDirection,
    MessageMapping, MessageKind, MessageStatus, PlatformType,
)


class SafePreDispatchError(ConnectionError):
    retryable = True
    delivery_uncertain = False


class UncertainDispatchError(ConnectionError):
    retryable = False
    delivery_uncertain = True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def eitaa_case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'eitaa-outbound-workflow.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        platform = PlatformType(key="eitaa_pv_enterprise", display_name="Eitaa PV",
            capabilities_json={}, metadata_schema_json={})
        db.add(platform)
        db.flush()
        instance = Instance(instance_key="eitaa-retry-regression", platform_type_id=platform.id,
            is_enabled=True, platform_metadata_encrypted="", chatwoot_config_encrypted="", proxy_config_encrypted="")
        db.add(instance)
        db.flush()
        conversation = Conversation(instance_id=instance.id, platform_conversation_id="6239618",
            chatwoot_conversation_id="7902", chatwoot_contact_id="42", chatwoot_inbox_id="57", is_active=True)
        db.add(conversation)
        db.commit()
        bridge = ChatwootBridgeService()
        bridge._prepare_adapter_outbound = AsyncMock()
        bridge._finish_adapter_outbound = AsyncMock()
        adapter = AsyncMock()
        runtime = SimpleNamespace(status="open", platform_type="eitaa_pv_enterprise", adapter=adapter)
        client = AsyncMock()
        client.base_url = "http://chatwoot"
        client.list_conversation_messages.return_value = {"payload": []}
        client.post_message.return_value = {"id": 50001}
        config = {"account_id": 3, "inbox_id": 57, "base_url": "http://chatwoot"}
        payload = {"event": "message_created", "id": 99001, "message_type": "outgoing", "content": "",
            "conversation": {"id": 7902, "inbox_id": 57, "meta": {"sender": {"id": 42}}}}
        monkeypatch.setattr(bridge_module, "get_runtime", lambda _: runtime)
        monkeypatch.setattr(bridge, "_chatwoot_client_for_instance", lambda *_: (instance, config, client))
        monkeypatch.setattr(controller, "chatwoot_bridge", bridge)
        monkeypatch.setattr(controller, "SessionLocal", factory)
        yield SimpleNamespace(db=db, factory=factory, instance=instance, conversation=conversation,
            bridge=bridge, adapter=adapter, runtime=runtime, client=client, config=config, payload=payload)
    engine.dispose()


def statuses(case):
    return [call.kwargs["status"] for call in case.client.update_message_status.await_args_list]


def media_parts():
    return [{"id": 101, "data_url": "/files/one.jpg", "filename": "one.jpg"},
            {"id": 102, "data_url": "/files/two.pdf", "filename": "two.pdf"}]


def persist(case, payload=None):
    return controller._persist_chatwoot_delivery(case.db, instance_key=case.instance.instance_key,
        platform_key="eitaa_pv_enterprise", route_key=None, payload=payload or case.payload)


async def worker(case, row_id, payload=None):
    await controller._deliver_chatwoot_webhook_background("eitaa_pv_enterprise", case.instance.instance_key,
        payload or case.payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    return case.db.get(ChatwootWebhookDelivery, row_id)


async def acknowledged(options, message_id, random_id):
    # Eitaa's random_id is the durable request key, NOT the resulting Eitaa
    # message ID; the ledger must accept an acknowledged different server ID.
    await options["before_dispatch"](random_id)
    return {"ok": True, "confirmed": True, "result": {
        "message_id": message_id, "random_id": random_id,
        "confirmation_kind": "eitaa_server_message_id"}}


def mapped(case):
    return (case.db.query(MessageMapping).filter_by(conversation_id=case.conversation.id,
        direction=MessageDirection.chatwoot_to_platform, chatwoot_message_id="99001").one())


@pytest.mark.anyio
async def test_eitaa_safe_pre_dispatch_failure_auto_retries_then_marks_sent_and_removes_note(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "sample"
    case.adapter.send_text.side_effect = SafePreDispatchError("Eitaa unavailable before dispatch")
    row_id = persist(case)
    failed = await worker(case, row_id)
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert set(statuses(case)) == {"failed"}
    case.client.delete_delivery_failure_note.assert_not_awaited()
    async def recovered(*args, **options):
        return await acknowledged(options, 301, 100001)
    case.adapter.send_text.side_effect = recovered
    assert await worker(case, row_id) is None
    assert case.adapter.send_text.await_count == 2
    assert statuses(case)[-1] == "sent"
    assert mapped(case).platform_message_id == "301"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(3, 7902, 50001)


@pytest.mark.anyio
async def test_legacy_caption_ack_before_parent_transfer_reuses_existing_caption(eitaa_case):
    case = eitaa_case
    case.payload['content'] = 'caption'
    case.payload['attachments'] = media_parts()
    legacy = []
    for part, server_id in [('media:101', '311'), ('media:102', '312'), ('text', '313')]:
        row = MessageMapping(conversation_id=case.conversation.id,
            direction=MessageDirection.chatwoot_to_platform,
            message_kind=MessageKind.text if part == 'text' else MessageKind.media,
            status=MessageStatus.sent, platform_message_id=server_id,
            chatwoot_message_id='99001' if part == 'media:101' else None,
            platform_payload_json={'text': 'caption' if part == 'text' else '',
                'batch_message_id': '99001', 'batch_part': part, 'delivery_complete': False})
        case.db.add(row)
        legacy.append(row)
    case.db.commit()
    caption_row_id = legacy[-1].id
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key, case.payload)
    assert result['ok'] is True
    case.adapter.send_media.assert_not_awaited()
    case.adapter.send_text.assert_not_awaited()
    canonical = mapped(case)
    assert canonical.id == caption_row_id
    assert canonical.platform_message_id == '313'
    assert case.db.query(MessageMapping).filter_by(conversation_id=case.conversation.id).count() == 3
    state = case.bridge._confirmed_outbound.state(canonical)
    assert state['complete'] is True
    assert state['receipts'] == {'media:101': '311', 'media:102': '312', 'text': '313'}
    assert statuses(case)[-1] == 'sent'


@pytest.mark.anyio
async def test_eitaa_uncertain_dispatch_is_terminal_and_explicit_retry_cannot_repeat_it(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "sample"
    async def uncertain(*args, **options):
        await options["before_dispatch"](100002)
        raise UncertainDispatchError("Eitaa ACK response lost")
    case.adapter.send_text.side_effect = uncertain
    row_id = persist(case)
    failed = await worker(case, row_id)
    assert failed.status == "failed"
    assert failed.next_attempt_at is None
    assert set(statuses(case)) == {"failed"}
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "retry-uncertain"}
    assert persist(case, retry) == row_id
    again = await worker(case, row_id, retry)
    assert again.status == "failed"
    assert case.adapter.send_text.await_count == 1
    case.client.delete_delivery_failure_note.assert_not_awaited()
    assert "sent" not in statuses(case)


@pytest.mark.anyio
async def test_eitaa_media_parts_then_separate_caption_all_require_ack_and_retry_resumes_only_missing(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "caption"
    case.payload["attachments"] = media_parts()
    sent_urls = []
    async def media(peer, url, **options):
        assert options["caption"] is None
        sent_urls.append(url)
        assert "sent" not in statuses(case)
        if url.endswith("two.pdf") and len(sent_urls) == 2:
            raise SafePreDispatchError("temporary upload failure before dispatch")
        return await acknowledged(options, 311 if url.endswith("one.jpg") else 312,
                                   100011 if url.endswith("one.jpg") else 100012)
    async def caption(peer, text, **options):
        assert len(sent_urls) == 3
        assert text == "caption"
        assert "sent" not in statuses(case)
        return await acknowledged(options, 313, 100013)
    case.adapter.send_media.side_effect = media
    case.adapter.send_text.side_effect = caption
    row_id = persist(case)
    failed = await worker(case, row_id)
    assert failed.status == "pending"
    assert set(statuses(case)) == {"failed"}
    case.adapter.send_text.assert_not_awaited()
    case.client.delete_delivery_failure_note.assert_not_awaited()
    assert await worker(case, row_id) is None
    assert sent_urls == ["http://chatwoot/files/one.jpg", "http://chatwoot/files/two.pdf", "http://chatwoot/files/two.pdf"]
    assert case.adapter.send_text.await_count == 1
    assert statuses(case)[-1] == "sent"
    assert mapped(case).platform_message_id == "313"  # Later caption edits address the text, not first photo.
    rows = case.db.query(MessageMapping).filter_by(conversation_id=case.conversation.id).all()
    assert {row.platform_message_id for row in rows} == {"311", "312", "313"}
    case.client.delete_delivery_failure_note.assert_awaited_once_with(3, 7902, 50001)


@pytest.mark.anyio
async def test_eitaa_explicit_retry_rearms_terminal_failure_only_once_per_request(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "sample"
    case.adapter.send_text.side_effect = RuntimeError("recipient temporarily rejected sending")
    row_id = persist(case)
    terminal = await worker(case, row_id)
    assert terminal.status == "failed"
    original_attempts = terminal.attempts
    assert persist(case, deepcopy(case.payload)) == row_id
    case.db.refresh(terminal)
    assert terminal.status == "failed"
    assert terminal.attempts == original_attempts
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "intentional-eitaa-retry"}
    assert persist(case, retry) == row_id
    case.db.refresh(terminal)
    assert terminal.status == "pending"
    assert terminal.attempts == 0
    async def recovered(*args, **options):
        return await acknowledged(options, 321, 100021)
    case.adapter.send_text.side_effect = recovered
    assert await worker(case, row_id, retry) is None
    assert case.adapter.send_text.await_count == 2
    assert statuses(case)[-1] == "sent"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(3, 7902, 50001)


@pytest.mark.anyio
async def test_eitaa_status_sync_retry_after_ack_never_repeats_platform_send(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "sample"
    async def accepted(*args, **options):
        return await acknowledged(options, 331, 100031)
    case.adapter.send_text.side_effect = accepted
    chatwoot_available = False
    async def status(*args, **options):
        if options["status"] == "sent" and not chatwoot_available:
            raise ConnectionError("Chatwoot status update unavailable")
    case.client.update_message_status.side_effect = status
    row_id = persist(case)
    pending = await worker(case, row_id)
    assert pending.status == "pending"
    assert case.adapter.send_text.await_count == 1
    chatwoot_available = True
    assert await worker(case, row_id) is None
    assert case.adapter.send_text.await_count == 1
    assert statuses(case)[-1] == "sent"
    assert mapped(case).platform_message_id == "331"


@pytest.mark.anyio
async def test_eitaa_failed_note_cleanup_retries_only_bookkeeping_after_media_and_caption_ack(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "caption"
    case.payload["attachments"] = media_parts()[:1]
    case.adapter.send_media.side_effect = SafePreDispatchError("upload unavailable before dispatch")
    row_id = persist(case)
    assert (await worker(case, row_id)).status == "pending"
    case.client.delete_delivery_failure_note.assert_not_awaited()
    async def media(*args, **options):
        return await acknowledged(options, 341, 100041)
    async def text(*args, **options):
        return await acknowledged(options, 342, 100042)
    case.adapter.send_media.side_effect = media
    case.adapter.send_text.side_effect = text
    cleanup_available = False
    async def cleanup(*args, **options):
        assert case.adapter.send_text.await_count == 1
        if not cleanup_available:
            raise ConnectionError("Chatwoot note cleanup unavailable")
    case.client.delete_delivery_failure_note.side_effect = cleanup
    assert (await worker(case, row_id)).status == "pending"
    assert case.adapter.send_media.await_count == 2
    assert case.adapter.send_text.await_count == 1
    assert "sent" not in statuses(case)
    cleanup_available = True
    assert await worker(case, row_id) is None
    assert case.adapter.send_media.await_count == 2
    assert case.adapter.send_text.await_count == 1
    assert statuses(case)[-1] == "sent"
    assert case.client.delete_delivery_failure_note.await_args.args == (3, 7902, 50001)


def test_eitaa_pending_retry_click_is_consumed_and_cannot_refill_later_exhausted_budget(eitaa_case):
    case = eitaa_case
    row_id = persist(case)
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "clicked-while-pending"}
    assert persist(case, retry) == row_id
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.attempts = controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    row.next_attempt_at = None
    row.payload_json = {**row.payload_json, "_wootify_failure_note_ids": [50001]}
    case.db.commit()
    assert persist(case, retry) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    assert persist(case, {**retry, "retry_request_id": "fresh-click-after-exhaustion"}) == row_id
    case.db.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.payload_json["_wootify_failure_note_ids"] == [50001]


def test_eitaa_retry_without_request_id_cannot_rearm_and_cancelled_job_rejects_even_fresh_retry(eitaa_case):
    case = eitaa_case
    row_id = persist(case)
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.next_attempt_at = None
    case.db.commit()
    assert persist(case, {**case.payload, "wootify_retry": True}) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    row.payload_json = {**row.payload_json, "_wootify_cancelled": True}
    case.db.commit()
    assert persist(case, {**case.payload, "wootify_retry": True, "retry_request_id": "fresh-but-cancelled"}) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.payload_json["_wootify_cancelled"] is True


async def uncertain_text(case, random_id=100051):
    case.payload["content"] = "sample"
    async def uncertain(*args, **options):
        await options["before_dispatch"](random_id)
        raise UncertainDispatchError("Eitaa accepted state unknown after lost ACK")
    case.adapter.send_text.side_effect = uncertain
    row_id = persist(case)
    assert (await worker(case, row_id)).status == "failed"
    return row_id


@pytest.mark.anyio
async def test_eitaa_exact_server_request_correlation_resolves_lost_ack_and_removes_note(eitaa_case):
    case = eitaa_case
    row_id = await uncertain_text(case)
    # updateMessageID need not contain a peer. It can resolve only one exact
    # instance+inbox-scoped request ID; it is not an ordinary incoming message.
    result = await case.bridge.ingest_platform_event(case.db, case.instance.instance_key,
        {"delivery_confirmation": True, "request_id": "100051", "platform_message_id": "351"})
    assert result["confirmation_reconciled"] is True
    assert statuses(case)[-1] == "sent"
    assert case.adapter.send_text.await_count == 1
    assert mapped(case).platform_message_id == "351"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(3, 7902, 50001)
    assert case.db.get(ChatwootWebhookDelivery, row_id) is None


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [
    {"request_id": "not-the-request"},
    {"request_id": "100052"},
    {"chat_id": "999"},
    {"platform_message_id": "0"},
    {"platform_message_id": "-1"},
])
async def test_eitaa_uncorrelated_confirmation_cannot_mark_uncertain_message_sent(eitaa_case, changes):
    case = eitaa_case
    await uncertain_text(case)
    result = await case.bridge.ingest_platform_event(case.db, case.instance.instance_key,
        {"delivery_confirmation": True, "request_id": "100051", "platform_message_id": "351", **changes})
    assert result["ignored"] is True
    assert "sent" not in statuses(case)
    assert mapped(case).platform_message_id is None
    case.client.delete_delivery_failure_note.assert_not_awaited()
    assert case.adapter.send_text.await_count == 1


@pytest.mark.anyio
async def test_eitaa_confirmation_never_borrows_a_pending_request_from_another_instance(eitaa_case):
    case = eitaa_case
    await uncertain_text(case)
    foreign = Instance(instance_key="foreign-eitaa", platform_type_id=case.instance.platform_type_id,
        is_enabled=True, platform_metadata_encrypted="", chatwoot_config_encrypted="", proxy_config_encrypted="")
    case.db.add(foreign)
    case.db.commit()
    result = await case.bridge._confirmed_outbound.reconcile_eitaa_confirmation(case.db, foreign,
        case.client, case.config, {"request_id": "100051", "platform_message_id": "351"})
    assert result["reason"] == "unmatched_delivery_confirmation"
    assert "sent" not in statuses(case)
    case.client.delete_delivery_failure_note.assert_not_awaited()


@pytest.mark.anyio
async def test_eitaa_ambiguous_request_id_cannot_confirm_either_message(eitaa_case):
    case = eitaa_case
    await uncertain_text(case)
    original = mapped(case)
    second_conversation = Conversation(instance_id=case.instance.id, platform_conversation_id="6239618",
        chatwoot_conversation_id="7903", chatwoot_contact_id="42", chatwoot_inbox_id="57", is_active=False)
    case.db.add(second_conversation)
    case.db.flush()
    case.db.add(MessageMapping(conversation_id=second_conversation.id,
        direction=MessageDirection.chatwoot_to_platform, chatwoot_message_id="99002",
        message_kind=original.message_kind, status=original.status,
        platform_payload_json=deepcopy(original.platform_payload_json)))
    case.db.commit()
    result = await case.bridge.ingest_platform_event(case.db, case.instance.instance_key,
        {"delivery_confirmation": True, "request_id": "100051", "platform_message_id": "351"})
    assert result["reason"] == "unmatched_delivery_confirmation"
    assert "sent" not in statuses(case)
    assert mapped(case).platform_message_id is None
    case.client.delete_delivery_failure_note.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("runtime_available", [True, False], ids=["before-upload", "offline-no-mapping"])
async def test_eitaa_deleted_failed_attachment_cancels_queue_and_refuses_new_retry(eitaa_case, monkeypatch, runtime_available):
    case = eitaa_case
    case.payload["attachments"] = media_parts()[:1]
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime if runtime_available else None)
    case.adapter.send_media.side_effect = SafePreDispatchError("upload unavailable before dispatch")
    row_id = persist(case)
    assert (await worker(case, row_id)).status == "pending"
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key,
        {**case.payload, "event": "message_updated", "content_attributes": {"deleted": True}})
    assert result["ok"] is True
    queue = case.db.get(ChatwootWebhookDelivery, row_id)
    case.db.refresh(queue)
    assert queue.payload_json["_wootify_cancelled"] is True
    assert queue.status == "failed"
    assert queue.next_attempt_at is None
    if runtime_available:
        assert case.bridge._confirmed_outbound.state(mapped(case))["cancelled"] is True
    else:
        assert case.db.query(MessageMapping).count() == 0
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "fresh-but-deleted"}
    assert persist(case, retry) == row_id
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    assert (await worker(case, row_id, retry)).status == "failed"
    assert case.adapter.send_media.await_count == int(runtime_available)
    assert "sent" not in statuses(case)


@pytest.mark.anyio
async def test_eitaa_flood_wait_schedules_no_earlier_than_server_retry_after(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "sample"
    async def limited(*args, **options):
        await options["before_dispatch"](100060)
        raise EitaaSendRejectedError("FLOOD_WAIT_120", retryable=True, retry_after_seconds=120)
    case.adapter.send_text.side_effect = limited
    row_id = persist(case)
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    pending = await worker(case, row_id)
    assert pending.status == "pending"
    assert pending.attempts == 1
    assert pending.next_attempt_at >= started + timedelta(seconds=120)
    assert case.bridge._confirmed_outbound.state(mapped(case))["inflight"] == {}
    assert case.adapter.send_text.await_count == 1
    assert "sent" not in statuses(case)
    case.client.delete_delivery_failure_note.assert_not_awaited()


@pytest.mark.anyio
async def test_eitaa_intermediate_confirmation_rearms_only_remaining_parts_without_refilling_budget(eitaa_case):
    case = eitaa_case
    case.payload["content"] = "caption"
    case.payload["attachments"] = media_parts()
    urls = []
    async def uncertain_media(peer, url, **options):
        urls.append(url)
        await options["before_dispatch"](100061)
        raise UncertainDispatchError("first photo ACK response lost")
    case.adapter.send_media.side_effect = uncertain_media
    row_id = persist(case)
    failed = await worker(case, row_id)
    assert failed.status == "failed"
    failed.attempts = 4
    case.db.commit()
    result = await case.bridge.ingest_platform_event(case.db, case.instance.instance_key,
        {"delivery_confirmation": True, "request_id": "100061", "platform_message_id": "361"})
    assert result["confirmation_reconciled"] is True
    case.db.refresh(failed)
    assert failed.status == "pending"
    assert failed.attempts == 4
    assert failed.next_attempt_at is not None
    assert "_wootify_terminal_failure" not in failed.payload_json
    case.client.delete_delivery_failure_note.assert_not_awaited()
    assert "sent" not in statuses(case)
    async def remaining_media(peer, url, **options):
        urls.append(url)
        assert url.endswith("two.pdf")
        assert "sent" not in statuses(case)
        return await acknowledged(options, 362, 100062)
    async def caption(peer, text, **options):
        assert text == "caption"
        assert "sent" not in statuses(case)
        return await acknowledged(options, 363, 100063)
    case.adapter.send_media.side_effect = remaining_media
    case.adapter.send_text.side_effect = caption
    assert await worker(case, row_id) is None
    assert urls == ["http://chatwoot/files/one.jpg", "http://chatwoot/files/two.pdf"]
    assert case.adapter.send_media.await_count == 2
    assert case.adapter.send_text.await_count == 1
    assert mapped(case).platform_message_id == "363"
    assert statuses(case)[-1] == "sent"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(3, 7902, 50001)
