"""Tests for the persistent inbound event retry queue (BalePollingService)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import wootify.services.bale_polling_service as polling_module
from wootify.models import Base, InboundEventRetry, Instance
from wootify.services.bale_polling_service import BalePollingService


@pytest.fixture()
def session_factory():
    """Shared in-memory SQLite (StaticPool) so multiple sessions see one DB."""
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    yield factory
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


def _service(monkeypatch, session_factory, ingest_side_effect=None):
    """Build a polling service wired to the test DB with stubbed delivery."""
    monkeypatch.setattr(polling_module, 'SessionLocal', session_factory)
    service = BalePollingService()
    service._instances = SimpleNamespace(
        get_runtime_instance=lambda db, key: SimpleNamespace(
            instance=SimpleNamespace(is_enabled=True)
        )
    )

    async def _fake_normalize(instance_key, platform_key, update, *, connector):
        return {'chat_id': '1', 'update_id': update.get('update_id')}

    calls = []

    async def _fake_ingest(db, instance_key, event):
        calls.append(event)
        if ingest_side_effect is not None:
            raise ingest_side_effect
        return {'ok': True}

    service._platform_update_to_event = _fake_normalize
    service._bridge = SimpleNamespace(ingest_platform_event=_fake_ingest)
    return service, calls


def _queued_rows(session_factory):
    with session_factory() as db:
        return [
            {
                'instance_key': r.instance_key,
                'platform_key': r.platform_key,
                'update_id': r.update_id,
                'payload': r.payload_json,
                'attempts': r.attempts,
                'next_attempt_at': r.next_attempt_at,
                'last_error': r.last_error,
            }
            for r in db.query(InboundEventRetry).order_by(InboundEventRetry.created_at, InboundEventRetry.id).all()
        ]


def test_enqueue_after_refetch_budget_exhausted(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    update = {'update_id': 42, 'message': {'text': 'hi'}}

    assert service._handle_update_failure('inst-1', 'bale', update, '42', RuntimeError('down')) is False
    assert service._handle_update_failure('inst-1', 'bale', update, '42', RuntimeError('down')) is False
    # Third failure exhausts the refetch budget: offset advances, update persisted.
    assert service._handle_update_failure('inst-1', 'bale', update, '42', RuntimeError('down')) is True

    rows = _queued_rows(session_factory)
    assert len(rows) == 1
    assert rows[0]['instance_key'] == 'inst-1'
    assert rows[0]['platform_key'] == 'bale'
    assert rows[0]['update_id'] == '42'
    assert rows[0]['payload']['message']['text'] == 'hi'
    assert rows[0]['attempts'] == 0
    assert 'down' in rows[0]['last_error']


def test_update_without_id_is_queued_immediately(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)

    # No update id => cannot be refetched via offset; queued on first failure.
    assert service._handle_update_failure('inst-1', 'bale', {'foo': 'bar'}, None, RuntimeError('x')) is True

    rows = _queued_rows(session_factory)
    assert len(rows) == 1
    assert rows[0]['update_id'] is None
    assert rows[0]['payload'] == {'foo': 'bar'}


def test_enqueue_dedupes_same_update(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    exc = RuntimeError('boom')

    service._enqueue_failed_update('inst-1', 'bale', {'update_id': 7}, '7', exc)
    service._enqueue_failed_update('inst-1', 'bale', {'update_id': 7}, '7', exc)
    service._enqueue_failed_update('inst-1', 'bale', {'update_id': 8}, '8', exc)

    rows = _queued_rows(session_factory)
    assert sorted(r['update_id'] for r in rows) == ['7', '8']


def test_drain_delivers_and_deletes_row(monkeypatch, session_factory):
    service, calls = _service(monkeypatch, session_factory)
    with session_factory() as db:
        _make_instance(db)
    service._enqueue_failed_update('inst-1', 'bale', {'update_id': 7}, '7', RuntimeError('boom'))

    asyncio.run(service._drain_retry_queue())

    assert calls == [{'chat_id': '1', 'update_id': 7}]
    assert _queued_rows(session_factory) == []


def test_drain_failure_backs_off_and_preserves_order(monkeypatch, session_factory):
    service, calls = _service(
        monkeypatch, session_factory, ingest_side_effect=RuntimeError('chatwoot down')
    )
    with session_factory() as db:
        _make_instance(db)
        base = datetime.now(timezone.utc).replace(tzinfo=None)
        # Explicit created_at values: SQLite func.now() has second precision,
        # so defaults alone would not guarantee creation order in tests.
        db.add_all([
            InboundEventRetry(
                instance_key='inst-1', platform_key='bale', update_id='1',
                payload_json={'update_id': 1}, attempts=0,
                created_at=base, next_attempt_at=base,
            ),
            InboundEventRetry(
                instance_key='inst-1', platform_key='bale', update_id='2',
                payload_json={'update_id': 2}, attempts=0,
                created_at=base + timedelta(seconds=1),
                next_attempt_at=base + timedelta(seconds=1),
            ),
        ])
        db.commit()

    asyncio.run(service._drain_retry_queue())

    # Only the first row was attempted (head-of-line blocking); it backed off.
    assert calls == [{'chat_id': '1', 'update_id': 1}]
    rows = _queued_rows(session_factory)
    assert len(rows) == 2
    first = next(r for r in rows if r['update_id'] == '1')
    second = next(r for r in rows if r['update_id'] == '2')
    assert first['attempts'] == 1
    assert 'chatwoot down' in first['last_error']
    assert first['next_attempt_at'] > datetime.now(timezone.utc).replace(tzinfo=None)
    assert second['attempts'] == 0

    # While the first row backs off, the second row must not overtake it.
    asyncio.run(service._drain_retry_queue())
    assert calls == [{'chat_id': '1', 'update_id': 1}]
    rows = _queued_rows(session_factory)
    assert next(r for r in rows if r['update_id'] == '2')['attempts'] == 0


def test_drain_purges_rows_of_deleted_instances(monkeypatch, session_factory):
    service, calls = _service(monkeypatch, session_factory)
    # No Instance row for 'ghost': the queue row must be purged, not retried.
    service._enqueue_failed_update('ghost', 'bale', {'update_id': 9}, '9', RuntimeError('boom'))

    asyncio.run(service._drain_retry_queue())

    assert calls == []
    assert _queued_rows(session_factory) == []


def test_bale_updates_are_persisted_before_delivery_and_deduped(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    update = {'update_id': 42, 'message': {'message_id': '42', 'chat': {'id': 7}, 'text': 'hello'}}
    edited = {'update_id': 42, 'message': {'message_id': '42', 'chat': {'id': 7},
                                          'text': 'hello again', '_edited': True}}
    deleted = {'update_id': 42, 'message': {'message_id': '42', 'chat': {'id': 7},
                                           '_deleted': True}}

    service._persist_bale_updates('inst-1', 'bale_pv_enterprise', [update, update, edited, deleted])

    rows = _queued_rows(session_factory)
    assert len(rows) == 3
    assert rows[0]['update_id'] == '42'
    assert rows[1]['update_id'].startswith('42:edit:')
    assert rows[2]['update_id'] == '42:delete'
    assert all(row['attempts'] == 0 for row in rows)


def test_bale_queue_retries_chatwoot_failure_until_recovery(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    with session_factory() as db:
        _make_instance(db)
    calls = []
    available = {'value': False}

    async def normalize(instance_key, update):
        return {'chat_id': '7', 'platform_message_id': str(update['update_id'])}

    async def ingest(db, instance_key, event):
        calls.append(event)
        if not available['value']:
            raise ConnectionError('Chatwoot DNS unavailable')
        return {'ok': True}

    service._normalize_with_adapter = normalize
    monkeypatch.setattr(polling_module, 'chatwoot_bridge', SimpleNamespace(ingest_platform_event=ingest))
    service._persist_bale_updates(
        'inst-1', 'bale_pv_enterprise',
        [{'update_id': 42, 'message': {'message_id': '42', 'chat': {'id': 7}, 'text': 'hello'}}],
    )

    assert calls == []  # Persistence must not require Chatwoot to be online.
    asyncio.run(service._drain_retry_queue())
    rows = _queued_rows(session_factory)
    assert len(rows) == 1
    assert rows[0]['attempts'] == 1
    assert 'Chatwoot DNS unavailable' in rows[0]['last_error']

    available['value'] = True
    with session_factory() as db:
        row = db.query(InboundEventRetry).one()
        row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    asyncio.run(service._drain_retry_queue())

    assert len(calls) == 2
    assert _queued_rows(session_factory) == []


def test_bale_failure_blocks_only_its_own_peer(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    with session_factory() as db:
        _make_instance(db)
    calls = []

    async def normalize(instance_key, update):
        return {'chat_id': str(update['message']['chat']['id'])}

    async def ingest(db, instance_key, event):
        calls.append(event['chat_id'])
        if event['chat_id'] == '7':
            raise ConnectionError('peer 7 failed')
        return {'ok': True}

    service._normalize_with_adapter = normalize
    monkeypatch.setattr(polling_module, 'chatwoot_bridge', SimpleNamespace(ingest_platform_event=ingest))
    service._persist_bale_updates('inst-1', 'bale_pv_enterprise', [
        {'update_id': 1, 'message': {'chat': {'id': 7}}},
        {'update_id': 2, 'message': {'chat': {'id': 7}}},
        {'update_id': 3, 'message': {'chat': {'id': 8}}},
    ])

    asyncio.run(service._drain_retry_queue())

    assert calls == ['7', '8']
    assert [row['update_id'] for row in _queued_rows(session_factory)] == ['1', '2']


def test_bale_queue_retains_non_exception_delivery_failure(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    with session_factory() as db:
        _make_instance(db)

    async def normalize(instance_key, update):
        return {'chat_id': '7'}

    async def ingest(db, instance_key, event):
        return {'ok': False, 'detail': 'chatwoot_not_enabled'}

    service._normalize_with_adapter = normalize
    monkeypatch.setattr(polling_module, 'chatwoot_bridge', SimpleNamespace(ingest_platform_event=ingest))
    service._persist_bale_updates(
        'inst-1', 'bale_pv_enterprise',
        [{'update_id': 42, 'message': {'chat': {'id': 7}}}],
    )

    asyncio.run(service._drain_retry_queue())

    rows = _queued_rows(session_factory)
    assert len(rows) == 1
    assert rows[0]['attempts'] == 1
    assert 'chatwoot_not_enabled' in rows[0]['last_error']


def test_bale_poller_routes_websocket_update_only_to_durable_queue(monkeypatch, session_factory):
    service, _ = _service(monkeypatch, session_factory)
    runtime = SimpleNamespace(
        instance=SimpleNamespace(id='instance-1', is_enabled=True),
        platform_type=SimpleNamespace(key='bale_pv_enterprise'),
        platform_metadata={}, proxy=None,
        runtime_state_last_update_id=None,
        runtime_state_last_sms_sync_at=None,
    )
    service._instances.get_runtime_instance = lambda db, key: runtime
    calls = []

    class Connector:
        async def connect(self, instance_key, config, proxy):
            return None

        async def get_updates(self, instance_key, *, offset, timeout):
            calls.append('polled')
            service._stop.set()
            return {'ok': True, 'result': [
                {'update_id': 42, 'message': {'chat': {'id': 7}, 'text': 'hello'}}
            ]}

    async def connect_instance(instance_key, platform_key, config):
        return None

    async def update_state(*args, **kwargs):
        return True

    async def forbidden_ingest(*args, **kwargs):
        pytest.fail('Chatwoot delivery must be owned by the queue worker')

    monkeypatch.setattr(polling_module.connector_registry, 'get', lambda key: Connector())
    monkeypatch.setattr(polling_module.runtime_registry, 'connect_instance', connect_instance)
    monkeypatch.setattr(polling_module, 'chatwoot_bridge', SimpleNamespace(ingest_platform_event=forbidden_ingest))
    service._update_runtime_state_with_retry = update_state

    asyncio.run(service._run_instance('inst-1'))

    assert calls == ['polled']
    assert [row['update_id'] for row in _queued_rows(session_factory)] == ['42']
