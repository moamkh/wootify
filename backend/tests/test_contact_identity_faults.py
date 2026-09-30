"""Contact ownership and recovery tests (no live accounts or messages)."""
import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from wootify.application.messaging.contact_identity import ContactIdentityService, contact_scope
from wootify.infrastructure.chatwoot.client import ChatwootClient
from wootify.infrastructure.persistence.models import ContactCreation, ContactMapping, Instance
from wootify.models import Base, PlatformType
from sqlalchemy import create_engine
from sqlalchemy import text, inspect
from sqlalchemy.orm import sessionmaker
from wootify.infrastructure.persistence.repositories.contact_mapping_repository import ContactMappingRepository, ContactMappingConflict


@pytest.fixture
def anyio_backend():
    return "asyncio"


def missing():
    return httpx.HTTPStatusError("missing", request=httpx.Request("POST", "http://chatwoot"), response=httpx.Response(404))


def fake_client():
    client = AsyncMock()
    client.base_url = "http://chatwoot"
    client.get_contact_by_source.side_effect = missing()
    client.search_contacts.return_value = {"payload": []}
    client.get_inbox.return_value = {"channel_type": "Channel::Api"}
    client.create_contact.return_value = {"id": 91}
    client.get_contact.side_effect = lambda account, cid: {"id": cid, "identifier": "whatsapp-owned"}
    return client


def options(db, key):
    instance = db.query(Instance).filter_by(instance_key=key).one()
    return dict(instance_id=instance.id, account_id=1, inbox_id=5, peer_id="123", payload={"name": "Test"}, peer_type="user")


@pytest.mark.anyio
@pytest.mark.parametrize("identifier", [None, "", "BALE_PV:123", "whatsapp-owned"])
async def test_http_boundary_never_writes_identifier(identifier):
    client = ChatwootClient("http://chatwoot", "token")
    requests = []
    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 91})
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    original = {"name": "Safe", "identifier": identifier}
    await client.create_contact(1, original)
    await client.update_contact(1, 91, original)
    assert requests and all("identifier" not in body for body in requests)
    assert "identifier" in original
    await client._client.aclose()


@pytest.mark.anyio
async def test_lost_create_response_recovers_without_second_post(db_session, sample_instance):
    client = fake_client()
    client.get_contact_by_source.side_effect = [missing(), {"id": 91}]
    client.create_contact.side_effect = httpx.ReadTimeout("accepted but response lost")
    result = await ContactIdentityService().resolve(db_session, client, **options(db_session, sample_instance))
    assert result == (91, False)
    client.create_contact.assert_awaited_once()
    assert db_session.query(ContactMapping).one().chatwoot_contact_id == "91"
    anchor = db_session.query(ContactCreation).one()
    assert client.create_contact.call_args.args[1]["source_id"] == anchor.id


