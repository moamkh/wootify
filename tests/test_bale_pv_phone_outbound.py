"""Tests for Bale PV phone-number outbound messaging and failure notes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.chatwoot_bridge_service as bridge_module
from app.models import Base, Instance
from app.services.chatwoot_bridge_service import ChatwootBridgeService


@pytest.fixture()
def db():
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _make_instance(db, key='inst-1') -> Instance:
    row = Instance(
        instance_key=key,
        platform_type_id='pt-1',
        is_enabled=True,
        platform_metadata_encrypted='',
        chatwoot_config_encrypted='',
        proxy_config_encrypted='',
    )
    db.add(row)
    db.commit()
    return row


class _FakeAdapter:
    def __init__(self, send_error: Exception | None = None):
        self.send_error = send_error
        self.sent_texts = []
        self.cached_hashes = {}
        self.resolved_phones = []

    def cache_access_hash(self, user_id, access_hash):
        self.cached_hashes[user_id] = access_hash

    async def resolve_phone_to_user(self, phone):
        self.resolved_phones.append(phone)
        return {'id': 555000111, 'access_hash': '998877', 'name': 'Ali Test'}

    async def send_text(self, peer_id, content, reply_to=None):
        if self.send_error is not None:
            raise self.send_error
        self.sent_texts.append({'peer_id': peer_id, 'content': content, 'reply_to': reply_to})
        return {'ok': True}


class _FakeClient:
    def __init__(self):
        self.posted_messages = []
        self.updated_contacts = []

    async def post_message(self, account_id, conversation_id, data):
        self.posted_messages.append(
            {'account_id': account_id, 'conversation_id': conversation_id, 'data': data}
        )
        return {'id': 1}

    async def update_contact(self, account_id, contact_id, data):
        self.updated_contacts.append(data)
        return {'id': contact_id}


def _service(monkeypatch, db, adapter, client, instance):
    runtime = SimpleNamespace(
        status='open', platform_type='bale_pv_enterprise', adapter=adapter
    )
    monkeypatch.setattr(bridge_module, 'get_runtime', lambda key: runtime)
    service = ChatwootBridgeService()
    monkeypatch.setattr(
        service,
        '_chatwoot_client_for_instance',
        lambda db_, key: (instance, {'account_id': 1}, client),
    )
    return service


def _payload(phone='09123456789'):
    return {
        'event': 'message_created',
        'message_type': 'outgoing',
        'content': 'hello there',
        'conversation': {
            'id': 77,
            'meta': {'sender': {'id': 9, 'phone_number': phone}},
            'messages': [],
        },
    }


class TestPhoneDetection:
    @pytest.mark.parametrize('value', [
        '989123456789', '+989123456789', '00989123456789', '09123456789',
        '9123456789', '+98 912 345 6789', '0912 345 6789',
    ])
    def test_phone_formats_detected(self, value):
        assert ChatwootBridgeService._is_phone_number_destination(value) is True

    @pytest.mark.parametrize('value', [None, '', '12345', 'BALE_PV:12345', 'ali'])
    def test_non_phone_values_rejected(self, value):
        assert ChatwootBridgeService._is_phone_number_destination(value) is False

    @pytest.mark.parametrize('raw,expected', [
        ('09123456789', '989123456789'),
        ('+989123456789', '989123456789'),
        ('00989123456789', '989123456789'),
        ('9123456789', '989123456789'),
        ('+98 912 345 6789', '989123456789'),
    ])
    def test_normalization(self, raw, expected):
        assert ChatwootBridgeService._normalize_bale_pv_phone(raw) == expected


def test_phone_contact_resolved_and_message_sent(monkeypatch, db):
    instance = _make_instance(db)
    adapter = _FakeAdapter()
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', _payload()))

    assert result['ok'] is True
    assert result['peer_id'] == '555000111'
    assert adapter.sent_texts == [
        {'peer_id': '555000111', 'content': 'hello there', 'reply_to': None}
    ]
    # Contact identifier rewritten to the resolved Bale user id.
    assert client.updated_contacts and client.updated_contacts[0]['phone_number'] == '989123456789'
    # Resolved user cached in the DB for future sends.
    from app.models import BalePvPhoneResolvedUser
    cached = db.query(BalePvPhoneResolvedUser).filter_by(phone_number='989123456789').one()
    assert cached.bale_user_id == 555000111
    # No failure note on success.
    assert client.posted_messages == []


def test_failed_send_posts_private_note(monkeypatch, db):
    instance = _make_instance(db)
    adapter = _FakeAdapter(send_error=RuntimeError('send_text failed: Forbidden'))
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', _payload()))

    assert result['ok'] is False
    assert result['message'] == 'delivery_failed'
    assert len(client.posted_messages) == 1
    note = client.posted_messages[0]
    assert note['conversation_id'] == 77
    assert note['data']['private'] is True
    assert '555000111' in note['data']['content']
    assert 'Forbidden' in note['data']['content']


def test_unresolvable_phone_posts_private_note(monkeypatch, db):
    instance = _make_instance(db)

    class _NoUserAdapter(_FakeAdapter):
        async def resolve_phone_to_user(self, phone):
            raise RuntimeError('Phone number 989123456789 not found on Bale')

    adapter = _NoUserAdapter()
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', _payload()))

    assert result['ok'] is False
    assert result['message'] == 'delivery_failed'
    assert len(client.posted_messages) == 1
    assert 'not found on Bale' in client.posted_messages[0]['data']['content']


def test_contact_without_identifier_or_phone_reports_failure(monkeypatch, db):
    instance = _make_instance(db)
    adapter = _FakeAdapter()
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    payload = _payload()
    payload['conversation']['meta']['sender'] = {'id': 9}
    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', payload))

    assert result['ok'] is False
    assert result['detail'] == 'peer_id_not_found'
    assert len(client.posted_messages) == 1
    assert client.posted_messages[0]['data']['private'] is True


def test_prefixed_identifier_is_never_re_resolved_as_phone(monkeypatch, db):
    """BALE_PV:<id> is a resolved user id, even when its digits look like a phone."""
    instance = _make_instance(db)
    adapter = _FakeAdapter()
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    payload = _payload()
    # 9123456789 is a Bale user id here, NOT a phone number.
    payload['conversation']['meta']['sender'] = {
        'id': 9,
        'identifier': 'BALE_PV:9123456789',
        'phone_number': '+989009998877',
    }
    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', payload))

    assert result['ok'] is True
    assert result['peer_id'] == '9123456789'
    assert adapter.sent_texts[0]['peer_id'] == '9123456789'
    assert adapter.resolved_phones == []  # no phone resolution attempted
    assert client.updated_contacts == []  # contact identifier left untouched


def test_unprefixed_phone_like_identifier_is_resolved(monkeypatch, db):
    instance = _make_instance(db)
    adapter = _FakeAdapter()
    client = _FakeClient()
    service = _service(monkeypatch, db, adapter, client, instance)

    payload = _payload()
    payload['conversation']['meta']['sender'] = {'id': 9, 'identifier': '9123456789'}
    result = asyncio.run(service.handle_chatwoot_webhook(db, 'inst-1', payload))

    assert result['ok'] is True
    assert adapter.resolved_phones == ['989123456789']
    assert adapter.sent_texts[0]['peer_id'] == '555000111'


# ---------------------------------------------------------------------------
# ImportContacts wire format regression (uid was mis-parsed as client index)
# ---------------------------------------------------------------------------

# Captured from a live bale.users.v1.Users/ImportContacts response:
# seq=26, imported contact {uid=1755271951, client_id=1}.
_IMPORT_RESPONSE_HEX = '101a2208088fa6fdc4061001'


def _load_users_response_bytes(uid, access_hash, name):
    """Serialize a LoadUsers response containing one User message."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bale_pv_connector' / 'src'))

    from bale_pv_connector.protobuf_wire import ProtobufMessage

    user = ProtobufMessage()
    user.add_int32(1, uid)
    user.add_int64(2, access_hash)
    user.add_string(3, name)
    response = ProtobufMessage()
    response.add_message(1, user)
    return response.serialize()


