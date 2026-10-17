import pytest

from wootify.plugins.eitaa_pv.adapter import EitaaPvAdapter
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService


@pytest.mark.parametrize('peer_id,response', [
    ('6239618', {'_': 'updates', 'random_id': 123,
        'updates': [{'_': 'updateMessageID', 'id': 24, 'random_id': 123}]}),
    ('6239618', {'_': 'updates', 'updates': [{'_': 'updateNewMessage', 'message': {'id': 24, 'out': True,
        'peer_id': {'_': 'peerUser', 'user_id': 6239618}}}]}),
    ('channel:6239618', {'_': 'updatesCombined', 'updates': [{'_': 'updateNewChannelMessage', 'message': {'id': 24, 'out': True,
        'peer_id': {'_': 'peerChannel', 'channel_id': 6239618}}}]}),
    ('6239618', {'_': 'updateShortSentMessage', 'id': 24}),
])
def test_sent_id_can_be_persisted_and_only_its_echo_is_suppressed(peer_id, response):
    adapter = EitaaPvAdapter('test', {})
    result = adapter._sent_result(peer_id, response, mirror_echo=False)
    assert ChatwootBridgeService._extract_platform_rid(result) == '24'
    raw = {'chat_id': peer_id, 'message': {'id': 24, 'out': True, 'message': ''}}
    assert adapter.normalize_incoming_update(raw) is None
    # Native-app sends, other conversations, and edits must still sync.
    assert adapter.normalize_incoming_update({**raw, 'message': {**raw['message'], 'id': 25}}) is not None
    assert adapter.normalize_incoming_update({**raw, 'chat_id': '123'}) is not None
    assert adapter.normalize_incoming_update({**raw, 'message': {**raw['message'], 'edit_date': 100}}) is not None


def test_requested_echo_is_preserved():
    adapter = EitaaPvAdapter('test', {})
    adapter._sent_result('6239618', {'_': 'updateShortSentMessage', 'id': 24}, mirror_echo=True)
    assert adapter.normalize_incoming_update({'chat_id': '6239618', 'message': {'id': 24, 'out': True}}) is not None
