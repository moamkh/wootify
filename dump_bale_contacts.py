import argparse
import asyncio
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from app.db import SessionLocal
from app.services.instance_service import InstanceService
from app.connectors.bale_pv_connector import bale_pv


def json_default(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_key", default="pv-test", nargs="?")
    parser.add_argument("--output", "-o", default="bale_contacts_dump.json")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = InstanceService()
        runtime = service.get_runtime_instance(db, args.instance_key)
        if not runtime:
            print("Instance not found")
            return
        await bale_pv.connect(args.instance_key, runtime.platform_metadata)
        contacts = await bale_pv.get_contacts(args.instance_key)
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(contacts, f, indent=2, ensure_ascii=False, default=json_default)
        print(f"Dumped {len(contacts.get('contacts', []))} contacts to {output_path.resolve()}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
