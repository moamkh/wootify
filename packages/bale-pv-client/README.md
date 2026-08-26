# Bale PV Connector

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An async-first Python client for [Bale Messenger](https://bale.ai)'s gRPC-Web +
WebSocket API. The API was reverse-engineered from the official web client
(`web.bale.ai`) traffic and is designed to feel similar to popular Telegram
clients.

> **Disclaimer:** This is an unofficial, reverse-engineered client. Bale may
> change their protocol at any time. Use at your own risk and do not share real
> authentication tokens or SMS codes.

## Features

- Async/await API
- Phone-number authentication (`StartPhoneAuth` → `ValidateCode`)
- WebSocket connection with automatic handshake and request/response pairing
- Fetch dialogs, groups/channels, contacts, users and message history
- Send, edit, delete and read messages
- Real-time updates stream (new messages, read receipts, typing, etc.)
- Hand-rolled protobuf wire encoder/decoder (no `.proto` files required)

## Installation

```bash
pip install bale_pv_connector
```

For local development:

```bash
git clone https://github.com/moamkh/bale_pv_connector.git
cd bale_pv_connector
pip install -e ".[dev]"
```

## Quick Start

```python
import asyncio
from bale_pv_connector import BaleClient

async def main():
    client = BaleClient()

    # 1. Request SMS code
    auth_info = await client.start_phone_auth("989123456789")
    print("Transaction hash:", auth_info["transaction_hash"])

    # 2. Enter the code you received
    code = input("SMS code: ")
    await client.validate_code(code)

    # 3. Connect to the real-time WebSocket
    await client.connect()

    # 4. Fetch dialogs
    dialogs = await client.get_dialogs(limit=50)
    for dialog in dialogs["dialogs"]:
        peer = dialog.get("peer")
        print(peer, dialog.get("unread_count"))

    # 5. Listen for updates
    async for update in client.get_updates():
        if update.get("type") == "message":
            print("New message:", update.get("text"), "from", update.get("sender_uid"))

try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
```

## Usage with an existing JWT

If you already have a JWT token, you can skip phone authentication:

```python
client = BaleClient(jwt_token="eyJ...")
await client.connect()
```

## Security Notes

- Never commit real JWT tokens or phone numbers to version control.
- The library logs raw WebSocket frames only when the `BALE_WS_DEBUG_LOG`
  environment variable is set. Keep it unset in production.
- Use a dedicated test phone number while developing.

## Project Structure

```
bale_pv_connector/
├── src/bale_pv_connector/
│   ├── __init__.py          # Public exports
│   ├── client.py            # High-level BaleClient
│   ├── auth_client.py       # Phone auth over gRPC-Web
│   ├── messaging_client.py  # Messaging RPCs over WebSocket
│   ├── groups_client.py     # Group/channel RPCs
│   ├── ws_client.py         # WebSocket transport + framing
│   ├── protobuf_wire.py     # Minimal protobuf encoder/decoder
│   ├── dialog_parser.py     # Response parsers
│   ├── update_parser.py     # Update/event parsers
│   ├── auth_messages.py     # Auth protobuf builders
│   └── messaging_messages.py # Messaging protobuf builders
├── tests/
├── examples/
├── README.md
├── LICENSE
└── pyproject.toml
```

## Testing

```bash
pytest
```

## License

MIT License. See [LICENSE](LICENSE) for details.
