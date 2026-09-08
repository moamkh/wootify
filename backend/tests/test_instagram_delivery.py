"""Regression coverage for Instagram SDK models, delivery and auth wiring."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from instagrapi.types import DirectMedia, DirectMessage, DirectThread, ReplyMessage

from wootify.plugins.instagram.connector import InstagramPvConnector, InstagramPvInstanceRuntime
from wootify.plugins.instagram.adapter import InstagramPvAdapter
from wootify.plugins.instagram.polling_service import InstagramPollingService
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
from wootify.application.instances.service import InstanceService
from wootify.paths import VAR_ROOT


def message(i, ts=200, **kwargs):
    return DirectMessage.model_construct(id=str(i), user_id='2', thread_id=100,
        timestamp=datetime.fromtimestamp(ts, timezone.utc), item_type='text', text='hello', **kwargs)


def thread(messages, tid='100'):
    return DirectThread.model_construct(id=tid, messages=messages, users=[], is_group=False, thread_title='')


def setup_connector(messages=None):
    client = Mock()
    client.direct_threads.return_value = [thread(messages)] if messages else []
    client.direct_pending_inbox.return_value = []
    client.direct_messages.return_value = messages or []
    c = InstagramPvConnector()
    r = InstagramPvInstanceRuntime('test', {}, client=client, authenticated=True, self_user_id='1', sync_since=100)
    c._runtimes['test'] = r
    c._dump_settings = AsyncMock()
    c._persist_watermarks = AsyncMock()
    return c, r, client


@pytest.mark.asyncio
async def test_first_customer_message_and_sdk_thread_id_are_delivered():
    c, r, client = setup_connector([message(1)])
    events = await c._fetch_new_messages(r)
    assert [e['message']['message_id'] for e in events] == ['100|1']
    assert r.watermarks == {}
    assert await c._fetch_new_messages(r) == []
    await c.commit_updates('test')
    assert r.watermarks == {'100': '1'}


@pytest.mark.asyncio
async def test_pending_requests_and_backlog_larger_than_inbox_preview():
    all_messages = [message(i) for i in range(1, 76)]
    c, r, client = setup_connector(all_messages[-20:])
    r.watermarks['100'] = '1'
    client.direct_messages.side_effect = lambda tid, amount: all_messages[-amount:]
    client.direct_pending_inbox.return_value = [thread([message(99)], '101')]
    events = await c._fetch_new_messages(r)
    assert len([e for e in events if e['message']['message_id'].startswith('100|')]) == 74
    assert '101' in r.thread_cache
    assert client.direct_threads.call_args.kwargs['amount'] == 20
    assert client.direct_pending_inbox.call_args.kwargs['amount'] == 20


@pytest.mark.asyncio
async def test_inbox_fetch_yields_pending_request_poll_to_waiting_outbound_send():
    c, r, client = setup_connector([message(1)])
    r.outbound_waiters = 1
    await c._fetch_new_messages(r)
    client.direct_pending_inbox.assert_not_called()


def test_sdk_request_pacing_uses_one_second_not_a_twenty_second_wait():
    assert InstagramPvConnector._new_client().request_timeout == 1


@pytest.mark.asyncio
async def test_session_load_cannot_restore_old_twenty_second_request_pacing(monkeypatch, tmp_path):
    c = InstagramPvConnector()
    client = Mock(user_id='1')
    client.login.return_value = True
    client.account_info.return_value = NS(username='test')
    client.request_timeout = 20

    def restore_old_session(_path):
        client.request_timeout = 20

    client.load_settings.side_effect = restore_old_session
    client.set_retry_config.side_effect = lambda **kwargs: setattr(
        client, 'request_timeout', kwargs['request_timeout']
    )
    (tmp_path / 'test.json').write_text('{}')
    monkeypatch.setattr(c, '_new_client', lambda: client)
    monkeypatch.setattr(c, '_safe_session_dir', lambda params: tmp_path)

    await c.connect('test', {'instagram_username': 'test', 'instagram_password': 'secret'})

    assert client.request_timeout == 1


@pytest.mark.asyncio
async def test_cutoff_survives_restart_without_acknowledging_delivery(tmp_path):
    c, r, client = setup_connector([message(1, ts=50), message(2)])
    r.session_path = tmp_path / 'test.json'
    c._persist_watermarks = InstagramPvConnector._persist_watermarks.__get__(c)
    assert len(await c._fetch_new_messages(r)) == 1
    fresh = InstagramPvInstanceRuntime('test', {}, session_path=r.session_path)
    await c._load_watermarks(fresh)
    assert fresh.sync_since == 100
    assert fresh.watermarks == {}
    fresh.client, fresh.authenticated = client, True
    c._runtimes['test'] = fresh
    assert len(await c._fetch_new_messages(fresh)) == 1


@pytest.mark.parametrize('field,kind', [('thumbnail_url','photo'),('video_url','video'),('audio_url','voice')])
def test_actual_sdk_media_models(field, kind):
    media = DirectMedia.model_construct(**{field:'https://example.com/media'})
    c = InstagramPvConnector()
    assert kind in c._extract_attachment_refs(NS(media=media), 'voice_media' if kind=='voice' else 'media')


def test_reply_ids_survive_normalization():
    c, r, _ = setup_connector()
    m = message(2, reply=ReplyMessage.model_construct(id='1'))
    update = c._build_update(r, '100', False, '', {}, m, '1')
    event = InstagramPvAdapter('test', {}).normalize_incoming_update(update)
    assert event['reply_to'] == {'message_id':'100|1'}


def test_send_ack_matches_incoming_id_and_bridge_parser():
    ack = InstagramPvConnector._send_result(message(2), '100')
    assert ChatwootBridgeService._extract_platform_rid({'ok':True, 'result':ack}) == '100|2'
    assert ChatwootBridgeService._extract_platform_rid({'result':{'result':{'rid':123}}}) == '123'


def test_runtime_directory_and_m4a_are_supported():
    assert InstagramPvConnector._safe_session_dir({'instagram_session_dir':str(VAR_ROOT/'sessions/instagram_pv')}) == (VAR_ROOT/'sessions/instagram_pv').resolve()
    assert InstagramPvConnector._sniff_media_type(b'\0\0\0\x18ftypM4A '+b'\0'*20,'voice.m4a') == 'audio/mp4'


@pytest.mark.asyncio
async def test_cookie_auth_adapter_preserves_proxy(monkeypatch):
    sdk = Mock(connect=AsyncMock(), get_self_user_id=Mock(return_value='1'))
    monkeypatch.setattr('wootify.plugins.instagram.adapter.instagram_pv', sdk)
    cfg = {'instagram_sessionid':'cookie', 'proxy':{'enabled':True,'host':'proxy','port':1080}}
    await InstagramPvAdapter('test', cfg).connect()
    assert sdk.connect.call_args.args == ('test',cfg,cfg['proxy'])


@pytest.mark.asyncio
async def test_download_failure_is_retriable(monkeypatch):
    sdk = Mock(download_file_by_id=AsyncMock(side_effect=OSError('temporary')))
    monkeypatch.setattr('wootify.plugins.instagram.adapter.instagram_pv',sdk)
    with pytest.raises(OSError):
        await InstagramPvAdapter('test',{}).resolve_attachments([{'file_id':'file'}])


@pytest.mark.asyncio
async def test_unsupported_document_fails_before_caption_is_sent():
    c, _, _ = setup_connector()
    c.send_text = AsyncMock()
    with pytest.raises(ValueError, match='unsupported'):
        await c.send_media('test','2',b'%PDF-1.7','test.pdf',caption='caption')
    c.send_text.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('media', 'filename', 'error'),
    [
        (b'GIF89a' + b'0' * 32, 'animation.gif', 'GIF'),
        (b'ID3' + b'0' * 32, 'voice.mp3', 'AAC/M4A'),
        (b'OggS' + b'0' * 32, 'voice.ogg', 'AAC/M4A'),
        (b'\0\0\0\x18ftypisom' + b'0' * 32, 'clip.mov', 'MP4'),
    ],
)
async def test_unsupported_instagram_media_fails_before_caption_is_sent(media, filename, error):
    c, _, _ = setup_connector()
    c.send_text = AsyncMock()
    with pytest.raises(ValueError, match=error):
        await c.send_media('test', '2', media, filename, caption='caption')
    c.send_text.assert_not_called()


def test_instagram_media_type_validation_accepts_sdk_supported_extensions():
    validate = InstagramPvConnector._validate_outbound_media
    validate('image/jpeg', 'photo.jpg')
    validate('image/png', 'photo.png')
    validate('image/webp', 'photo.webp')
    validate('video/mp4', 'video.mp4')
    validate('audio/mp4', 'voice.m4a')


@pytest.mark.asyncio
async def test_instagram_edit_is_reported_as_unsupported():
    with pytest.raises(RuntimeError, match='not supported'):
        await InstagramPvAdapter('test', {}).edit_message('2', '100|1', 'revised')


def test_one_unreadable_instance_does_not_stop_all_pollers(monkeypatch):
    service = InstanceService()
    bad, good = NS(is_enabled=True, instance_key='bad'), NS(is_enabled=True, instance_key='good')
    monkeypatch.setattr(service, '_instance_repo', lambda db: NS(list_all=lambda:[bad,good]))
    runtime = NS(platform_type=NS(key='instagram_pv_enterprise'), platform_metadata={'instagram_sessionid':'cookie'})
    monkeypatch.setattr(service, '_to_runtime', Mock(side_effect=[RuntimeError('Failed to decrypt config payload'), runtime]))
    assert service.list_runtime_enabled_instances(None) == [runtime]


@pytest.mark.asyncio
async def test_outage_does_not_drop_retry_queue(monkeypatch):
    from collections import deque
    service = InstagramPollingService()
    event = {'update_id':1}
    service._pending_events['test'] = deque([event])
    service._deliver_update = AsyncMock(return_value=False)
    service._update_runtime_state_with_retry = AsyncMock()
    for _ in range(5):
        service._retry_not_before['test'] = 0
        assert await service._drain_pending('test',None) is False
    assert list(service._pending_events['test']) == [event]


@pytest.mark.asyncio
async def test_proxy_session_is_not_reauthenticated_by_adapter(monkeypatch, tmp_path):
    c = InstagramPvConnector()
    client = Mock(user_id='1')
    client.login.return_value = True
    client.account_info.return_value = NS(username='test')
    monkeypatch.setattr(c, '_new_client', lambda:client)
    monkeypatch.setattr(c, '_safe_session_dir', lambda params:tmp_path)
    monkeypatch.setattr('wootify.plugins.instagram.adapter.instagram_pv', c)
    cfg = {'instagram_username':'test','instagram_password':'secret'}
    proxy = {'enabled':True,'host':'localhost','port':1080,'protocol':'http'}
    await c.connect('test',cfg,proxy)
    await InstagramPvAdapter('test',{**cfg,'proxy':proxy}).connect()
    assert client.login.call_count == 1
    assert c._get_runtime('test').proxy_url == 'http://localhost:1080'


@pytest.mark.asyncio
async def test_false_login_does_not_report_authenticated(monkeypatch, tmp_path):
    c = InstagramPvConnector()
    client = Mock(user_id=None)
    client.login.return_value = False
    monkeypatch.setattr(c, '_new_client', lambda:client)
    monkeypatch.setattr(c, '_safe_session_dir', lambda params:tmp_path)
    with pytest.raises(RuntimeError, match='returned false'):
        await c.connect('test',{'instagram_username':'test','instagram_password':'secret'})
    assert not c.get_auth_state('test')['authenticated']


@pytest.mark.asyncio
async def test_attachment_failure_does_not_reach_chatwoot(monkeypatch):
    adapter = NS(normalize_incoming_update=lambda update:{'attachments':[{'file_id':'x'}]},
                 resolve_attachments=AsyncMock(side_effect=OSError('network')))
    monkeypatch.setattr('wootify.plugins.instagram.polling_service.runtime_registry.get_runtime', lambda key:NS(adapter=adapter))
    bridge = AsyncMock()
    monkeypatch.setattr('wootify.plugins.instagram.polling_service.chatwoot_bridge.ingest_platform_event',bridge)
    assert not await InstagramPollingService()._deliver_update('test',{})
    bridge.assert_not_called()


@pytest.mark.asyncio
async def test_partial_fetch_failure_does_not_skip_undelivered_messages():
    c, r, client = setup_connector()
    client.direct_threads.return_value = [thread([message(1)]),thread([message(2)],'101')]
    client.direct_messages.side_effect = [[message(1)],OSError('temporary')]
    with pytest.raises(OSError):
        await c._fetch_new_messages(r)
    assert r.pending_watermarks == {}
    client.direct_messages.side_effect = [[message(1)],[message(2)]]
    assert len(await c._fetch_new_messages(r)) == 2


@pytest.mark.asyncio
async def test_pending_request_is_accepted_only_when_replying():
    c, r, client = setup_connector()
    r.thread_cache['100'] = {'is_group':False,'users':{'2':{}},'pending':True}
    client.direct_pending_approve.return_value = True
    client.direct_send.return_value = message(2)
    assert (await c.send_text('test','2','reply'))['message_id'] == '100|2'
    client.direct_pending_approve.assert_called_once_with(100)
    client.direct_thread_by_participants.assert_not_called()
    client.direct_send.assert_called_once_with('reply',thread_ids=[100])


@pytest.mark.asyncio
async def test_checkpoint_waits_for_user_action_instead_of_repeated_login(monkeypatch,tmp_path):
    from instagrapi.exceptions import ChallengeRequired
    c = InstagramPvConnector()
    client = Mock()
    client.login.side_effect = ChallengeRequired('checkpoint')
    client.last_json = {}
    monkeypatch.setattr(c,'_new_client',lambda:client)
    monkeypatch.setattr(c,'_safe_session_dir',lambda params:tmp_path)
    cfg = {'instagram_username':'test','instagram_password':'secret'}
    for _ in range(2):
        with pytest.raises(RuntimeError,match='challenge_required'):
            await c.connect('test',cfg)
    assert client.login.call_count == 1
