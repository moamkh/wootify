"""The production duplicate-contact case must route to the verified Bale peer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import wootify.services.chatwoot_bridge_service as bridge_module
from wootify.application.messaging.contact_identity import contact_scope
from wootify.infrastructure.persistence.models import Base, ContactAlias, ContactMapping, Conversation, Instance, PlatformType
from wootify.infrastructure.persistence.repositories.contact_mapping_repository import ContactMappingConflict, ContactMappingRepository
from wootify.services.chatwoot_bridge_service import ChatwootBridgeService
from wootify.application.messaging.conversation_mapping import ConversationMappingService


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def setup(db, monkeypatch, *, resolved_ids=(12345,)):
    platform = PlatformType(key="bale_pv_enterprise", display_name="Bale PV", capabilities_json={}, metadata_schema_json={})
    db.add(platform)
    db.flush()
    instance = Instance(instance_key="duplicate-contact", platform_type_id=platform.id,
                        is_enabled=True, platform_metadata_encrypted="", chatwoot_config_encrypted="", proxy_config_encrypted="")
    db.add(instance)
    db.flush()
    client = AsyncMock()
    client.base_url = "http://chatwoot"

    async def confirmed_send(peer_id, content, **options):
        rid = 2**62 + adapter.send_text.await_count
        if options.get("before_dispatch") is not None:
            await options["before_dispatch"](rid)
        return {"ok": True, "confirmed": True, "result": {"rid": rid}}

    adapter = SimpleNamespace(
        resolve_phone_to_user=AsyncMock(side_effect=[{"id": peer, "access_hash": "99", "name": "Kamal"} for peer in resolved_ids]),
        cache_access_hash=MagicMock(),
        send_text=AsyncMock(side_effect=confirmed_send),
    )
    runtime = SimpleNamespace(status="open", platform_type="bale_pv_enterprise", adapter=adapter)
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: runtime)
    service = ChatwootBridgeService()
    monkeypatch.setattr(service, "_chatwoot_client_for_instance", lambda *_: (instance, {"account_id": 1}, client))
    ContactMappingRepository(db).save(instance.id, "12345", "42", chatwoot_scope=contact_scope(client, 1), platform_contact_type="user")
    db.commit()
    return service, client, adapter, instance


def payload(message_id, conversation_id, *, phone=True):
    sender = {"id": 48}
    if phone:
        sender["phone_number"] = "+989169091940"
    return {"event": "message_created", "id": message_id, "message_type": "outgoing", "content": "Hello",
            "conversation": {"id": conversation_id, "inbox_id": 5, "meta": {"sender": sender}, "messages": []}}


@pytest.mark.anyio
async def test_duplicate_chatwoot_contact_recovers_and_future_conversations_route(db, monkeypatch):
    service, client, adapter, instance = setup(db, monkeypatch, resolved_ids=(12345, 12345))
    first = await service.handle_chatwoot_webhook(db, instance.instance_key, payload(9001, 2208))
    assert first["ok"] is True
    assert first["peer_id"] == "12345"
    assert db.query(ContactMapping).one().chatwoot_contact_id == "42"
    alias = db.query(ContactAlias).one()
    assert (alias.chatwoot_contact_id, alias.platform_contact_id, alias.verification_method) == ("48", "12345", "bale_phone")
    assert db.query(Conversation).filter_by(chatwoot_conversation_id="2208").one().chatwoot_contact_id == "48"
    second = await service.handle_chatwoot_webhook(db, instance.instance_key, payload(9002, 2208, phone=False))
    third = await service.handle_chatwoot_webhook(db, instance.instance_key, payload(9003, 2210, phone=False))
    assert second["ok"] is True and third["ok"] is True
    assert adapter.resolve_phone_to_user.await_count == 2  # no new lookup after alias verification
    assert adapter.send_text.await_count == 3
    assert db.query(ContactAlias).count() == 1


@pytest.mark.anyio
async def test_alias_phone_disagreement_blocks_delivery(db, monkeypatch):
    service, client, adapter, instance = setup(db, monkeypatch, resolved_ids=(12345, 99999))
    result = await service.handle_chatwoot_webhook(db, instance.instance_key, payload(9001, 2208))
    assert result["ok"] is False
    adapter.send_text.assert_not_awaited()
    assert db.query(ContactAlias).count() == 0
    assert db.query(ContactMapping).one().chatwoot_contact_id == "42"


def test_alias_cannot_be_claimed_by_another_peer(db, monkeypatch):
    _, client, _, instance = setup(db, monkeypatch)
    mappings = ContactMappingRepository(db)
    scope = contact_scope(client, 1)
    mappings.save(instance.id, "99999", "99", chatwoot_scope=scope)
    mappings.save_verified_alias(instance.id, "12345", "48", chatwoot_scope=scope, verification_method="bale_phone")
    with pytest.raises(ContactMappingConflict, match="chatwoot_contact_owned_by_other_platform_peer"):
        mappings.save(instance.id, "99999", "48", chatwoot_scope=scope)
    with pytest.raises(ContactMappingConflict, match="contact_alias_owned_by_other_platform_peer"):
        mappings.save_verified_alias(instance.id, "99999", "48", chatwoot_scope=scope, verification_method="bale_phone")
    assert mappings.unique_platform_id_for_chatwoot(instance.id, "48", chatwoot_scope=scope) == "12345"


@pytest.mark.anyio
async def test_precreation_phone_verification_unifies_alias_policy_without_touching_identifier(db, monkeypatch):
    service, client, adapter, instance = setup(db, monkeypatch)
    monkeypatch.setattr(service, '_chatwoot_client_for_instance', lambda *_: (instance,
        {'account_id': 1, 'inbox_id': 5, 'reopen_conversation': True}, client))
    client.get_inbox.return_value = {'hmac_token': 'inbox-secret'}
    client.get_contact.return_value = {'id': 48, 'phone_number': '+989169091940', 'identifier': 'untouched@s.whatsapp.net'}
    ConversationMappingService().upsert(db, instance_id=instance.id, platform_conversation_id='12345',
        chatwoot_conversation_id='100', chatwoot_contact_id='42', chatwoot_inbox_id='5')
    db.commit()
    result = await service.conversation_policy_snapshot(db, instance.instance_key,
        {'account_id': 1, 'inbox_id': 5, 'contact_id': 48}, 'inbox-secret')
    assert result['contact_id'] == 42 and result['contact_ids'] == [42, 48] and result['reopen'] is True
    assert result['preferred_conversation_id'] == 100
    assert db.query(ContactMapping).one().chatwoot_contact_id == '42'
    adapter.resolve_phone_to_user.assert_awaited_once()
    adapter.send_text.assert_not_awaited()
    client.update_contact.assert_not_awaited()
    client.create_contact.assert_not_awaited()


@pytest.mark.anyio
async def test_precreation_rejects_bad_auth_and_cross_inbox_before_identity_lookup(db, monkeypatch):
    from fastapi import HTTPException
    service, client, adapter, instance = setup(db, monkeypatch)
    monkeypatch.setattr(service, '_chatwoot_client_for_instance', lambda *_: (instance,
        {'account_id': 1, 'inbox_id': 5}, client))
    client.get_inbox.return_value = {'hmac_token': 'inbox-secret'}
    for inbox, token in [(5, 'wrong'), (6, 'inbox-secret')]:
        with pytest.raises(HTTPException) as error:
            await service.conversation_policy_snapshot(db, instance.instance_key,
                {'account_id': 1, 'inbox_id': inbox, 'contact_id': 48}, token)
        assert error.value.status_code == 403
    client.get_contact.assert_not_awaited()
    adapter.resolve_phone_to_user.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('reopen', [True, False])
async def test_inbound_policy_includes_verified_aliases_and_preserves_old_outbound_pointer(db, monkeypatch, reopen):
    service, client, _, instance = setup(db, monkeypatch)
    mappings = ContactMappingRepository(db)
    mappings.save_verified_alias(instance.id, '12345', '48', chatwoot_scope=contact_scope(client, 1), verification_method='bale_phone')
    old = ConversationMappingService().upsert(db, instance_id=instance.id, platform_conversation_id='12345',
        chatwoot_conversation_id='100', chatwoot_contact_id='42', chatwoot_inbox_id='5')
    latest = ConversationMappingService().upsert(db, instance_id=instance.id, platform_conversation_id='12345',
        chatwoot_conversation_id='200', chatwoot_contact_id='48', chatwoot_inbox_id='5')
    db.commit()
    await service._persist_outbound_conversation_mapping(db=db, instance_id=instance.id, platform_conversation_id='12345',
        chatwoot_conversation_id='100', chatwoot_contact_id='42', chatwoot_inbox_id='5')
    db.refresh(latest)
    assert latest.is_active is True
    async def remote(_account, contact):
        return {'payload': [{'id': 100 if contact == 42 else 200, 'inbox_id': 5, 'status': 'resolved'}]}
    client.list_contact_conversations.side_effect = remote
    client.create_conversation.return_value = {'id': 300}
    result = await service._get_or_create_conversation(db, client, instance=instance, account_id=1, inbox_id=5,
        contact_id=42, chat_id='12345', chatwoot_config={'reopen_conversation': reopen})
    assert result.chatwoot_conversation_id == ('200' if reopen else '300')
    assert old.chatwoot_conversation_id == '100'


def test_consolidation_redirect_preserves_idempotency_identity_and_is_scoped(db, monkeypatch):
    service, _, _, instance = setup(db, monkeypatch)
    for cid, contact in [('100', '42'), ('200', '48')]:
        ConversationMappingService().upsert(db, instance_id=instance.id, platform_conversation_id='12345',
            chatwoot_conversation_id=cid, chatwoot_contact_id=contact, chatwoot_inbox_id='5')
    db.commit()
    config = {'verified_conversation_redirects': {'100': {'conversation_id': 200, 'peer_id': '12345', 'inbox_id': 5}}}
    original = payload(9001, 100)
    result = service._redirect_consolidated_conversation(db, instance, config, original)
    assert result['id'] == original['id'] == 9001
    assert result['conversation']['id'] == 200 and original['conversation']['id'] == 100
    assert result['content'] == original['content']
    wrong = payload(9001, 100)
    wrong['conversation']['inbox_id'] = 6
    with pytest.raises(ContactMappingConflict, match='inbox_mismatch'):
        service._redirect_consolidated_conversation(db, instance, config, wrong)
    resolution = {**original, 'event': 'conversation_status_changed'}
    assert service._redirect_consolidated_conversation(db, instance, config, resolution)['conversation']['id'] == 100
