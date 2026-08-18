"""Tests for the persistent inbound event retry queue (BalePollingService)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.bale_polling_service as polling_module
from app.models import Base, InboundEventRetry, Instance
from app.services.bale_polling_service import BalePollingService


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
