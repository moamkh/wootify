from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wootify.plugins.eitaa_pv.connector import EitaaPvConnector, EitaaPvRuntime
from wootify.plugins.eitaa_pv.adapter import EitaaPvAdapter
from wootify.application.messaging.chatwoot_bridge import chatwoot_bridge
from wootify.infrastructure.persistence.repositories.contact_mapping_repository import ContactMappingRepository
from wootify.application.messaging.contact_identity import contact_scope
from wootify.models import Instance, PlatformType


def instance(db):
    p = PlatformType(key="eitaa_pv_enterprise", display_name="Eitaa", capabilities_json={}, metadata_schema_json={})
    db.add(p)
    db.flush()
    i = Instance(instance_key="phone-test", platform_type_id=p.id, is_enabled=True,
        platform_metadata_encrypted="", proxy_config_encrypted="", chatwoot_config_encrypted="")
    db.add(i)
    db.commit()
    return i


def connector(users):
    client = SimpleNamespace(profile=SimpleNamespace(authenticated=True),
        invoke=AsyncMock(return_value={"users": users}),
        dialogs=SimpleNamespace(list=AsyncMock(return_value={"dialogs": [], "users": []})))
    result = EitaaPvConnector()
    result._runtimes["test"] = EitaaPvRuntime("test", "", Path("session.json"), client)
    return result


@pytest.mark.anyio
async def test_exact_phone_resolution_ignores_nonmatching_first_hit():
    c = connector([{"id":1,"phone":"989111111111","access_hash":8},
                   {"id":42,"phone":"989904905660","access_hash":99}])
    assert (await c.resolve_phone_to_user("test", "+989904905660"))["id"] == 42
    assert c._runtimes["test"].peer_refs["42"] == "user:42:99"


@pytest.mark.parametrize("users,error", [([], "not_found"),
    ([{"id":1,"phone":"989904905660","access_hash":8},
      {"id":2,"phone":"989904905660","access_hash":9}], "ambiguous")])
@pytest.mark.anyio
async def test_missing_or_ambiguous_phone_fails_closed(users, error):
    with pytest.raises(RuntimeError, match=error):
        await connector(users).resolve_phone_to_user("test", "+989904905660")


@pytest.mark.anyio
async def test_eitaa_alias_preserves_canonical_contact_and_whatsapp_identifier(db_session):
    i = instance(db_session)
    client = SimpleNamespace(base_url="http://chatwoot")
    scope = contact_scope(client, 1)
    repo = ContactMappingRepository(db_session)
    repo.save(i.id, "42", "5215", chatwoot_scope=scope)
    db_session.commit()
    runtime = SimpleNamespace(platform_type="eitaa_pv_enterprise",
        adapter=SimpleNamespace(resolve_phone_to_user=AsyncMock(return_value={"id":42,"access_hash":99})))
    await chatwoot_bridge._ensure_verified_contact_alias(db_session, client, i, runtime,
        account_id=1, peer_id="42", contact_id="4004", phone_number="+989904905660",
        identifier="989904905660@s.whatsapp.net")
    assert repo.get(i.id, "42").chatwoot_contact_id == "5215"
    assert repo.unique_platform_id_for_chatwoot(i.id, "4004", chatwoot_scope=scope) == "42"
    assert repo.get_verified_alias(i.id, "4004", scope).verification_method == "eitaa_phone"


def test_incoming_uses_dialog_peer_phone_not_authenticated_senders_phone():
    event = EitaaPvAdapter.normalize_incoming_update(object.__new__(EitaaPvAdapter), {
        "chat_id":"42", "peer":{"_":"peerUser","user_id":42},
        "message":{"id":7,"from_id":1,"out":True,"message":"hello"},
        "entities":{("user",42):{"id":42,"first_name":"Customer","phone":"989904905660"},
                    ("user",1):{"id":1,"phone":"989111111111"}},
    })
    assert event["contact"]["phone_number"] == "989904905660"


@pytest.mark.anyio
async def test_unmapped_outbound_recovers_and_next_message_uses_owned_map(db_session, monkeypatch):
    import wootify.application.messaging.chatwoot_bridge as bridge_module
    from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
    i = instance(db_session)
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    adapter = SimpleNamespace(resolve_phone_to_user=AsyncMock(return_value={"id":42,"access_hash":99}),
        send_text=AsyncMock(return_value={"ok":True}))
    runtime = SimpleNamespace(status="open", platform_type="eitaa_pv_enterprise", adapter=adapter)
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: runtime)
    service = ChatwootBridgeService()
    monkeypatch.setattr(service, "_chatwoot_client_for_instance", lambda *_: (i, {"account_id":1}, client))
    ContactMappingRepository(db_session).save(i.id, "42", "5215", chatwoot_scope=contact_scope(client,1))
    db_session.commit()
    payload = {"event":"message_created","id":9001,"message_type":"outgoing","content":"Hello",
        "conversation":{"id":2836,"inbox_id":57,"meta":{"sender":{"id":4004,
            "phone_number":"+989904905660","identifier":"989904905660@s.whatsapp.net"}},"messages":[]}}
    result = await service.handle_chatwoot_webhook(db_session, i.instance_key, payload)
    assert result["ok"] is True and result["peer_id"] == "42"
    payload["id"] = 9002
    payload["conversation"]["meta"]["sender"].pop("phone_number")
    assert (await service.handle_chatwoot_webhook(db_session, i.instance_key, payload))["ok"] is True
    assert adapter.send_text.await_count == 2
    assert adapter.resolve_phone_to_user.await_count == 2
    client.update_contact.assert_not_awaited()
