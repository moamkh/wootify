"""Interactive, credential-safe Instagram authentication and polling probe.

Run with the project Python environment. No messages are sent or bridged.
The session is reusable by a Wootify instance with the same instance key.
"""
import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from wootify.plugins.instagram.connector import InstagramPvConnector


async def run(args):
    logging.disable(logging.CRITICAL)
    connector = InstagramPvConnector()
    clients = []
    factory = connector._new_client
    def capture_client():
        client = factory()
        clients.append(client)
        return client
    connector._new_client = capture_client
    config = {"instagram_username": args.username, "instagram_poll_interval": 10}
    if args.session_cookie:
        config['instagram_sessionid'] = getpass.getpass('Instagram sessionid cookie: ')
    else:
        config['instagram_password'] = getpass.getpass('Instagram password: ')
    if args.proxy:
        url = urlsplit(args.proxy)
        config['proxy'] = {'enabled':True, 'protocol':url.scheme, 'host':url.hostname, 'port':url.port}
    print("Authenticating...", flush=True)
    try:
        if args.challenge:
            state = await connector.start_challenge(args.instance, config)
            runtime = connector._get_runtime(args.instance)
            for _ in range(180):
                if runtime.challenge_state not in ('sending','resolving'):
                    break
                await asyncio.sleep(1)
            print('Challenge state:', runtime.challenge_state, flush=True)
            if runtime.challenge_state == 'code_sent':
                code = await asyncio.to_thread(getpass.getpass, 'Instagram security code: ')
                await connector.submit_challenge_code(args.instance, code)
            elif runtime.challenge_state == 'manual_approval':
                await asyncio.to_thread(input, 'Approve the login in Instagram, then press Enter: ')
                await connector.resume_challenge(args.instance)
            elif runtime.challenge_state == 'failed':
                print('Challenge failed:', runtime.challenge_detail[:200], flush=True)
                return False
        else:
            await connector.connect(args.instance, config)
        health = await connector.check_connectivity(args.instance, config)
        print("Authenticated:", health["connected"], flush=True)
        if not health["connected"]:
            print('State:', health.get('detail','unknown').split(':',1)[0], flush=True)
            return False
        for i in range(2):
            updates = await connector.get_updates(args.instance, timeout=0)
            runtime = connector._get_runtime(args.instance)
            print("Poll", i + 1, "ok:", updates.get("ok"), "threads:",
                  len(runtime.thread_cache), "new events:", len(updates.get("result", [])), flush=True)
            if not updates.get('ok'):
                return False
        # Do not acknowledge updates: this probe has not delivered them.
        return True
    except Exception as exc:
        print("Authentication/poll failure:", type(exc).__name__, flush=True)
        state = connector.get_auth_state(args.instance)
        print("State:", state.get("detail", "unknown").split(":", 1)[0], flush=True)
        if args.show_challenge and clients:
            challenge = (clients[-1].last_json or {}).get('challenge') or {}
            print('Checkpoint URL:', challenge.get('url'), flush=True)
            print('Checkpoint API path:', challenge.get('api_path'), flush=True)
        return False
    finally:
        await connector.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--proxy", help="Proxy URL without credentials")
    parser.add_argument("--challenge", action='store_true')
    parser.add_argument("--show-challenge", action='store_true')
    parser.add_argument("--session-cookie", action='store_true', help='Prompt for a browser sessionid instead of a password')
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
