"""
Example: authenticate with a phone number, fetch dialogs and listen for updates.

This example expects real credentials interactively; do not hard-code them.
"""

import asyncio
import logging

from bale_pv_connector import BaleClient

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    client = BaleClient()

    phone = input("Phone number (e.g. 989123456789): ").strip()
    auth_info = await client.start_phone_auth(phone)
    print("Code sent. transaction_hash:", auth_info["transaction_hash"])

    code = input("SMS code: ").strip()
    await client.validate_code(code)
    print("Authenticated. JWT:", client.jwt_token[:20] + "...")

    await client.connect()
    print("Connected.")

    dialogs = await client.get_dialogs(limit=20)
    print("Dialogs:", len(dialogs["dialogs"]))

    try:
        async for update in client.get_updates():
            print(update)
    except KeyboardInterrupt:
        pass
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
