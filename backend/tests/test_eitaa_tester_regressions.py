import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from wootify.models import Base, Instance, PlatformType, Conversation, MessageMapping, MessageDirection, MessageKind, MessageStatus, InboundEventRetry, InstanceRuntimeState
from wootify.plugins.eitaa_pv.adapter import EitaaPvAdapter
from wootify.plugins.eitaa_pv.connector import EitaaPvConnector, EitaaPvRuntime
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
from wootify.application.messaging.contact_identity import ContactIdentityService, contact_scope
from wootify.infrastructure.persistence.repositories.contact_mapping_repository import ContactMappingRepository
from wootify.application.messaging.polling import BalePollingService


@pytest.fixture
def committed_db():
    engine = create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def raw(message):
    return {"chat_id":"42", "peer":{"_":"peerUser","user_id":42}, "message":{"id":7, **message},
            "entities":[{"kind":"user","id":42,"entity":{"id":42,"first_name":"Customer","phone":"989904905660"}}]}


def test_nested_eitaa_reply_is_normalized():
    event = EitaaPvAdapter("test", {}).normalize_incoming_update(raw({"message":"Reply", "reply_to":{"_":"messageReplyHeader","reply_to_msg_id":6}}))
    assert event["reply_to"] == {"message_id":"6"}


@pytest.mark.parametrize("kind", ["messageMediaGeo", "messageMediaGeoLive", "messageMediaVenue"])
def test_locations_are_links_not_broken_attachments(kind):
    event = EitaaPvAdapter("test", {}).normalize_incoming_update(raw({"media":{"_":kind,"geo":{"lat":35.7,"long":51.4},"title":"Office"}}))
    assert "https://www.google.com/maps/search/?api=1&query=35.7,51.4" in event["text"]
    assert event["attachments"] == []


def test_contact_card_is_text_not_download_reference():
    event = EitaaPvAdapter("test", {}).normalize_incoming_update(raw({"media":{"_":"messageMediaContact","first_name":"Example","phone_number":"123"}}))
    assert event["text"] == "Contact: Example\n123" and not event["attachments"]


def make_connector(responses):
    client = SimpleNamespace(profile=SimpleNamespace(authenticated=True), invoke=AsyncMock(side_effect=responses),
        dialogs=SimpleNamespace(list=AsyncMock(return_value={"dialogs":[],"users":[]})))
    c = EitaaPvConnector()
    c._runtimes["test"] = EitaaPvRuntime("test", "", Path("session.json"), client)
    c._set_presence = AsyncMock()
    return c


@pytest.mark.anyio
async def test_difference_cursor_waits_for_durable_ack_and_restarts():
    state = {"_":"updates.state","pts":10,"date":100,"qts":0,"seq":0}
    difference = {"_":"updates.difference", "state":{**state,"pts":12},
        "users":[{"id":42,"access_hash":99,"first_name":"Customer"}],
        "new_messages":[{"id":7,"peer_id":{"_":"peerUser","user_id":42},"message":"hello"}],
        "other_updates":[{"_":"updateDeleteMessages","messages":[7],"pts":12}]}
    c = make_connector([{"schedule_period_foreground_ms":35000}, state, difference])
    assert (await c.get_updates("test"))["result"] == []
    assert c.poll_interval("test") == 35
    assert c._runtimes["test"].update_state is None
    c.acknowledge_updates("test")
    batch = (await c.get_updates("test"))["result"]
    assert batch[0]["message"]["id"] == 7
    assert batch[1]["deleted"] and batch[1]["chat_id"] == "42"
    assert c._runtimes["test"].update_state["pts"] == 10
    assert (await c.get_updates("test"))["result"] == batch
    assert c._runtimes["test"].client.invoke.await_count == 3
    serialized = json.dumps(batch)  # No tuple-keyed entity dictionaries in durable payloads.
    assert EitaaPvAdapter("test", {}).normalize_incoming_update(json.loads(serialized)[0])["from_name"] == "Customer"
    cursor = c.pending_cursor("test")
    c.acknowledge_updates("test")
    restarted = make_connector([])
    restarted.restore_update_state("test", cursor)
    assert restarted._runtimes["test"].update_state["pts"] == 12


