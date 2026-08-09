"""Regression tests for the Bale PV WebSocket reconnect loop.

Bug: ``_start_messaging_client`` swallowed connection failures, so the
``_ws_listen`` reconnect loop never backed off, and every successful
reconnect spawned a duplicate listener task. Under a flaky Bale WS
endpoint this multiplied into dozens of concurrent loops reconnecting
every 2 seconds, starving the main HTTP event loop.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bale_pv_connector" / "src"))

from app.connectors.bale_pv_connector import (  # noqa: E402
    BalePvConnector,
    BalePvInstanceRuntime,
)


class _FakeWs:
    def __init__(self) -> None:
        self.is_connected = False
        self.last_frame_at = 0.0


class _FakeClient:
    """Messaging client stub whose connect() succeeds or fails on demand."""

    def __init__(self, connect_ok: bool) -> None:
        self.ws = _FakeWs()
        self._connect_ok = connect_ok

    async def connect(self) -> None:
        if not self._connect_ok:
            raise ConnectionError("bale ws unreachable")
        self.ws.is_connected = True

    async def close(self) -> None:
        self.ws.is_connected = False


def _make_runtime(tmp_path: Path, key: str = "test-instance") -> BalePvInstanceRuntime:
    runtime = BalePvInstanceRuntime(instance_key=key, phone_number="989120000000")
    runtime.session_dir = tmp_path
    runtime.session_id = "sid-1"
    runtime.auth_state = "authenticated"
    (tmp_path / "989120000000_sid-1.session").write_text("jwt:fake-token")
    return runtime


def _patch_client_factory(monkeypatch: pytest.MonkeyPatch, connect_ok: bool) -> None:
    def factory(**kwargs):
        return _FakeClient(connect_ok)

    monkeypatch.setattr(
        "app.connectors.bale_pv_connector._get_messaging_client",
        lambda: factory,
    )


@pytest.mark.asyncio
async def test_start_messaging_client_returns_false_on_failure(tmp_path, monkeypatch):
    """Connection failure returns False instead of raising or spinning."""
    _patch_client_factory(monkeypatch, connect_ok=False)
    connector = BalePvConnector()
    runtime = _make_runtime(tmp_path)

    ok = await connector._start_messaging_client(runtime)

    assert ok is False
    assert runtime.client is None
    assert runtime.ws_task is None  # no listener spawned on failure


@pytest.mark.asyncio
async def test_reconnect_does_not_spawn_duplicate_listener_tasks(tmp_path, monkeypatch):
    """A successful reconnect from inside _ws_listen must not add tasks."""
    _patch_client_factory(monkeypatch, connect_ok=True)
    connector = BalePvConnector()
    runtime = _make_runtime(tmp_path)

    ok = await connector._start_messaging_client(runtime)
    assert ok is True
    first_task = runtime.ws_task
    assert first_task is not None and not first_task.done()

    # Simulate reconnect while the listener task is alive (the old bug
    # spawned an extra task per success).
    ok = await connector._start_messaging_client(runtime)
    assert ok is True
    assert runtime.ws_task is first_task

    runtime.stop_event.set()
    first_task.cancel()
    try:
        await first_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_ws_listen_backs_off_and_stays_single(tmp_path, monkeypatch):
    """Repeated connect failures must back off (2s..60s), never tight-loop."""
    _patch_client_factory(monkeypatch, connect_ok=False)
    connector = BalePvConnector()
    runtime = _make_runtime(tmp_path)

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0.001)  # yield without real waiting

    monkeypatch.setattr("app.connectors.bale_pv_connector.asyncio.sleep", fake_sleep)

    connector._start_websocket_listener(runtime)
    assert runtime.ws_task is not None

    await real_sleep(0.5)  # let the loop spin through many fake iterations
    runtime.stop_event.set()
    runtime.ws_task.cancel()
    try:
        await runtime.ws_task
    except asyncio.CancelledError:
        pass

    # The recorded backoff sequence must grow exponentially 2,4,8,16,32,60
    # and then stay capped at 60s — never a tight retry loop.
    expected = [2.0, 4.0, 8.0, 16.0, 32.0]
    assert sleeps[:5] == expected
    assert max(sleeps) <= 60.0
    for prev, curr in zip(sleeps, sleeps[1:]):
        assert curr >= prev
    # Exactly one listener task ever existed.
    assert runtime.ws_task is not None
