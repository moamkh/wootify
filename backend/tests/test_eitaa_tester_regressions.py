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
@pytest.mark.parametrize("response,error", [({"_":"eitaa_updates_expire_token"},"login required"), ({"_":"updates.differenceTooLong","pts":500},"history recovery required")])
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
