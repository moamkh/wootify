from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wootify.plugins.eitaa_pv.connector import EitaaPvConnector, EitaaPvRuntime


def _dialogs() -> dict:
    return {
        "dialogs": [{"peer": {"_": "peerUser", "user_id": 42}}],
        "users": [{"id": 42, "access_hash": 99}],
        "chats": [],
    }


@pytest.mark.anyio
async def test_history_edit_is_emitted_once_after_the_initial_baseline() -> None:
    """An Eitaa edit keeps its id, so its edit_date must drive polling."""
    original = {"id": 7, "message": "before", "date": 100}
    edited = {"id": 7, "message": "after", "date": 100, "edit_date": 200}
    client = SimpleNamespace(
        profile=SimpleNamespace(authenticated=True),
        dialogs=SimpleNamespace(list=AsyncMock(return_value=_dialogs())),
        messages=SimpleNamespace(history=AsyncMock(side_effect=[{"messages": [original]}, {"messages": [edited]}, {"messages": [edited]}])),
    )
    connector = EitaaPvConnector()
    connector._runtimes["test"] = EitaaPvRuntime("test", "", Path("session.json"), client)
    connector._set_presence = AsyncMock()  # type: ignore[method-assign]

    assert (await connector.get_updates("test"))["result"] == []

    update = (await connector.get_updates("test"))["result"]
    assert len(update) == 1
    assert update[0]["message"] == edited

    # The same history entry remains visible, but is not sent to Chatwoot a
    # second time when Eitaa has not changed its edit timestamp.
    assert (await connector.get_updates("test"))["result"] == []


@pytest.mark.anyio
async def test_existing_edited_history_is_only_baselined_not_replayed() -> None:
    edited = {"id": 7, "message": "already edited", "date": 100, "edit_date": 200}
    client = SimpleNamespace(
        profile=SimpleNamespace(authenticated=True),
        dialogs=SimpleNamespace(list=AsyncMock(return_value=_dialogs())),
        messages=SimpleNamespace(history=AsyncMock(return_value={"messages": [edited]})),
    )
    connector = EitaaPvConnector()
    connector._runtimes["test"] = EitaaPvRuntime("test", "", Path("session.json"), client)
    connector._set_presence = AsyncMock()  # type: ignore[method-assign]

    assert (await connector.get_updates("test"))["result"] == []
    assert (await connector.get_updates("test"))["result"] == []