@pytest.mark.anyio
async def test_local_commit_failure_can_recover_by_durable_source(db_session, sample_instance, monkeypatch):
    service = ContactIdentityService()
    client = fake_client()
    args = options(db_session, sample_instance)
    real_finish = service._finish
    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable after remote acceptance")
    monkeypatch.setattr(service, "_finish", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.resolve(db_session, client, **args)
    db_session.rollback()
    saved_key = db_session.query(ContactCreation).one().id
    monkeypatch.setattr(service, "_finish", real_finish)
    client.get_contact_by_source.side_effect = None
    client.get_contact_by_source.return_value = {"id": 91}
    assert await service.resolve(db_session, client, **args) == (91, False)
    assert client.get_contact_by_source.call_args.args[-1] == saved_key
    client.create_contact.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_uncertain_lookup_never_creates_contact(db_session, sample_instance, status):
    client = fake_client()
    client.get_contact_by_source.side_effect = httpx.HTTPStatusError("unavailable", request=httpx.Request("POST", "http://chatwoot"), response=httpx.Response(status))
    with pytest.raises(httpx.HTTPStatusError):
        await ContactIdentityService().resolve(db_session, client, **options(db_session, sample_instance))
    client.create_contact.assert_not_awaited()


@pytest.mark.anyio
async def test_deleted_mapping_is_recovered(db_session, sample_instance):
    client = fake_client()
    args = options(db_session, sample_instance)
    ContactMappingRepository(db_session).save(args["instance_id"], "123", "55")
    db_session.commit()
    client.get_contact.side_effect = missing()
    assert await ContactIdentityService().resolve(db_session, client, **args) == (91, True)
    assert db_session.query(ContactMapping).one().chatwoot_contact_id == "91"


@pytest.mark.anyio
async def test_account_change_stops_routing(db_session, sample_instance):
    client = fake_client()
    args = options(db_session, sample_instance)
    ContactMappingRepository(db_session).save(args["instance_id"], "123", "55", chatwoot_scope=contact_scope(client, 2))
    db_session.commit()
    with pytest.raises(ContactMappingConflict):
        await ContactIdentityService().resolve(db_session, client, **args)
    client.create_contact.assert_not_awaited()
    client.get_contact.assert_not_awaited()


@pytest.mark.anyio
async def test_phone_reuses_whatsapp_contact_unchanged(db_session, sample_instance):
    client = fake_client()
    args = options(db_session, sample_instance)
    args["payload"]["phone_number"] = "+989123456789"
    contact = {"id": 91, "identifier": "989123456789@s.whatsapp.net", "phone_number": "+989123456789"}
    client.search_contacts.return_value = {"payload": [contact]}
    assert await ContactIdentityService().resolve(db_session, client, **args) == (91, False)
    client.create_contact.assert_not_awaited()
    client.update_contact.assert_not_awaited()
    assert contact["identifier"].endswith("@s.whatsapp.net")


@pytest.mark.anyio
async def test_non_api_inbox_never_creates(db_session, sample_instance):
    client = fake_client()
    client.get_inbox.return_value = {"channel_type": "Channel::Whatsapp"}
    with pytest.raises(RuntimeError, match="api_inbox"):
        await ContactIdentityService().resolve(db_session, client, **options(db_session, sample_instance))
    client.create_contact.assert_not_awaited()


@pytest.mark.anyio
async def test_search_paginates_and_rejects_ambiguity():
    client = fake_client()
    client.search_contacts.side_effect = [
        {"payload": [{"id": i, "identifier": "fuzzy"} for i in range(1, 16)]},
        {"payload": [{"id": 91, "identifier": "BALE_PV:123"}]},
    ]
    service = ContactIdentityService()
    assert (await service._exact(client, 1, "BALE_PV:123", "identifier"))["id"] == 91
    assert client.search_contacts.call_args.kwargs["page"] == 2
    client.search_contacts.side_effect = None
    client.search_contacts.return_value = {"payload": [{"id": 91, "identifier": "same"}, {"id": 92, "identifier": "same"}]}
    with pytest.raises(ContactMappingConflict):
        await service._exact(client, 1, "same", "identifier")


def test_mapping_cannot_be_rebound_or_ambiguously_reversed(db_session, sample_instance):
    args = options(db_session, sample_instance)
    repo = ContactMappingRepository(db_session)
    repo.save(args["instance_id"], "123", "91")
    with pytest.raises(ContactMappingConflict):
        repo.save(args["instance_id"], "123", "92")
    with pytest.raises(ContactMappingConflict):
        repo.save(args["instance_id"], "456", "91")
    # Older databases may already contain the ambiguity; reverse lookup must
    # still reject those rows instead of choosing either recipient.
    db_session.add(ContactMapping(instance_id=args["instance_id"], platform_contact_id="456", chatwoot_contact_id="91"))
    db_session.flush()
    with pytest.raises(ContactMappingConflict):
        repo.unique_platform_id_for_chatwoot(args["instance_id"], "91")


@pytest.mark.anyio
async def test_concurrent_first_routes_share_creation_key(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'contacts.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as seed:
        platform = PlatformType(key="bale_enterprise", display_name="Bale", capabilities_json={}, metadata_schema_json={})
        seed.add(platform)
        seed.flush()
        instance = Instance(instance_key="concurrent", platform_type_id=platform.id, platform_metadata_encrypted="", chatwoot_config_encrypted="", proxy_config_encrypted="")
        seed.add(instance)
        seed.commit()
        instance_id = instance.id
    client = fake_client()
    barrier = asyncio.Event()
    lookups = 0
    remote = {}
    async def source(account, inbox, source_id):
        nonlocal lookups
        lookups += 1
        if lookups <= 2:
            if lookups == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 3)
            raise missing()
        if (inbox, source_id) in remote:
            return remote[(inbox, source_id)]
        raise missing()
    async def create(account, payload):
        key = (payload["inbox_id"], payload["source_id"])
        if key in remote:
            raise httpx.HTTPStatusError("unique source collision", request=httpx.Request("POST", "http://chatwoot"), response=httpx.Response(422))
        remote[key] = {"id": 91}
        return remote[key]
    client.get_contact_by_source.side_effect = source
    client.create_contact.side_effect = create
    async def route(inbox):
        with Session() as db:
            return await ContactIdentityService().resolve(db, client, instance_id=instance_id, account_id=1, inbox_id=inbox, peer_id="123", payload={"name": "Test"})
    results = await asyncio.gather(route(5), route(6))
    assert {result[0] for result in results} == {91}
    assert len(remote) == 1
    with Session() as db:
        assert db.query(ContactMapping).count() == 1
        assert db.query(ContactCreation).count() == 1
    engine.dispose()


@pytest.mark.anyio
async def test_http_create_timeout_is_not_blindly_retried():
    client = ChatwootClient("http://chatwoot", "token")
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost reply", request=request)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        await client.create_contact(1, {"name": "Test"})
    assert calls == 1
    await client._client.aclose()


@pytest.mark.parametrize("prefix", ["BALE_PV", "TELEGRAM", "EITAA_PV"])
def test_foreign_whatsapp_identifier_is_not_a_destination(prefix):
    from wootify.application.messaging.destination_resolver import DestinationResolver
    payload = {"conversation": {"contact_inbox": {"source_id": "989123456789@s.whatsapp.net"}, "meta": {"sender": {"identifier": "989123456789@s.whatsapp.net"}}}}
    assert DestinationResolver.extract_destination(payload, expected_prefix=prefix, known_prefixes={prefix}) == (None, None)


def test_migration_preserves_existing_contact_map(tmp_path):
    root = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    env = dict(os.environ, DATABASE_URL=url, DATABASE_NAME="", PYTHONPATH=str(root / "backend" / "src"))
    def upgrade(revision):
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=root, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    upgrade("f7a0c02d21b1")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO contact_mappings (id, instance_id, platform_contact_id, chatwoot_contact_id) VALUES ('test-map', 'test-instance', '123', '91')"))
    upgrade("head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT platform_contact_id, chatwoot_contact_id, chatwoot_scope FROM contact_mappings WHERE id='test-map'")).one() == ("123", "91", None)
    assert "contact_creations" in inspect(engine).get_table_names()
    engine.dispose()
