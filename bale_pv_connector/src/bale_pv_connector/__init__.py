"""
Bale PV Connector
=================
An async-first Python client for Bale Messenger's gRPC-Web + WebSocket API.

The package is built by reverse-engineering the official web client
(``web.bale.ai``) traffic. It provides a clean, Telegram-like API for
authentication, fetching dialogs/groups/channels, sending messages and
receiving real-time updates.

Quick start::

    import asyncio
    from bale_pv_connector import BaleClient

    async def main():
        client = BaleClient()
        await client.start_phone_auth("989123456789")
        code = input("SMS code: ")
        await client.validate_code(code)
        await client.connect()

        dialogs = await client.get_dialogs()
        print(dialogs["dialogs"])

        async for update in client.get_updates():
            print(update)

    asyncio.run(main())
"""

__version__ = "0.2.5"

from .auth_client import BaleAuthClient
from .client import AuthResult, BaleClient
from .exceptions import (
    BaleAuthError,
    BaleConnectionError,
    BaleNotImplementedError,
    BaleRpcError,
)
from .groups_client import BaleGroupsClient
from .messaging_client import BaleMessagingClient
from .update_parser import BaleUpdateType, parse_ws_update

__all__ = [
    "AuthResult",
    "BaleAuthClient",
    "BaleAuthError",
    "BaleClient",
    "BaleConnectionError",
    "BaleGroupsClient",
    "BaleMessagingClient",
    "BaleNotImplementedError",
    "BaleRpcError",
    "BaleUpdateType",
    "parse_ws_update",
]