def test_parse_import_contacts_response_decodes_imported_contact():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bale_pv_connector' / 'src'))

    from bale_pv_connector.dialog_parser import parse_import_contacts_response

    parsed = parse_import_contacts_response(bytes.fromhex(_IMPORT_RESPONSE_HEX))

    assert parsed['seq'] == 26
    assert parsed['users'] == []
    assert parsed['imported'] == [{'uid': 1755271951, 'client_id': 1}]


def test_resolve_phone_to_user_uses_imported_uid_and_loads_access_hash():
    from app.connectors.bale_pv_connector import BalePvConnector

    class _FakeMessagingClient:
        async def import_contacts(self, phones, optimizations=None):
            return bytes.fromhex(_IMPORT_RESPONSE_HEX)

        async def load_users(self, peers):
            assert peers == [{'uid': 1755271951}]
            return _load_users_response_bytes(1755271951, 4242, 'Ali')

    connector = BalePvConnector()
    connector._instances['inst'] = SimpleNamespace(
        auth_state='authenticated', client=_FakeMessagingClient()
    )

    user = asyncio.run(connector.resolve_phone_to_user('inst', '09123456789'))

    assert user['id'] == 1755271951
    assert user['access_hash'] == 4242


def test_resolve_phone_to_user_falls_back_to_bare_uid_when_load_users_fails():
    from app.connectors.bale_pv_connector import BalePvConnector

    class _FakeMessagingClient:
        async def import_contacts(self, phones, optimizations=None):
            return bytes.fromhex(_IMPORT_RESPONSE_HEX)

        async def load_users(self, peers):
            raise RuntimeError('load users unavailable')

    connector = BalePvConnector()
    connector._instances['inst'] = SimpleNamespace(
        auth_state='authenticated', client=_FakeMessagingClient()
    )

    user = asyncio.run(connector.resolve_phone_to_user('inst', '989136421196'))

    assert user['id'] == 1755271951
    assert user['access_hash'] is None


def test_resolve_phone_to_user_raises_when_phone_not_on_bale():
    from app.connectors.bale_pv_connector import BalePvConnector

    class _FakeMessagingClient:
        async def import_contacts(self, phones, optimizations=None):
            return bytes.fromhex('101a')  # seq=26, no users, no imported

    connector = BalePvConnector()
    connector._instances['inst'] = SimpleNamespace(
        auth_state='authenticated', client=_FakeMessagingClient()
    )

    with pytest.raises(RuntimeError, match='not found on Bale'):
        asyncio.run(connector.resolve_phone_to_user('inst', '989136421196'))
