from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bale_pv_connector.protobuf_wire import ProtobufMessage
from wootify.plugins.bale_pv.connector import BalePvConnector, BalePvInstanceRuntime
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService


def _users(name, local_name=None):
    user = ProtobufMessage()
    user.add_int32(1, 456)
    user.add_string(3, name)
    if local_name:
        alias = ProtobufMessage()
        alias.add_string(1, local_name)
        user.add_message(4, alias)
    response = ProtobufMessage()
    response.add_message(1, user)
    return response.serialize()


@pytest.mark.anyio
@pytest.mark.parametrize('alias,expected', [(None, 'Public Name'), ('Saved Contact', 'Saved Contact')])
async def test_first_outgoing_resolves_recipient_not_self(alias, expected):
    client = SimpleNamespace(load_users=AsyncMock(return_value=_users('Public Name', alias)))
    runtime = BalePvInstanceRuntime('test', '', client=client, self_user_id=999)
    runtime.user_cache[999] = 'Agent'
    connector = BalePvConnector()
    connector._instances['test'] = runtime
    update = {'message': {'chat': {'id': '456', 'type': 'private'}, 'from': {'id': 999}, '_outgoing': True, '_sender_access_hash': 12345}}
    await connector._resolve_unknown_sender_info(runtime, [update])
    client.load_users.assert_awaited_once_with([{'uid': 456, 'access_hash': 0}])
    assert connector.get_user_name('test', 456) == expected


@pytest.mark.anyio
async def test_missing_recipient_name_has_retry_backoff():
    client = SimpleNamespace(load_users=AsyncMock(return_value=b''), load_dialogs=AsyncMock(return_value=b''))
    runtime = BalePvInstanceRuntime('test', '', client=client)
    connector = BalePvConnector()
    update = {'message': {'chat': {'id': '456', 'type': 'private'}, 'from': {'id': 999}, '_outgoing': True}}
    await connector._resolve_unknown_sender_info(runtime, [update])
    await connector._resolve_unknown_sender_info(runtime, [update])
    assert client.load_users.await_count == 1
    assert client.load_dialogs.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize('existing_name,should_rename', [('Bale User 456', True), ('Agent Custom Name', False)])
async def test_private_placeholder_repair_preserves_custom_names(existing_name, should_rename):
    service = ChatwootBridgeService()
    instance = SimpleNamespace(platform_type=SimpleNamespace(key='bale_pv_enterprise'))
    client = AsyncMock()
    client.get_contact.return_value = {'payload': {'name': existing_name}}
    event = {'chat_id': '456', 'chat_type': 'private', 'from_name': 'Saved Contact', 'message_id': '1', 'service_notice': True}
    with patch.object(service, '_chatwoot_client_for_instance', return_value=(instance, {'account_id': 1, 'inbox_id': 2}, client)), patch.object(service, '_get_or_create_contact', new=AsyncMock(return_value=(10, False))):
        result = await service.ingest_platform_event(MagicMock(), 'test', event)
    assert result['contact_only']
    name_updates = [
        call
        for call in client.update_contact.await_args_list
        if len(call.args) >= 3 and call.args[2].get('name') == 'Saved Contact'
    ]
    if should_rename:
        assert len(name_updates) == 1
    else:
        assert name_updates == []