@pytest.mark.anyio
@pytest.mark.parametrize("response,error", [({"_":"eitaa_updates_expire_token"},"renewal pending"), ({"_":"updates.differenceTooLong","pts":500},"history recovery required")])
async def test_difference_errors_do_not_advance_cursor(response, error):
    c = make_connector([response])
    r = c._runtimes["test"]
    r.web_poll_interval = 35
    r.update_state = {"pts":10,"date":100,"qts":0,"seq":0}
    with pytest.raises(RuntimeError, match=error):
        await c.get_updates("test")
    assert r.update_state["pts"] == 10 and r.pending_updates is None


@pytest.mark.anyio
async def test_edits_with_same_timestamp_keep_distinct_queue_keys():
    message = {"id":7,"peer_id":{"_":"peerUser","user_id":42},"edit_date":200}
    difference = {"_":"updates.difference","state":{"pts":12,"date":200},"new_messages":[],
        "other_updates":[{"_":"updateEditMessage","message":{**message,"message":"first edit"}},
                         {"_":"updateEditMessage","message":{**message,"message":"second edit"}}]}
    c = make_connector([difference])
    runtime = c._runtimes["test"]
    runtime.update_state = {"pts":10,"date":100}
    runtime.web_poll_interval = 35
    events = (await c.get_updates("test"))["result"]
    assert len({event["update_id"] for event in events}) == 2


def test_missing_numeric_peer_hash_never_uses_fuzzy_search():
    runtime = EitaaPvRuntime("test","",Path("session.json"),None)
    with pytest.raises(RuntimeError,match="access_hash_unavailable"):
        EitaaPvConnector._peer_ref(runtime,"42")


def test_channel_gap_remains_a_visible_recovery_failure():
    with pytest.raises(RuntimeError,match="channel_difference_required"):
        EitaaPvAdapter("test",{}).normalize_incoming_update({"channel_gap":True,"chat_id":"channel:42"})


def setup(db):
    p = PlatformType(key="eitaa_pv_enterprise",display_name="Eitaa",capabilities_json={},metadata_schema_json={})
    db.add(p)
    db.flush()
    i = Instance(instance_key="tester",platform_type_id=p.id,is_enabled=True,
        platform_metadata_encrypted="",proxy_config_encrypted="",chatwoot_config_encrypted="")
    db.add(i)
    db.flush()
    conv = Conversation(instance_id=i.id,platform_conversation_id="42",chatwoot_conversation_id="2836",chatwoot_contact_id="4004",chatwoot_inbox_id="57")
    db.add(conv)
    db.commit()
    return i, conv


@pytest.mark.anyio
@pytest.mark.parametrize("media_kind", [None, "messageMediaGeo", "messageMediaContact"])
async def test_customer_reply_to_operator_preserves_chatwoot_parent(db_session, monkeypatch, media_kind):
    """Reply metadata must survive normalization even for location/contact replies."""
    i, conv = setup(db_session)
    db_session.add(MessageMapping(
        conversation_id=conv.id, direction=MessageDirection.chatwoot_to_platform,
        message_kind=MessageKind.text, status=MessageStatus.sent,
        platform_message_id="110", chatwoot_message_id="85993",
    ))
    db_session.commit()
    message = {"id":111, "message":"customer reply",
               "reply_to":{"_":"messageReplyHeader","reply_to_msg_id":110}}
    if media_kind == "messageMediaGeo":
        message["media"] = {"_":media_kind,"geo":{"lat":35.7,"long":51.4}}
    elif media_kind == "messageMediaContact":
        message["media"] = {"_":media_kind,"first_name":"Example","phone_number":"123"}
    event = EitaaPvAdapter("tester", {}).normalize_incoming_update(raw(message))
    client = AsyncMock()
    client.get_contact.return_value = {"id":4004,"name":"Customer"}
    client.post_message.return_value = {"id":86000}
    service = ChatwootBridgeService()
    monkeypatch.setattr(service, "_chatwoot_client_for_instance",
                        lambda *args: (i, {"account_id":3,"inbox_id":57}, client))
    monkeypatch.setattr(service, "_get_or_create_contact", AsyncMock(return_value=(4004,False)))
    result = await service.ingest_platform_event(db_session, "tester", event)
    assert result["ok"]
    assert client.post_message.await_args.args[2]["content_attributes"] == {"in_reply_to":85993}
    mapping = db_session.query(MessageMapping).filter_by(platform_message_id="111").one()
    assert mapping.platform_parent_message_id == "110"
    assert mapping.chatwoot_parent_message_id == "85993"


