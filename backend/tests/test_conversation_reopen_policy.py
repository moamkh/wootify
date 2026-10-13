"""Inbox settings remain editable and govern every conversation delivery path."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wootify.application.instances.service import InstanceService
from wootify.application.messaging.bridge import BridgeService
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
from wootify.application.messaging.conversation_policy import reopen_enabled, select_conversation
from wootify.infrastructure.persistence.models import (
    Conversation, Instance, MessageMapping, MessageDirection, MessageStatus, MessageKind, PlatformType,
    EnterpriseSessionStatus, EnterpriseUserState,
)
from wootify.infrastructure.security.crypto import encryptor
from wootify.presentation.http.schemas.api_v1 import InstancePatchRequest
from wootify.application.enterprise.bale import EnterpriseBaleService
from wootify.application.enterprise.telegram import EnterpriseTelegramService


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def inbox_instance(db_session):
    platform = PlatformType(key='bale_pv_enterprise', display_name='Bale PV', capabilities_json={}, metadata_schema_json={})
    db_session.add(platform)
    db_session.flush()
    instance = Instance(instance_key='reopen-test', platform_type_id=platform.id, is_enabled=True, platform_metadata_encrypted=encryptor.encrypt_json({'bale_pv_phone_number': '989123456789'}),
                        chatwoot_config_encrypted=encryptor.encrypt_json({'base_url': 'http://chatwoot', 'account_id': 3, 'inbox_id': 57, 'api_access_token': 'secret'}), proxy_config_encrypted='')
    db_session.add(instance)
    db_session.commit()
    return instance


def mapped(db, instance, *, active=True):
    row = Conversation(instance_id=instance.id, platform_conversation_id='42', chatwoot_conversation_id='100', chatwoot_contact_id='400', chatwoot_inbox_id='57', is_active=active)
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize('value,expected', [(True, True), (False, False), ('false', False), ('true', True), (None, False)])
def test_policy_default_and_bool_parsing(value, expected):
    assert reopen_enabled({'reopen_conversation': value}, 57) is expected
    assert reopen_enabled({'reopen_conversation': True, 'reopen_conversation_by_inbox': {'57': False}}, 57) is False
    assert reopen_enabled({'reopen_conversation_by_inbox': {'57': True}}, 58) is False


def test_partial_patch_is_live_and_preserves_credentials_and_other_inbox_settings(db_session, inbox_instance):
    service = InstanceService()
    service.update_instance(db_session, inbox_instance.instance_key, InstancePatchRequest(chatwoot={'reopen_conversation_by_inbox': {'57': True, '58': True}}))
    service.update_instance(db_session, inbox_instance.instance_key, InstancePatchRequest(chatwoot={'reopen_conversation_by_inbox': {'57': False}}))
    config = service.get_runtime_instance(db_session, inbox_instance.instance_key).chatwoot
    assert config['reopen_conversation_by_inbox'] == {'57': False, '58': True}
    assert config['api_access_token'] == 'secret'
    assert config['account_id'] == 3 and config['inbox_id'] == 57


@pytest.mark.parametrize('value', [[], {'wrong': True}, {'0': True}, {'57': 'false'}])
def test_invalid_settings_are_rejected(value):
    with pytest.raises(ValueError):
        InstanceService()._normalize_chatwoot_config({'reopen_conversation_by_inbox': value})


@pytest.mark.anyio
@pytest.mark.parametrize('platform', ['bale_pv_enterprise', 'eitaa_pv_enterprise', 'instagram_pv_enterprise'])
@pytest.mark.parametrize('active', [True, False])
@pytest.mark.parametrize('reopen', [True, False])
async def test_resolved_conversation_obeys_policy_even_after_missed_webhook(db_session, inbox_instance, platform, active, reopen):
    inbox_instance.platform_type.key = platform
    original = mapped(db_session, inbox_instance, active=active)
    message = MessageMapping(conversation_id=original.id, direction=MessageDirection.platform_to_chatwoot,
                             message_kind=MessageKind.text, platform_message_id='old', chatwoot_message_id='20', status=MessageStatus.sent)
    db_session.add(message)
    db_session.commit()
    client = AsyncMock()
    client.list_contact_conversations.return_value = {'payload': [{'id': 100, 'inbox_id': 57, 'status': 'resolved'}]}
    client.create_conversation.return_value = {'id': 101}
    result = await ChatwootBridgeService()._get_or_create_conversation(db_session, client, instance=inbox_instance, account_id=3, inbox_id=57,
                    contact_id=400, chat_id='42', chatwoot_config={'reopen_conversation_by_inbox': {'57': reopen}})
    assert result.chatwoot_conversation_id == ('100' if reopen else '101')
    if reopen:
        client.toggle_conversation_status.assert_awaited_once_with(3, 100, 'open')
        client.create_conversation.assert_not_awaited()
    else:
        client.toggle_conversation_status.assert_not_awaited()
        client.create_conversation.assert_awaited_once()
    db_session.refresh(message)
    db_session.refresh(original)
    assert message.conversation_id == original.id
    assert original.chatwoot_conversation_id == '100'
    assert original.is_active is reopen


@pytest.mark.anyio
async def test_reopen_failure_does_not_create_or_rebind(db_session, inbox_instance):
    original = mapped(db_session, inbox_instance, active=False)
    client = AsyncMock()
    client.list_contact_conversations.return_value = {'payload': [{'id': 100, 'inbox_id': 57, 'status': 'resolved'}]}
    client.toggle_conversation_status.side_effect = RuntimeError('network failure')
    with pytest.raises(RuntimeError, match='network failure'):
        await ChatwootBridgeService()._get_or_create_conversation(db_session, client, instance=inbox_instance, account_id=3, inbox_id=57, contact_id=400, chat_id='42', chatwoot_config={'reopen_conversation': True})
    client.create_conversation.assert_not_awaited()
    assert not original.is_active


def test_choose_open_before_resolved_and_never_cross_inboxes():
    rows = [{'id': 300, 'inbox_id': 58, 'status': 'open'}, {'id': 200, 'inbox_id': 57, 'status': 'resolved'}, {'id': 100, 'inbox_id': 57, 'status': 'open'}]
    assert select_conversation(rows, 57, True)['id'] == 100
    assert BridgeService()._select_reusable_contact_conversation(rows, inbox_id=57, reopen_conversation=False)['id'] == 100
    assert select_conversation(rows[:2], 57, True)['id'] == 200
    assert select_conversation(rows[:2], 57, False) is None


@pytest.mark.anyio
@pytest.mark.parametrize('service_cls', [EnterpriseBaleService, EnterpriseTelegramService])
@pytest.mark.parametrize('remote_status', ['resolved', 'open', 'missing'])
async def test_enterprise_reopens_only_existing_resolved_conversation(service_cls, remote_status):
    service = service_cls.__new__(service_cls)
    repo, client = Mock(), AsyncMock()
    service._sessions = Mock(return_value=repo)
    service._get_chatwoot_client = Mock(return_value=client)
    service._get_remote_route_session_status = AsyncMock(return_value=remote_status)
    runtime = SimpleNamespace(chatwoot={'account_id': 3, 'reopen_conversation_by_inbox': {'57': True}}, instance=SimpleNamespace(instance_key='test'))
    session = SimpleNamespace(id='session', status=EnterpriseSessionStatus.resolved, chatwoot_inbox_id='57', chatwoot_conversation_id='100', route_key='sales', user_present=False)
    result = await service._resolve_reusable_route_session(None, runtime, session)
    if remote_status == 'missing':
        assert result is None
        client.toggle_conversation_status.assert_not_awaited()
    else:
        assert result is session and session.status == EnterpriseSessionStatus.open
        assert client.toggle_conversation_status.await_count == (1 if remote_status == 'resolved' else 0)


@pytest.mark.anyio
@pytest.mark.parametrize('service_cls', [EnterpriseBaleService, EnterpriseTelegramService])
async def test_enterprise_off_does_not_reopen_or_look_up_resolved_session(service_cls):
    service = service_cls.__new__(service_cls)
    service._get_remote_route_session_status = AsyncMock()
    runtime = SimpleNamespace(chatwoot={'reopen_conversation_by_inbox': {'57': False}})
    session = SimpleNamespace(status=EnterpriseSessionStatus.resolved, chatwoot_inbox_id='57')
    assert await service._resolve_reusable_route_session(None, runtime, session) is None
    service._get_remote_route_session_status.assert_not_awaited()


@pytest.mark.parametrize('service_cls,state', [(EnterpriseBaleService, EnterpriseUserState.live_sales), (EnterpriseTelegramService, 'sales')])
def test_enterprise_customer_first_message_after_resolution_keeps_route(service_cls, state):
    service = service_cls.__new__(service_cls)
    session = SimpleNamespace(status=EnterpriseSessionStatus.resolved)
    repo = Mock()
    repo.get_unresolved_for_user_route.return_value = None
    repo.get_latest_for_user_route.return_value = session
    service._sessions = Mock(return_value=repo)
    user = SimpleNamespace(id='user', current_state=state)
    lookup = service._active_live_session_for_state if service_cls is EnterpriseBaleService else service._active_live_session
    assert lookup(None, user, runtime=SimpleNamespace()) is session
    repo.get_latest_for_user_route.assert_called_once_with('user', 'sales')


@pytest.mark.anyio
@pytest.mark.parametrize('text', ['hello', None])
async def test_delayed_duplicate_does_not_reopen_resolved_ticket(db_session, inbox_instance, monkeypatch, text):
    original = mapped(db_session, inbox_instance, active=False)
    db_session.add(MessageMapping(conversation_id=original.id, direction=MessageDirection.platform_to_chatwoot,
                   message_kind=MessageKind.text, platform_message_id='old', chatwoot_message_id='20', status=MessageStatus.sent, platform_payload_json={'text': text}))
    db_session.commit()
    service, client = ChatwootBridgeService(), AsyncMock()
    monkeypatch.setattr(service, '_chatwoot_client_for_instance', lambda *args: (inbox_instance, {'account_id': 3, 'inbox_id': 57, 'reopen_conversation': True}, client))
    assert (await service.ingest_platform_event(db_session, inbox_instance.instance_key, {'chat_id': '42', 'message_id': 'old', 'text': text}))['duplicate']
    client.list_contact_conversations.assert_not_awaited()
    client.toggle_conversation_status.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('platform', ['bale', 'telegram'])
@pytest.mark.parametrize('reopen', [True, False])
@pytest.mark.parametrize('active_mapping', [True, False])
async def test_standard_bot_delivery_uses_inbox_policy(db_session, inbox_instance, monkeypatch, platform, reopen, active_mapping):
    inbox_instance.platform_type.key = platform
    runtime = SimpleNamespace(instance=inbox_instance, platform_type=inbox_instance.platform_type,
                              chatwoot={'account_id': 3, 'inbox_id': 57, 'reopen_conversation_by_inbox': {'57': reopen}}, feature_flags={})
    service, client = BridgeService(), AsyncMock()
    if active_mapping:
        mapped(db_session, inbox_instance)
    monkeypatch.setattr(service, '_require_runtime_instance', lambda *args: runtime)
    monkeypatch.setattr(service, '_get_chatwoot_client', lambda *args: client)
    monkeypatch.setattr(service, '_get_or_create_contact', AsyncMock(return_value=400))
    client.list_contact_conversations.return_value = {'payload': [{'id': 100, 'inbox_id': 57, 'status': 'resolved'}]}
    client.create_conversation.return_value = {'id': 101}
    client.post_message.return_value = {'id': 900}
    client.get_contact.return_value = {'id': 400, 'name': 'Customer'}
    result = await service.ingest_platform_event(db_session, inbox_instance.instance_key, {'chat_id': '42', 'text': 'hello', 'message_id': 'new'})
    assert result['chatwoot_conversation_id'] == ('100' if reopen else '101')
    assert client.toggle_conversation_status.await_count == int(reopen)
    assert client.create_conversation.await_count == int(not reopen)


@pytest.mark.anyio
@pytest.mark.parametrize('service_cls', [EnterpriseBaleService, EnterpriseTelegramService])
@pytest.mark.parametrize('reopen', [True, False])
async def test_enterprise_open_route_applies_individual_inbox_policy(service_cls, reopen):
    service = service_cls.__new__(service_cls)
    previous = SimpleNamespace(id='old', status=EnterpriseSessionStatus.resolved, chatwoot_inbox_id='57', chatwoot_conversation_id='100', route_key='sales', user_present=False)
    repo, client = Mock(), AsyncMock()
    repo.get_unresolved_for_user_route.return_value = None
    repo.get_latest_for_user_route.return_value = previous
    service._sessions = Mock(return_value=repo)
    service._ensure_route_inbox = AsyncMock(return_value=57)
    service._get_remote_route_session_status = AsyncMock(return_value='resolved')
    service._get_or_create_contact = AsyncMock(return_value=400)
    service._get_chatwoot_client = Mock(return_value=client)
    client.create_conversation.return_value = {'id': 101}
    runtime = SimpleNamespace(instance=SimpleNamespace(instance_key='test'), chatwoot={'account_id': 3, 'reopen_conversation_by_inbox': {'57': reopen, '58': not reopen}})
    result, created = await service._get_or_open_route_session(None, runtime, SimpleNamespace(id='user'), 'sales')
    assert result.chatwoot_conversation_id == ('100' if reopen else '101')
    assert created is not reopen
    assert client.toggle_conversation_status.await_count == int(reopen)
    assert client.create_conversation.await_count == int(not reopen)


@pytest.mark.anyio
@pytest.mark.parametrize('service_cls', [EnterpriseBaleService, EnterpriseTelegramService])
async def test_changed_enterprise_route_inbox_never_reopens_previous_inbox(service_cls):
    service = service_cls.__new__(service_cls)
    service._ensure_route_inbox = AsyncMock(return_value=58)
    service._resolve_reusable_route_session = AsyncMock()
    replacement = object()
    service._get_or_open_route_session = AsyncMock(return_value=(replacement, True))
    previous = SimpleNamespace(route_key='sales', chatwoot_inbox_id='57')
    assert await service._ensure_forwardable_route_session(None, None, None, previous) is replacement
    service._resolve_reusable_route_session.assert_not_awaited()


def test_existing_inbox_can_be_toggled_through_patch_api(client, inbox_instance):
    for enabled in [True, False, True]:
        response = client.patch(f'/api/v1/instances/{inbox_instance.instance_key}', json={'chatwoot': {'reopen_conversation_by_inbox': {'57': enabled}}})
        assert response.status_code == 200, response.text
        assert response.json()['chatwoot']['reopen_conversation_by_inbox']['57'] is enabled
        assert response.json()['chatwoot']['inbox_id'] == 57
    response = client.patch(f'/api/v1/instances/{inbox_instance.instance_key}', json={'chatwoot': {'reopen_conversation_by_inbox': {'57': 'false'}}})
    assert response.status_code == 400
