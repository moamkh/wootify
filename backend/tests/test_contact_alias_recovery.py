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
    adapter = SimpleNamespace(
        resolve_phone_to_user=AsyncMock(side_effect=[{"id": peer, "access_hash": "99", "name": "Kamal"} for peer in resolved_ids]),
        cache_access_hash=MagicMock(),
        send_text=AsyncMock(return_value={"ok": True}),
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