@pytest.mark.anyio
@pytest.mark.parametrize("part", ["media:1", "media:2", "text"])
async def test_reply_to_each_eitaa_batch_part_targets_original_chatwoot_message(db_session, monkeypatch, part):
    i, conv = setup(db_session)
    ids = {"media:1":"92", "media:2":"93", "text":"94"}
    for name, mid in ids.items():
        db_session.add(MessageMapping(
            conversation_id=conv.id, direction=MessageDirection.chatwoot_to_platform,
            message_kind=MessageKind.text if name == "text" else MessageKind.media,
            status=MessageStatus.sent, platform_message_id=mid,
            chatwoot_message_id="85888" if name == "text" else None,
            platform_payload_json={"batch_message_id":"85888","batch_part":name,"delivery_complete":True},
        ))
    db_session.commit()
    event = EitaaPvAdapter("tester",{}).normalize_incoming_update(raw({
        "id":95,"message":"b","reply_to":{"reply_to_msg_id":int(ids[part])},
    }))
    client = AsyncMock()
    client.get_contact.return_value = {"id":4004,"name":"Customer"}
    client.post_message.return_value = {"id":85899}
    service = ChatwootBridgeService()
    monkeypatch.setattr(service,"_chatwoot_client_for_instance",
                        lambda *args: (i,{"account_id":3,"inbox_id":57},client))
    monkeypatch.setattr(service,"_get_or_create_contact",AsyncMock(return_value=(4004,False)))
    await service.ingest_platform_event(db_session,"tester",event)
    assert client.post_message.await_args.args[2].get("content_attributes") == {"in_reply_to":85888}
    reply = db_session.query(MessageMapping).filter_by(platform_message_id="95").one()
    assert reply.chatwoot_parent_message_id == "85888"
    assert reply.platform_parent_message_id == ids[part]


@pytest.mark.anyio
@pytest.mark.parametrize("canonical_scope", ["missing", "another_conversation"])
async def test_batch_reply_never_borrows_parent_from_another_conversation(db_session, monkeypatch, canonical_scope):
    i, conv = setup(db_session)
    db_session.add(MessageMapping(
        conversation_id=conv.id, direction=MessageDirection.chatwoot_to_platform,
        message_kind=MessageKind.media, status=MessageStatus.sent,
        platform_message_id="92", chatwoot_message_id=None,
        platform_payload_json={"batch_message_id":"85888","batch_part":"media:1"},
    ))
    if canonical_scope == "another_conversation":
        other = Conversation(instance_id=i.id,platform_conversation_id="43",chatwoot_conversation_id="2837")
        db_session.add(other)
        db_session.flush()
        db_session.add(MessageMapping(
            conversation_id=other.id,direction=MessageDirection.chatwoot_to_platform,
            message_kind=MessageKind.text,status=MessageStatus.sent,
            platform_message_id="93",chatwoot_message_id="85888",
        ))
    db_session.commit()
    event = EitaaPvAdapter("tester",{}).normalize_incoming_update(raw({
        "id":95,"message":"b","reply_to":{"reply_to_msg_id":92},
    }))
    client = AsyncMock()
    client.get_contact.return_value = {"id":4004,"name":"Customer"}
    client.post_message.return_value = {"id":85899}
    service = ChatwootBridgeService()
    monkeypatch.setattr(service,"_chatwoot_client_for_instance",
                        lambda *args: (i,{"account_id":3,"inbox_id":57},client))
    monkeypatch.setattr(service,"_get_or_create_contact",AsyncMock(return_value=(4004,False)))
    await service.ingest_platform_event(db_session,"tester",event)
    assert "content_attributes" not in client.post_message.await_args.args[2]


