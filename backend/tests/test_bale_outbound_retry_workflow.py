"""Durable Bale sending: truthful status, safe retries and per-part receipts.

All sends below are controlled fakes. Nothing reaches a customer or a network.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bale_pv_connector.exceptions import (
    BaleRpcError,
    BaleRequestNotDispatchedError,
    BaleRequestUnconfirmedError,
)
from wootify.application.messaging.bale_outbound_delivery import (
    DeliveryStatusSyncError,
    FailureNoteScanPending,
    safe_delivery_retry,
)
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
import wootify.services.chatwoot_bridge_service as bridge_module
from wootify.infrastructure.persistence.models import (
    ChatwootWebhookDelivery,
    Base,
    Conversation,
    Instance,
    MessageMapping,
    PlatformType,
)
import wootify.presentation.http.routers._handler_controller as controller


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_delivery_case(db_session):
    platform = PlatformType(key="bale_pv_enterprise", display_name="Bale PV",
                            capabilities_json={}, metadata_schema_json={})
    db_session.add(platform)
    db_session.flush()
    instance = Instance(instance_key="bale-retry-regression", platform_type_id=platform.id,
                        is_enabled=True, platform_metadata_encrypted="",
                        chatwoot_config_encrypted="", proxy_config_encrypted="")
    db_session.add(instance)
    db_session.flush()
    conversation = Conversation(instance_id=instance.id, platform_conversation_id="123",
                                chatwoot_conversation_id="77", chatwoot_contact_id="42",
                                chatwoot_inbox_id="5", is_active=True)
    db_session.add(conversation)
    db_session.commit()
    bridge = ChatwootBridgeService()
    bridge._prepare_adapter_outbound = AsyncMock()
    bridge._finish_adapter_outbound = AsyncMock()
    bridge._remove_delivery_failure_notes = AsyncMock()
    adapter = AsyncMock()
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    payload = {"event": "message_created", "id": 9001, "message_type": "outgoing",
               "content": "", "conversation": {"id": 77, "inbox_id": 5,
                   "meta": {"sender": {"id": 42, "identifier": "BALE_PV:USER:123"}}}}
    return SimpleNamespace(db=db_session, instance=instance, conversation=conversation,
                           bridge=bridge, delivery=bridge._bale_outbound, adapter=adapter,
                           runtime=SimpleNamespace(status="open", platform_type="bale_pv_enterprise", adapter=adapter),
                           client=client, config={"account_id": 1, "inbox_id": 5, "base_url": "http://chatwoot"},
                           payload=payload)


@pytest.fixture
def delivery_case(db_session):
    return make_delivery_case(db_session)


@pytest.fixture
def integration_case(tmp_path):
    # Full bridge failures perform rollback; independently committed fixtures
    # reproduce production transaction semantics instead of rolling back the
    # suite's outer fixture transaction and erasing the instance itself.
    engine = create_engine(f"sqlite:///{tmp_path / 'bale-retry-integration.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as db:
        yield make_delivery_case(db)
    engine.dispose()


async def deliver(case, attachments=None, content=""):
    return await case.delivery.deliver(case.db, case.instance, case.runtime, case.client,
                                      case.config, "123", case.payload, content=content,
                                      attachments=attachments or [], reply_to=None)


def media_parts():
    return [{"id": 11, "data_url": "/files/one.ogg", "filename": "one.ogg"},
            {"id": 12, "data_url": "/files/two.ogg", "filename": "two.ogg"}]


def statuses(case):
    return [call.kwargs["status"] for call in case.client.update_message_status.await_args_list]


async def accepted(case, rid, options):
    await options["before_dispatch"](rid)
    # The dispatch checkpoint is committed before the mocked network accepts it.
    assert case.db.in_transaction() is False
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    assert str(rid) in case.delivery.state(row)["inflight"].values()
    return {"ok": True, "confirmed": True, "result": {"rid": rid}}


@pytest.mark.parametrize("error, expected", [
    (BaleRequestNotDispatchedError("transport unavailable before dispatch"), True),
    (BaleRequestUnconfirmedError("ack timed out after dispatch"), False),
    (ConnectionError("unknown socket state"), False),
    (TimeoutError("unknown timeout"), False),
    (DeliveryStatusSyncError("status update failed after ACK"), True),
])
def test_only_proven_pre_dispatch_or_bookkeeping_failures_are_safe_to_retry(error, expected):
    assert safe_delivery_retry(error) is expected


def test_uncertain_inner_cause_overrides_outer_retryable_marker():
    outer = DeliveryStatusSyncError("wrapped transport error")
    outer.__cause__ = BaleRequestUnconfirmedError("send may have succeeded")
    assert safe_delivery_retry(outer) is False


@pytest.mark.anyio
async def test_pre_dispatch_failure_keeps_message_red_and_retry_has_no_duplicate_receipt(delivery_case):
    case = delivery_case
    case.adapter.send_media.side_effect = BaleRequestNotDispatchedError("set-cookie unavailable")
    with pytest.raises(BaleRequestNotDispatchedError):
        await deliver(case, media_parts()[:1])
    assert statuses(case) == ["failed"]
    assert case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))["inflight"] == {}

    async def recovered(*args, **options):
        return await accepted(case, 5001, options)
    case.adapter.send_media.side_effect = recovered
    await deliver(case, media_parts()[:1])
    assert case.adapter.send_media.await_count == 2
    assert statuses(case) == ["failed", "failed", "sent"]
    state = case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))
    assert state["receipts"] == {"media:11": "5001"}
    assert state["complete"] is True
    case.bridge._remove_delivery_failure_notes.assert_awaited_once()


@pytest.mark.anyio
async def test_uncertain_send_cannot_be_repeated_by_manual_or_automatic_retry(delivery_case):
    case = delivery_case
    async def uncertain(*args, **options):
        await options["before_dispatch"](5002)
        raise BaleRequestUnconfirmedError("Bale did not return an ACK")
    case.adapter.send_media.side_effect = uncertain
    with pytest.raises(BaleRequestUnconfirmedError):
        await deliver(case, media_parts()[:1])
    with pytest.raises(RuntimeError, match="delivery_unconfirmed"):
        await deliver(case, media_parts()[:1])
    assert case.adapter.send_media.await_count == 1
    assert statuses(case) == ["failed"]
    case.bridge._remove_delivery_failure_notes.assert_not_awaited()


@pytest.mark.anyio
async def test_every_attachment_needs_ack_and_retry_skips_already_accepted_parts(delivery_case):
    case = delivery_case
    calls = []
    async def partial(peer, url, **options):
        calls.append(url)
        assert "sent" not in statuses(case)
        if url.endswith("two.ogg"):
            raise BaleRequestNotDispatchedError("temporary upload connection failure")
        return await accepted(case, 5003, options)
    case.adapter.send_media.side_effect = partial
    with pytest.raises(BaleRequestNotDispatchedError):
        await deliver(case, media_parts())
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    assert case.delivery.state(row)["receipts"] == {"media:11": "5003"}
    assert case.delivery.state(row)["complete"] is False
    assert statuses(case) == ["failed"]

    async def recovered(peer, url, **options):
        calls.append(url)
        assert url.endswith("two.ogg")
        assert "sent" not in statuses(case)
        return await accepted(case, 5004, options)
    case.adapter.send_media.side_effect = recovered
    await deliver(case, media_parts())
    assert calls == ["http://chatwoot/files/one.ogg", "http://chatwoot/files/two.ogg", "http://chatwoot/files/two.ogg"]
    assert statuses(case) == ["failed", "failed", "sent"]
    assert case.delivery.state(row)["receipts"] == {"media:11": "5003", "media:12": "5004"}
    assert case.delivery.state(row)["complete"] is True
    assert case.db.query(MessageMapping).filter_by(conversation_id=case.conversation.id).count() == 2


@pytest.mark.anyio
async def test_attachment_id_and_fallback_index_cannot_share_the_same_checkpoint(delivery_case):
    case = delivery_case
    parts = [{"id": 1, "data_url": "/files/first.ogg", "filename": "first.ogg"},
             {"data_url": "/files/second.ogg", "filename": "second.ogg"}]
    keys = []
    async def confirmed(*args, **options):
        keys.append(options["idempotency_key"])
        return await accepted(case, 5200 + len(keys), options)
    case.adapter.send_media.side_effect = confirmed
    await deliver(case, parts)
    assert case.adapter.send_media.await_count == 2
    assert len(set(keys)) == 2
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    state = case.delivery.state(row)
    assert state["plan"] == ["media:1", "media:index:1"]
    assert state["receipts"] == {"media:1": "5201", "media:index:1": "5202"}
    assert statuses(case) == ["failed", "sent"]


@pytest.mark.anyio
async def test_generated_rid_without_ack_is_not_a_success(delivery_case):
    case = delivery_case
    async def unconfirmed(*args, **options):
        await options["before_dispatch"](5005)
        return {"ok": True, "result": {"rid": 5005}}
    case.adapter.send_text.side_effect = unconfirmed
    with pytest.raises(RuntimeError, match="delivery_unconfirmed"):
        await deliver(case, content="sample")
    assert statuses(case) == ["failed"]
    assert case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))["complete"] is False


@pytest.mark.anyio
async def test_echo_confirms_only_exact_peer_and_request_rid(delivery_case):
    case = delivery_case
    async def uncertain(*args, **options):
        await options["before_dispatch"](5006)
        raise BaleRequestUnconfirmedError("ack missing")
    case.adapter.send_text.side_effect = uncertain
    with pytest.raises(BaleRequestUnconfirmedError):
        await deliver(case, content="sample")
    for event in [{"outgoing": True, "chat_id": "999", "platform_message_id": "5006"},
                  {"outgoing": True, "chat_id": "123", "platform_message_id": "9000"},
                  {"outgoing": False, "chat_id": "123", "platform_message_id": "5006"}]:
        assert await case.delivery.reconcile_echo(case.db, case.instance, case.client, case.config, event) is None
    assert statuses(case) == ["failed"]
    result = await case.delivery.reconcile_echo(case.db, case.instance, case.client, case.config,
                      {"outgoing": True, "chat_id": "123", "platform_message_id": "5006"})
    assert result["confirmation_reconciled"] is True
    assert statuses(case) == ["failed", "sent"]
    assert case.adapter.send_text.await_count == 1
    case.bridge._remove_delivery_failure_notes.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("broken_bookkeeping", ["note_cleanup", "sent_status"])
async def test_chatwoot_bookkeeping_failure_retries_without_resending_bale(delivery_case, broken_bookkeeping):
    case = delivery_case
    async def confirmed(*args, **options):
        return await accepted(case, 5007, options)
    case.adapter.send_text.side_effect = confirmed
    if broken_bookkeeping == "note_cleanup":
        case.bridge._remove_delivery_failure_notes.side_effect = [ConnectionError("Chatwoot down"), None]
    else:
        async def status(account, conversation, message, **options):
            if options["status"] == "sent" and sum(call.kwargs["status"] == "sent" for call in case.client.update_message_status.await_args_list) == 1:
                raise ConnectionError("Chatwoot down")
        case.client.update_message_status.side_effect = status
    with pytest.raises(DeliveryStatusSyncError):
        await deliver(case, content="sample")
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    assert case.delivery.state(row)["complete"] is True
    await deliver(case, content="sample")
    assert case.adapter.send_text.await_count == 1
    assert statuses(case)[-1] == "sent"


def add_queue_note_ledger(case, *, message_id=9001, note_ids=None):
    row = ChatwootWebhookDelivery(delivery_key=f"regression-note-{message_id}",
        instance_key=case.instance.instance_key, platform_key="bale_pv_enterprise",
        payload_json={**case.payload, "id": message_id, "_wootify_failure_note_ids": note_ids or []},
        status="pending", attempts=0)
    case.db.add(row)
    case.db.commit()
    return row


@pytest.mark.anyio
async def test_failure_note_is_removed_only_after_real_ack_and_only_for_original_message(delivery_case):
    case = delivery_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["_wootify_instance_key"] = case.instance.instance_key
    queue = add_queue_note_ledger(case)
    unrelated = add_queue_note_ledger(case, message_id=9002, note_ids=[9502])
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.return_value = {"id": 9501}
    await case.bridge._notify_delivery_failure(case.client, 1, case.payload, "123",
        BaleRequestNotDispatchedError("cookie handshake failed"),
        platform_type="bale_pv_enterprise", db=case.db)
    assert queue.payload_json["_wootify_failure_note_ids"] == [9501]
    case.client.delete_delivery_failure_note.assert_not_awaited()
    note = case.client.post_message.await_args.args[2]
    assert note["private"] is True
    assert note["content_attributes"]["wootify_delivery_failure"] == {
        "instance_key": case.instance.instance_key, "message_id": "9001"}
    async def confirmed(*args, **options):
        return await accepted(case, 5008, options)
    case.adapter.send_text.side_effect = confirmed
    await deliver(case, content="sample")
    case.client.delete_delivery_failure_note.assert_awaited_once_with(1, 77, 9501)
    assert queue.payload_json["_wootify_failure_note_ids"] == []
    assert unrelated.payload_json["_wootify_failure_note_ids"] == [9502]
    assert statuses(case)[-1] == "sent"


@pytest.mark.anyio
async def test_lost_note_response_is_recovered_by_private_metadata_not_note_text(delivery_case):
    case = delivery_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["_wootify_instance_key"] = case.instance.instance_key
    queue = add_queue_note_ledger(case)
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.side_effect = ConnectionError("note response lost")
    with pytest.raises(DeliveryStatusSyncError):
        await case.bridge._notify_delivery_failure(case.client, 1, case.payload, "123",
            BaleRequestNotDispatchedError("cookie handshake failed"),
            platform_type="bale_pv_enterprise", db=case.db)
    assert queue.payload_json["_wootify_failure_note_attempted"] is True
    marker = {"instance_key": case.instance.instance_key, "message_id": "9001"}
    case.client.list_conversation_messages.side_effect = [{"payload": [
        {"id": 9601, "private": True, "content_attributes": {"wootify_delivery_failure": marker}},
        {"id": 9602, "private": True, "content": "Delivery to Bale PV failed", "content_attributes": {}},
        {"id": 9603, "private": False, "content_attributes": {"wootify_delivery_failure": marker}},
        {"id": 9604, "private": True, "content_attributes": {"wootify_delivery_failure": {**marker, "message_id": "9002"}}},
    ]}, {"payload": []}]
    async def confirmed(*args, **options):
        return await accepted(case, 5009, options)
    case.adapter.send_text.side_effect = confirmed
    await deliver(case, content="sample")
    case.client.delete_delivery_failure_note.assert_awaited_once_with(1, 77, 9601)
    assert queue.payload_json.get("_wootify_failure_note_attempted") is None


@pytest.mark.anyio
async def test_more_than_five_note_pages_preserve_cursor_and_resume_cleanup_without_resend(delivery_case):
    case = delivery_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    queue = add_queue_note_ledger(case)
    queue.payload_json = {**queue.payload_json, "_wootify_failure_note_attempted": True}
    case.db.commit()
    marker = {"instance_key": case.instance.instance_key, "message_id": "9001"}
    def page(note_id, tagged=False):
        return {"payload": [{"id": note_id, "private": tagged,
            "content_attributes": {"wootify_delivery_failure": marker} if tagged else {}}]}
    case.client.list_conversation_messages.side_effect = [
        page(10000, tagged=True), page(9900), page(9800), page(9700), page(9600),
        page(9500, tagged=True), page(9001),
    ]
    async def confirmed(*args, **options):
        return await accepted(case, 5203, options)
    case.adapter.send_text.side_effect = confirmed
    with pytest.raises(DeliveryStatusSyncError) as failed_cleanup:
        await deliver(case, content="sample")
    assert isinstance(failed_cleanup.value.__cause__, FailureNoteScanPending)
    assert queue.payload_json["_wootify_failure_note_ids"] == [10000]
    assert queue.payload_json["_wootify_failure_note_scan_before"] == 9600
    assert queue.payload_json["_wootify_failure_note_attempted"] is True
    assert case.client.list_conversation_messages.await_count == 5
    case.client.delete_delivery_failure_note.assert_not_awaited()
    assert "sent" not in statuses(case)
    await deliver(case, content="sample")
    assert case.adapter.send_text.await_count == 1
    resumed = case.client.list_conversation_messages.await_args_list[5]
    assert resumed.kwargs == {"before": 9600}
    assert [call.args[2] for call in case.client.delete_delivery_failure_note.await_args_list] == [9500, 10000]
    assert queue.payload_json["_wootify_failure_note_ids"] == []
    assert "_wootify_failure_note_scan_before" not in queue.payload_json
    assert "_wootify_failure_note_attempted" not in queue.payload_json
    assert statuses(case)[-1] == "sent"


@pytest.mark.anyio
async def test_bridge_pre_dispatch_failure_retry_delivers_voice_and_removes_its_note(integration_case, monkeypatch):
    case = integration_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["attachments"] = media_parts()[:1]
    queued = add_queue_note_ledger(case)
    case.client.post_message.return_value = {"id": 9701}
    case.client.list_conversation_messages.return_value = {"payload": []}
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    case.adapter.send_media.side_effect = BaleRequestNotDispatchedError("set-cookie failed")
    failed = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key, case.payload)
    assert failed["ok"] is False
    assert failed["retryable"] is True
    assert queued.payload_json["_wootify_failure_note_ids"] == [9701]
    assert set(statuses(case)) == {"failed"}
    async def confirmed(*args, **options):
        return await accepted(case, 5010, options)
    case.adapter.send_media.side_effect = confirmed
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key,
                                                    {**case.payload, "wootify_retry": True})
    assert result["ok"] is True
    assert case.adapter.send_media.await_count == 2
    assert statuses(case)[-1] == "sent"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(1, 77, 9701)


@pytest.mark.anyio
async def test_native_echo_before_lost_ack_is_authoritative_and_cannot_turn_red_again(integration_case, monkeypatch):
    case = integration_case
    case.payload["content"] = "sample"
    case.client.list_conversation_messages.return_value = {"payload": []}
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    async def echo_before_ack_timeout(*args, **options):
        await options["before_dispatch"](5011)
        await case.delivery.reconcile_echo(case.db, case.instance, case.client, case.config,
            {"outgoing": True, "chat_id": "123", "platform_message_id": "5011"})
        raise BaleRequestUnconfirmedError("ACK lost after independently verified outgoing echo")
    case.adapter.send_text.side_effect = echo_before_ack_timeout
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key, case.payload)
    assert result["ok"] is True
    assert statuses(case)[-1] == "sent"
    assert statuses(case).count("failed") == 1  # Only the initial waiting-for-confirmation state.
    case.client.post_message.assert_not_awaited()
    assert case.adapter.send_text.await_count == 1
    assert case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))["complete"] is True


@pytest.mark.anyio
async def test_concurrent_failure_report_then_matching_echo_finishes_sent_and_removes_note(integration_case, monkeypatch):
    case = integration_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["content"] = "sample"
    queued = add_queue_note_ledger(case)
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.return_value = {"id": 9901}
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    reporting_started = asyncio.Event()
    release_reporting = asyncio.Event()
    async def uncertain(*args, **options):
        await options["before_dispatch"](5013)
        raise BaleRequestUnconfirmedError("ACK response missing")
    async def status(*args, **options):
        if "BaleRequestUnconfirmedError" in options.get("external_error", ""):
            reporting_started.set()
            await release_reporting.wait()
    case.adapter.send_text.side_effect = uncertain
    case.client.update_message_status.side_effect = status
    delivery_task = asyncio.create_task(case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key, case.payload))
    try:
        await asyncio.wait_for(reporting_started.wait(), timeout=5)
        echo_task = asyncio.create_task(case.delivery.reconcile_echo(case.db, case.instance, case.client, case.config,
            {"outgoing": True, "chat_id": "123", "platform_message_id": "5013"}))
        await asyncio.sleep(0)
        assert echo_task.done() is False  # The report owns the same message lock.
        release_reporting.set()
        await asyncio.wait_for(delivery_task, timeout=5)
        result = await asyncio.wait_for(echo_task, timeout=5)
    finally:
        release_reporting.set()
        if not delivery_task.done():
            delivery_task.cancel()
            await asyncio.gather(delivery_task, return_exceptions=True)
    assert result["confirmation_reconciled"] is True
    assert statuses(case)[-1] == "sent"
    assert queued.payload_json["_wootify_failure_note_ids"] == []
    case.client.delete_delivery_failure_note.assert_awaited_once_with(1, 77, 9901)
    assert case.adapter.send_text.await_count == 1


@pytest.mark.anyio
async def test_terminal_failure_reporting_retry_never_repeats_platform_send(integration_case, monkeypatch):
    case = integration_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["attachments"] = media_parts()[:1]
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    monkeypatch.setattr(controller, "chatwoot_bridge", case.bridge)
    monkeypatch.setattr(controller, "SessionLocal", sessionmaker(bind=case.db.bind, autocommit=False, autoflush=False))
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.side_effect = [ConnectionError("failed-note response unavailable"), {"id": 9801}]
    case.adapter.send_media.side_effect = RuntimeError("7: PermissionDenied")
    row_id = persist(case, case.payload)
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        case.payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    assert row.status == "pending"  # Retry Chatwoot reporting, not the rejected send.
    assert case.adapter.send_media.await_count == 1
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        case.payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    assert case.db.get(ChatwootWebhookDelivery, row_id).status == "failed"
    assert case.adapter.send_media.await_count == 1
    assert set(statuses(case)) == {"failed"}
    assert case.client.post_message.await_count == 2


@pytest.mark.anyio
async def test_definitive_negative_ack_clears_uncertainty_but_requires_explicit_retry(integration_case, monkeypatch):
    case = integration_case
    del case.bridge.__dict__["_remove_delivery_failure_notes"]
    case.payload["attachments"] = media_parts()[:1]
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    monkeypatch.setattr(controller, "chatwoot_bridge", case.bridge)
    monkeypatch.setattr(controller, "SessionLocal", sessionmaker(bind=case.db.bind, autocommit=False, autoflush=False))
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.return_value = {"id": 9951}
    async def rejected(*args, **options):
        await options["before_dispatch"](5204)
        raise BaleRpcError("7: PermissionDenied", code=7, delivery_rejected=True)
    case.adapter.send_media.side_effect = rejected
    row_id = persist(case, case.payload)
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        case.payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    queue = case.db.get(ChatwootWebhookDelivery, row_id)
    assert queue.status == "failed"
    assert queue.next_attempt_at is None
    state = case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))
    assert state["inflight"] == {}
    assert state["complete"] is False
    assert case.adapter.send_media.await_count == 1
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        case.payload, route_key=None, delivery_id=row_id)
    assert case.adapter.send_media.await_count == 1
    async def later_accepted(*args, **options):
        await options["before_dispatch"](5204)
        return {"ok": True, "confirmed": True, "result": {"rid": 5204}}
    case.adapter.send_media.side_effect = later_accepted
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "fresh-after-negative-ack"}
    assert persist(case, retry) == row_id
    case.db.refresh(queue)
    assert "_wootify_terminal_failure" not in queue.payload_json
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        retry, route_key=None, delivery_id=row_id)
    assert case.adapter.send_media.await_count == 2
    assert statuses(case)[-1] == "sent"
    case.client.delete_delivery_failure_note.assert_awaited_once_with(1, 77, 9951)


def queue_payload(*, retry=False, edited_snapshot=False, retry_id="native-retry-one"):
    payload = {"event": "message_created", "id": 9001, "message_type": "outgoing",
               "content": "", "attachments": [{"id": 11, "data_url": "http://chatwoot/files/one.ogg"}],
               "conversation": {"id": 77, "inbox_id": 5, "messages": []}}
    if retry:
        payload["wootify_retry"] = True
        payload["retry_request_id"] = retry_id
    if edited_snapshot:
        payload["updated_at"] = "2026-10-14T14:00:00Z"
        payload["status"] = "failed"
        payload["conversation"]["messages"] = [{"id": 123, "content": "different preview"}]
    return payload


def persist(case, payload):
    return controller._persist_chatwoot_delivery(case.db, instance_key=case.instance.instance_key,
        platform_key="bale_pv_enterprise", route_key=None, payload=payload)


@pytest.fixture
def queue_case(tmp_path):
    # This controller deliberately rolls back on duplicate-key races; unlike
    # the suite's outer-transaction fixture, use real independent commits so
    # that rollback cannot erase the original queued delivery.
    engine = create_engine(f"sqlite:///{tmp_path / 'bale-retry-queue.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        yield SimpleNamespace(db=db, factory=factory, instance=SimpleNamespace(instance_key="queue-bale"))
    engine.dispose()


def test_bale_native_retry_reuses_stable_queue_key_despite_status_and_preview_changes(queue_case):
    case = queue_case
    original = queue_payload()
    row_id = persist(case, original)
    changed = queue_payload(retry=True, edited_snapshot=True)
    assert persist(case, changed) == row_id
    assert case.db.query(ChatwootWebhookDelivery).count() == 1


@pytest.mark.anyio
@pytest.mark.parametrize("retryable, expected", [(True, "pending"), (False, "failed")])
async def test_background_worker_retries_only_safe_bale_failures(queue_case, monkeypatch, retryable, expected):
    case = queue_case
    monkeypatch.setattr(controller, "SessionLocal", case.factory)
    fake_bridge = SimpleNamespace(handle_chatwoot_webhook=AsyncMock(return_value={
        "ok": False, "message": "delivery_failed", "detail": "mock delivery error", "retryable": retryable}))
    monkeypatch.setattr(controller, "chatwoot_bridge", fake_bridge)
    payload = queue_payload()
    row_id = persist(case, payload)
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                       payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    assert row.status == expected
    assert row.attempts == 1
    assert (row.next_attempt_at is not None) is retryable


def test_safe_automatic_retry_budget_exhaustion_keeps_terminal_failed_job(queue_case, monkeypatch):
    case = queue_case
    monkeypatch.setattr(controller, "SessionLocal", case.factory)
    row_id = persist(case, queue_payload())
    for _ in range(controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS):
        controller._fail_chatwoot_delivery(row_id, "mock safe transport failure", terminal=False)
    case.db.expire_all()
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    assert row.status == "failed"
    assert row.next_attempt_at is None
    assert row.attempts == controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS


def test_failed_job_rearms_only_on_explicit_retry_and_resets_attempt_budget(queue_case):
    case = queue_case
    payload = queue_payload()
    row_id = persist(case, payload)
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.attempts = 10
    row.next_attempt_at = None
    row.last_error = "temporary connection failed"
    case.db.commit()
    assert persist(case, deepcopy(payload)) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == 10
    assert persist(case, queue_payload(retry=True)) == row_id
    case.db.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.next_attempt_at is not None
    assert row.last_error is None
    row.attempts = 3
    case.db.commit()
    # A second rapid click while this retry is already pending must not refill
    # its automatic retry budget or create a parallel send job.
    assert persist(case, queue_payload(retry=True, edited_snapshot=True)) == row_id
    case.db.refresh(row)
    assert row.attempts == 3
    assert case.db.query(ChatwootWebhookDelivery).count() == 1


def test_consumed_native_retry_request_cannot_reset_exhausted_budget_again(queue_case):
    case = queue_case
    row_id = persist(case, queue_payload())
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.attempts = controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    row.next_attempt_at = None
    case.db.commit()
    assert persist(case, queue_payload(retry=True, retry_id="request-once")) == row_id
    case.db.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 0
    row.status = "failed"
    row.attempts = controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    row.next_attempt_at = None
    case.db.commit()
    # Network-level webhook redelivery of the old click is not a new click.
    assert persist(case, queue_payload(retry=True, retry_id="request-once", edited_snapshot=True)) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    assert persist(case, queue_payload(retry=True, retry_id="request-twice")) == row_id
    case.db.refresh(row)
    assert row.status == "pending"
    assert row.attempts == 0


def test_native_retry_received_while_pending_is_consumed_before_later_exhaustion(queue_case):
    case = queue_case
    row_id = persist(case, queue_payload())
    assert persist(case, queue_payload(retry=True, retry_id="clicked-while-pending")) == row_id
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.attempts = controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    row.next_attempt_at = None
    case.db.commit()
    assert persist(case, queue_payload(retry=True, retry_id="clicked-while-pending", edited_snapshot=True)) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS


def test_native_retry_without_unique_request_id_cannot_rearm_failed_job(queue_case):
    case = queue_case
    row_id = persist(case, queue_payload())
    row = case.db.get(ChatwootWebhookDelivery, row_id)
    row.status = "failed"
    row.attempts = controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS
    row.next_attempt_at = None
    case.db.commit()
    assert persist(case, queue_payload(retry=True, retry_id="")) == row_id
    case.db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == controller._WEBHOOK_DELIVERY_MAX_ATTEMPTS


@pytest.mark.anyio
async def test_guard_timeout_after_confirmed_send_reconciles_status_without_resend(integration_case, monkeypatch):
    case = integration_case
    case.payload["content"] = "sample"
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    monkeypatch.setattr(controller, "chatwoot_bridge", case.bridge)
    monkeypatch.setattr(controller, "SessionLocal", sessionmaker(bind=case.db.bind, autocommit=False, autoflush=False))
    monkeypatch.setattr(controller, "_WEBHOOK_DELIVERY_TIMEOUT_SECONDS", 0.05)
    case.client.list_conversation_messages.return_value = {"payload": []}
    sent_calls = 0
    async def send(*args, **options):
        await options["before_dispatch"](5012)
        return {"ok": True, "confirmed": True, "result": {"rid": 5012}}
    async def status(*args, **options):
        nonlocal sent_calls
        if options["status"] == "sent":
            sent_calls += 1
            if sent_calls == 1:
                await asyncio.sleep(10)
    case.adapter.send_text.side_effect = send
    case.client.update_message_status.side_effect = status
    row_id = persist(case, case.payload)
    await controller._deliver_chatwoot_webhook_guarded("bale_pv_enterprise", case.instance.instance_key,
                                                    case.payload, route_key=None, delivery_id=row_id)
    assert case.adapter.send_text.await_count == 1
    assert statuses(case)[-1] == "sent"
    assert statuses(case).count("failed") == 1
    case.client.post_message.assert_not_awaited()
    assert case.delivery.state(case.delivery.mapping(case.db, case.instance, case.payload))["complete"] is True


@pytest.mark.anyio
async def test_confirmed_native_text_edit_reaches_chatwoot_and_preserves_delivery_ledger(delivery_case, monkeypatch):
    case = delivery_case
    async def confirmed(*args, **options):
        return await accepted(case, 5301, options)
    case.adapter.send_text.side_effect = confirmed
    await deliver(case, content="original")
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    original_ledger = case.delivery.state(row)
    case.client.post_message.return_value = {"id": 9902}
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    result = await case.bridge.ingest_platform_event(case.db, case.instance.instance_key,
        {"outgoing": True, "chat_id": "123", "chat_type": "private", "platform_message_id": "5301",
         "message_id": "5301", "text": "edited in native Bale", "date": 123456, "attachments": []})
    assert result["status"] == "edit_replied"
    note = case.client.post_message.await_args.args[2]
    assert note["content"] == "edited : edited in native Bale"
    assert note["content_attributes"]["in_reply_to"] == 9001
    case.db.refresh(row)
    assert row.platform_payload_json["text"] == "edited in native Bale"
    assert case.delivery.state(row) == original_ledger
    assert row.platform_payload_json["delivery_complete"] is True


@pytest.mark.anyio
async def test_chatwoot_edit_merges_text_snapshot_without_discarding_confirmed_ledger(delivery_case, monkeypatch):
    case = delivery_case
    async def confirmed(*args, **options):
        return await accepted(case, 5302, options)
    case.adapter.send_text.side_effect = confirmed
    await deliver(case, content="original")
    row = case.delivery.mapping(case.db, case.instance, case.payload)
    original_ledger = case.delivery.state(row)
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key,
        {**case.payload, "event": "message_updated", "content": "edited in Chatwoot"})
    assert result["status"] == "edit_propagated"
    case.adapter.edit_message.assert_awaited_once_with(peer_id="123", message_id="5302", text="edited in Chatwoot")
    case.db.refresh(row)
    assert row.platform_payload_json["text"] == "edited in Chatwoot"
    assert case.delivery.state(row) == original_ledger
    assert row.platform_payload_json["delivery_complete"] is True


@pytest.mark.anyio
async def test_confirmed_delivery_bookkeeping_finishes_even_when_bale_runtime_is_offline(delivery_case, monkeypatch):
    case = delivery_case
    case.payload["content"] = "original"
    async def confirmed(*args, **options):
        return await accepted(case, 5303, options)
    case.adapter.send_text.side_effect = confirmed
    await deliver(case, content="original")
    case.client.update_message_status.reset_mock()
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: None)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key,
        {**case.payload, "wootify_retry": True, "retry_request_id": "offline-bookkeeping-only"})
    assert result["ok"] is True
    assert result["reason"] == "duplicate_delivery"
    assert statuses(case) == ["sent"]
    assert case.adapter.send_text.await_count == 1
    case.client.post_message.assert_not_awaited()
    assert case.bridge._remove_delivery_failure_notes.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("runtime_available", [True, False], ids=["failed-before-upload", "offline-no-message-mapping"])
async def test_deleted_failed_voice_cancels_durable_queue_and_cannot_be_rearmed(integration_case, monkeypatch, runtime_available):
    case = integration_case
    case.payload["attachments"] = media_parts()[:1]
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime if runtime_available else None)
    monkeypatch.setattr(case.bridge, "_chatwoot_client_for_instance",
                        lambda *_: (case.instance, case.config, case.client))
    monkeypatch.setattr(controller, "chatwoot_bridge", case.bridge)
    monkeypatch.setattr(controller, "SessionLocal", sessionmaker(bind=case.db.bind, autocommit=False, autoflush=False))
    case.client.list_conversation_messages.return_value = {"payload": []}
    case.client.post_message.return_value = {"id": 9971}
    case.adapter.send_media.side_effect = BaleRequestNotDispatchedError("upload connection unavailable")
    row_id = persist(case, case.payload)
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        case.payload, route_key=None, delivery_id=row_id)
    case.db.expire_all()
    queued = case.db.get(ChatwootWebhookDelivery, row_id)
    assert queued.status == "pending"
    mapping = case.delivery.mapping(case.db, case.instance, case.payload)
    if runtime_available:
        assert mapping is not None
        assert mapping.platform_message_id is None
        assert case.adapter.send_media.await_count == 1
    else:
        assert mapping is None
        case.adapter.send_media.assert_not_awaited()
    deleted = {**case.payload, "event": "message_updated", "content_attributes": {"deleted": True}}
    deletion_result = await case.bridge.handle_chatwoot_webhook(case.db, case.instance.instance_key, deleted)
    assert deletion_result["ok"] is True
    case.db.refresh(queued)
    assert queued.status == "failed"
    assert queued.next_attempt_at is None
    assert queued.payload_json["_wootify_cancelled"] is True
    assert queued.last_error == "cancelled_by_chatwoot_delete"
    case.client.delete_message.assert_not_awaited()  # There was no accepted Bale message to delete.
    if mapping is not None:
        case.db.refresh(mapping)
        assert case.delivery.state(mapping)["cancelled"] is True
        with pytest.raises(RuntimeError, match="outgoing_delivery_cancelled"):
            await deliver(case, media_parts()[:1])
    # Even a fresh intentional Retry, after connectivity returns, cannot
    # resurrect a message that the operator already deleted in Chatwoot.
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: case.runtime)
    retry = {**case.payload, "wootify_retry": True, "retry_request_id": "fresh-but-deleted"}
    assert persist(case, retry) == row_id
    case.db.refresh(queued)
    assert queued.status == "failed"
    assert queued.payload_json["_wootify_cancelled"] is True
    await controller._deliver_chatwoot_webhook_background("bale_pv_enterprise", case.instance.instance_key,
                                                        retry, route_key=None, delivery_id=row_id)
    assert case.adapter.send_media.await_count == int(runtime_available)
    assert "sent" not in statuses(case)
