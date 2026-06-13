"""
Dump Bale PV dialogs to JSON for inspection.

Usage:
    python dump_bale_dialogs.py <instance_key> [--history]

Example:
    python dump_bale_dialogs.py pv-test
    python dump_bale_dialogs.py pv-test --history --limit 20
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.db import SessionLocal
from app.services.instance_service import InstanceService
from app.connectors.bale_pv_connector import bale_pv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("dump_bale_dialogs")


def json_default(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dump Bale PV dialogs to JSON")
    parser.add_argument("instance_key", help="Instance key (e.g. pv-test)")
    parser.add_argument("--history", action="store_true", help="Also load message history")
    parser.add_argument("--limit", type=int, default=200, help="Dialog limit (default 200)")
    parser.add_argument("--history-limit", type=int, default=20, help="History messages per dialog (default 20)")
    parser.add_argument("--output", "-o", type=str, default="bale_dialogs_dump.json", help="Output JSON file")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = InstanceService()
        runtime = service.get_runtime_instance(db, args.instance_key)
        if not runtime:
            logger.error("Instance not found: %s", args.instance_key)
            sys.exit(1)

        logger.info("Connecting to Bale instance=%s phone=%s", args.instance_key, runtime.platform_metadata.get("bale_pv_phone_number"))
        await bale_pv.connect(args.instance_key, runtime.platform_metadata)

        logger.info("Loading dialogs...")
        result = await bale_pv.sync_bale_dialogs(
            args.instance_key,
            limit=args.limit,
            load_history=args.history,
            history_limit=args.history_limit,
        )

        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=json_default, ensure_ascii=False)

        logger.info("Dumped to %s", output_path.resolve())
        if result.get("ok"):
            logger.info(
                "dialogs=%s users=%s groups=%s",
                len(result.get("dialogs", [])),
                len(result.get("users_by_id", {})),
                len(result.get("groups_by_id", {})),
            )
            if args.history:
                logger.info("history peers=%s", len(result.get("history_by_peer", {})))
        else:
            logger.error("Failed: %s", result.get("description"))
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