@pytest.mark.anyio
async def test_media_batch_retry_sends_only_remaining_caption(db_session):
    i, conv = setup(db_session)
    order = []
    async def media(*args, **kwargs):
        order.append(kwargs["filename"])
        assert kwargs["caption"] is None
        return {"ok":True,"result":{"message_id":str(len(order))}}
    async def text(*args, **kwargs):
        order.append("text")
        if order.count("text") == 1:
            raise RuntimeError("temporary failure")
        return {"ok":True,"result":{"message_id":"3"}}
    adapter = SimpleNamespace(send_media=AsyncMock(side_effect=media),send_text=AsyncMock(side_effect=text))
    runtime = SimpleNamespace(adapter=adapter)
    service = ChatwootBridgeService()
    payload = {"id":9001,"conversation":{"id":2836}}
    attachments = [{"id":1,"data_url":"http://files/1.jpg"},{"id":2,"data_url":"http://files/2.pdf"}]
    async def send():
        return await service._send_eitaa_attachment_batch(db_session,i,runtime,"42",payload,attachments,
            content="caption",reply_to="6",chatwoot_cfg={})
    with pytest.raises(RuntimeError,match="temporary failure"):
        await send()
    assert order == ["file.jpg","file.pdf","text"]
    assert service._is_duplicate_outbound_delivery(db_session,instance=i,peer_id="42",payload=payload,content="caption",is_template_or_bot=False) is None
    await send()
    assert order == ["file.jpg","file.pdf","text","text"]
    assert adapter.send_media.await_count == 2
    assert adapter.send_text.await_args_list[0].kwargs["idempotency_key"] == adapter.send_text.await_args_list[1].kwargs["idempotency_key"]
    assert service._is_duplicate_outbound_delivery(db_session,instance=i,peer_id="42",payload=payload,content="caption",is_template_or_bot=False)
    mappings = db_session.query(MessageMapping).all()
    assert len(mappings) == 3
    assert all(m.platform_payload_json["delivery_complete"] for m in mappings)
    assert [m.platform_payload_json["text"] for m in mappings] == ["","","caption"]
    assert next(m for m in mappings if m.chatwoot_message_id == "9001").platform_payload_json["batch_part"] == "text"


@pytest.mark.anyio
async def test_phone_less_duplicate_rebinds_local_map_without_changing_contacts(db_session):
    i, _ = setup(db_session)
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    client.get_contact.return_value = {"id":5215,"name":"Dabagh"}
    client.search_contacts.return_value = {"payload":[{"id":4004,"name":"Ali Dabbagh","phone_number":"+989904905660","identifier":"whatsapp-owned"}]}
    mappings = ContactMappingRepository(db_session)
    scope = contact_scope(client,3)
    mappings.save(i.id,"42","5215",chatwoot_scope=scope)
    db_session.commit()
    cid, created = await ContactIdentityService().resolve(db_session,client,instance_id=i.id,account_id=3,inbox_id=57,
        peer_id="42",payload={"name":"Dabagh","phone_number":"989904905660"},peer_type="private")
    assert cid == 4004 and not created
    assert mappings.get(i.id,"42").chatwoot_contact_id == "4004"
    assert mappings.unique_platform_id_for_chatwoot(i.id,"5215",chatwoot_scope=scope) == "42"
    client.create_contact.assert_not_awaited()
    client.update_contact.assert_not_awaited()


def test_queue_commits_events_and_cursor_in_one_transaction(monkeypatch):
    import wootify.application.messaging.polling as module
    engine = create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        i, _ = setup(db)
        iid = i.id
    monkeypatch.setattr(module,"SessionLocal",factory)
    cursor = '{"pts":12,"date":100,"qts":0,"seq":0}'
    event = {**raw({"message":"hello"}),"update_id":"42:7:0"}
    service = BalePollingService()
    service._persist_bale_updates("tester","eitaa_pv_enterprise",[event],cursor=cursor)
    service._persist_bale_updates("tester","eitaa_pv_enterprise",[event],cursor=cursor)
    with factory() as db:
        assert db.query(InboundEventRetry).count() == 1
        assert db.get(InstanceRuntimeState,iid).last_platform_update_id == cursor
    # Missing instance forces the whole event/cursor transaction to roll back.
    with pytest.raises(Exception):
        service._persist_bale_updates("missing","eitaa_pv_enterprise",[event],cursor=cursor)
    with factory() as db:
        assert db.query(InboundEventRetry).count() == 1
    engine.dispose()


