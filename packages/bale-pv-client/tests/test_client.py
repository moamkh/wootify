"""Tests for the high-level BaleClient."""

import pytest

from bale_pv_connector import BaleClient
from bale_pv_connector.exceptions import BaleAuthError, BaleConnectionError


def test_client_requires_auth_before_connect() -> None:
    client = BaleClient()
    with pytest.raises(BaleConnectionError):
        # cannot connect before JWT is set
        import asyncio
        asyncio.run(client.connect())


def test_validate_code_without_transaction_hash_raises() -> None:
    client = BaleClient()
    with pytest.raises(BaleAuthError):
        import asyncio
        asyncio.run(client.validate_code("123456"))


def test_jwt_token_property_when_initialized() -> None:
    client = BaleClient(jwt_token="eyJ...")
    assert client.jwt_token == "eyJ..."


@pytest.mark.asyncio
async def test_get_dialogs_raises_when_not_authenticated(monkeypatch) -> None:
    client = BaleClient()
    with pytest.raises(BaleConnectionError):
        await client.get_dialogs()
