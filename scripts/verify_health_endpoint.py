"""End-to-end verification of the per-instance health endpoint (throwaway)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bale_pv_connector" / "src"))

from fastapi.testclient import TestClient

from app.main import app
from app.connectors.telegram_connector import telegram, TelegramInstanceRuntime, TelegramInstanceConfig
from app.connectors.bale_connector import bale, BaleInstanceRuntime, BaleInstanceConfig
from app.connectors.bale_pv_connector import bale_pv, BalePvInstanceRuntime

failures = []


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    if not cond:
        failures.append(name)


class FakeBot:
    async def get_me(self):
        return {"id": 1}


class FakeWs:
    is_connected = True
    last_frame_at = 1.0


class FakeClient:
    ws = FakeWs()


class FakeHttp:
    async def aclose(self):
        pass


with TestClient(app) as client:
    # 1. Unknown instance -> 404
    r = client.get("/api/v1/instances/does-not-exist/health")
    check("unknown instance returns 404", r.status_code == 404, f"got {r.status_code}")

    # 2. Disabled instance -> 503 'instance is disabled'
    r = client.post("/api/v1/instances", json={
        "instance_key": "health-e2e-disabled",
        "platform_type_key": "telegram",
        "is_enabled": False,
        "platform_metadata": {"telegram_token": "dummy-token"},
    })
    check("create disabled instance", r.status_code in (200, 201), f"got {r.status_code} {r.text[:200]}")
    r = client.get("/api/v1/instances/health-e2e-disabled/health")
    check("disabled instance returns 503", r.status_code == 503, f"got {r.status_code} {r.text[:120]}")

    # 3. Enabled telegram instance, connector not loaded -> 503 instance_not_loaded
    r = client.post("/api/v1/instances", json={
        "instance_key": "health-e2e-tg",
        "platform_type_key": "telegram",
        "is_enabled": True,
        "platform_metadata": {"telegram_token": "dummy-token"},
    })
    check("create telegram instance", r.status_code in (200, 201), f"got {r.status_code} {r.text[:200]}")
    r = client.get("/api/v1/instances/health-e2e-tg/health")
    check("unloaded telegram returns 503", r.status_code == 503, f"got {r.status_code} {r.text[:120]}")

    # 4. Inject a connected telegram runtime -> 200 {"status": "ok"}
    telegram._instances["health-e2e-tg"] = TelegramInstanceRuntime(
        cfg=TelegramInstanceConfig(token="x", api_base_url="x", file_base_url="x", proxy_url=None),
        bot=FakeBot(),
        file_client=FakeHttp(),
    )
    r = client.get("/api/v1/instances/health-e2e-tg/health")
    check("connected telegram returns 200", r.status_code == 200, f"got {r.status_code} {r.text[:120]}")
    check("connected telegram body is {'status':'ok'}", r.json() == {"status": "ok"}, f"got {r.text[:120]}")

    # 5. Broken telegram runtime (get_me raises) -> 503
    class BrokenBot:
        async def get_me(self):
            raise RuntimeError("network down")

    telegram._instances["health-e2e-tg"] = TelegramInstanceRuntime(
        cfg=TelegramInstanceConfig(token="x", api_base_url="x", file_base_url="x", proxy_url=None),
        bot=BrokenBot(),
        file_client=FakeHttp(),
    )
    r = client.get("/api/v1/instances/health-e2e-tg/health")
    check("broken telegram returns 503", r.status_code == 503, f"got {r.status_code} {r.text[:120]}")

    # 6. Enabled bale bot instance with injected runtime -> 200 needs real HTTP probe,
    #    so verify the bale_pv branch over HTTP instead.
    r = client.post("/api/v1/instances", json={
        "instance_key": "health-e2e-pv",
        "platform_type_key": "bale_pv_enterprise",
        "is_enabled": True,
        "platform_metadata": {"bale_pv_phone_number": "989120000000"},
    })
    check("create bale_pv instance", r.status_code in (200, 201), f"got {r.status_code} {r.text[:200]}")

    runtime = BalePvInstanceRuntime(instance_key="health-e2e-pv", phone_number="989120000000")
    runtime.auth_state = "authenticated"
    runtime.client = FakeClient()
    runtime.ws_task = asyncio.get_event_loop().create_future()  # not done => alive
    bale_pv._instances["health-e2e-pv"] = runtime
    r = client.get("/api/v1/instances/health-e2e-pv/health")
    check("connected bale_pv returns 200 + body", r.status_code == 200 and r.json() == {"status": "ok"}, f"got {r.status_code} {r.text[:120]}")

    # 7. bale_pv ws dropped -> 503
    runtime.client = None
    r = client.get("/api/v1/instances/health-e2e-pv/health")
    check("dropped bale_pv returns 503", r.status_code == 503, f"got {r.status_code} {r.text[:120]}")

    # 8. Bale bot connector unit-level: runtime present but API unreachable -> 503 detail
    r = client.post("/api/v1/instances", json={
        "instance_key": "health-e2e-bale",
        "platform_type_key": "bale",
        "is_enabled": True,
        "platform_metadata": {"bale_token": "dummy-token"},
    })
    check("create bale instance", r.status_code in (200, 201), f"got {r.status_code} {r.text[:200]}")
    import httpx as _httpx
    bale._instances["health-e2e-bale"] = BaleInstanceRuntime(
        instance_key="health-e2e-bale",
        cfg=BaleInstanceConfig(token="t", api_base_url="http://127.0.0.1:9", file_base_url="http://127.0.0.1:9", proxy_url=None),
        client=_httpx.AsyncClient(timeout=3),
        file_client=_httpx.AsyncClient(timeout=3),
    )
    r = client.get("/api/v1/instances/health-e2e-bale/health")
    check("unreachable bale bot returns 503", r.status_code == 503, f"got {r.status_code} {r.text[:120]}")

    # Cleanup
    for key in ("health-e2e-disabled", "health-e2e-tg", "health-e2e-pv", "health-e2e-bale"):
        client.delete(f"/api/v1/instances/{key}")
    telegram._instances.pop("health-e2e-tg", None)
    bale._instances.pop("health-e2e-bale", None)
    bale_pv._instances.pop("health-e2e-pv", None)
    print("cleanup done")

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