@pytest.mark.anyio
async def test_eitaa_delete_updates_original_chatwoot_row_not_new_message(db_session):
    i, conv = setup(db_session)
    db_session.add(MessageMapping(conversation_id=conv.id,direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,status=MessageStatus.sent,platform_message_id="7",chatwoot_message_id="100",platform_payload_json={}))
    db_session.commit()
    client = AsyncMock()
    result = await ChatwootBridgeService()._handle_platform_message_deleted(db=db_session,instance=i,instance_key=i.instance_key,
        client=client,account_id=3,event={"chat_id":"42","message_id":"7","deleted":True})
    assert result["status"] == "delete_propagated"
    client.delete_message.assert_awaited_once_with(account_id=3,conversation_id=2836,message_id=100)
    client.post_message.assert_not_awaited()


@pytest.mark.anyio
async def test_failed_eitaa_send_marks_red_and_retry_marks_sent(committed_db, monkeypatch):
    db_session = committed_db
    import wootify.application.messaging.chatwoot_bridge as module
    i, _ = setup(db_session)
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    ContactMappingRepository(db_session).save(i.id,"42","4004",chatwoot_scope=contact_scope(client,3))
    db_session.commit()
    adapter = SimpleNamespace(send_text=AsyncMock(side_effect=[RuntimeError("network unavailable"),{"ok":True,"result":{"message_id":"8"}}]))
    runtime = SimpleNamespace(status="open",platform_type="eitaa_pv_enterprise",adapter=adapter)
    monkeypatch.setattr(module,"get_runtime",lambda _:runtime)
    service = ChatwootBridgeService()
    monkeypatch.setattr(service,"_chatwoot_client_for_instance",lambda *_:(i,{"account_id":3},client))
    payload = {"event":"message_created","id":9001,"message_type":"outgoing","content":"hello",
               "conversation":{"id":2836,"inbox_id":57,"meta":{"sender":{"id":4004}},"messages":[]}}
    assert (await service.handle_chatwoot_webhook(db_session,"tester",payload))["ok"] is False
    assert client.update_message_status.await_args.kwargs["status"] == "failed"
    assert "network unavailable" in client.update_message_status.await_args.kwargs["external_error"]
    assert client.post_message.await_args.args[2]["private"] is True
    assert (await service.handle_chatwoot_webhook(db_session,"tester",payload))["ok"] is True
    assert client.update_message_status.await_args.kwargs["status"] == "sent"
    assert adapter.send_text.await_args_list[0].kwargs["idempotency_key"] == adapter.send_text.await_args_list[1].kwargs["idempotency_key"]


@pytest.mark.parametrize("response", [{"_":"boolFalse"},{"_":"eitaa_updates_expire_token"},{"_":"updates","updates":[]}])
def test_unacknowledged_send_is_never_reported_successful(response):
    with pytest.raises(RuntimeError,match="not_acknowledged"):
        EitaaPvAdapter("test",{})._sent_result("42",response,mirror_echo=False)


@pytest.mark.anyio
async def test_phone_match_reuses_one_contact_across_instances(db_session):
    import httpx
    i, _ = setup(db_session)
    other = Instance(instance_key="other-platform",platform_type_id=i.platform_type_id,is_enabled=True,
        platform_metadata_encrypted="",proxy_config_encrypted="",chatwoot_config_encrypted="")
    db_session.add(other)
    db_session.commit()
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    client.get_contact_by_source.side_effect = httpx.HTTPStatusError("missing",request=httpx.Request("GET","http://chatwoot"),response=httpx.Response(404))
    client.search_contacts.return_value = {"payload":[{"id":4004,"phone_number":"+989904905660","name":"Ali","identifier":"whatsapp-owned"}]}
    client.get_contact.return_value = client.search_contacts.return_value["payload"][0]
    resolver = ContactIdentityService()
    for instance, peer in [(i,"42"),(other,"different-id")]:
        cid, created = await resolver.resolve(db_session,client,instance_id=instance.id,account_id=3,inbox_id=57,
            peer_id=peer,payload={"name":"Alias","phone_number":"09904905660"},peer_type="user")
        assert cid == 4004 and not created
        assert ContactMappingRepository(db_session).get(instance.id,peer).chatwoot_contact_id == "4004"
    client.create_contact.assert_not_awaited()
    client.update_contact.assert_not_awaited()
