# TODO — Webhook authenticity (Chatwoot → wootify)

**Status:** OPEN — needs decision + coordinated rollout
**Severity:** Critical (unauthenticated message injection)
**Found:** bug-bounty round 3 (reports: `security.md` SEC-02, `ctrl-webhooks.md` C1, `main-lifecycle.md` C1)

---

## The problem

The endpoints that receive Chatwoot webhooks accept **any JSON from anyone**:

- `POST /api/v1/webhooks/chatwoot/{instance_key}` — `app/controllers/api_v1_controller.py` (`webhook_chatwoot`)
- `POST /api/v1/webhooks/chatwoot/{instance_key}/enterprise/{route_key}` — same file
- `POST /api/v1/simulate/platform/{instance_key}` — dev/simulation endpoint, also open

There is no signature check, no shared secret, no source-IP restriction. The only "secret" is the `instance_key` in the URL, and those are guessable/enumerable (e.g. `amin`, company names — and the whole API is unauthenticated so `GET /api/v1/instances` lists them).

## What an attacker can do today

From any machine that can reach the port:

1. POST a forged `message_created` webhook with `message_type: outgoing` (an "operator reply") → the bridge **sends a real Bale/Telegram message to a real customer** as if support wrote it.
2. POST forged `conversation_status_changed` events → the bridge sends the configured status texts ("Your chat has been resolved.") to customers.
3. Use `/simulate/platform/{key}` to inject fake *inbound* customer messages into Chatwoot conversations (social-engineering the operators).

Blast radius: customer-facing impersonation of the company, conversation poisoning, phishing customers through a trusted channel.

## Why it wasn't fixed in the bug-bounty rounds

The standard fix puts a secret in the webhook URL. But the Chatwoot server must then be told the new URL for **every inbox** — and we already learned the Chatwoot REST API silently ignores `webhook_url` PATCHes on API-channel inboxes (inbox 38 had to be registered via `rails runner` on the Chatwoot host). Changing the URL format without updating every registered webhook on Chatwoot = **all inbound operator replies silently stop**. So this needs a coordinated change on both sides, in one maintenance window.

## Options

### Option A — secret path token per instance (recommended)

Change the webhook path to include an unguessable per-instance secret:

```
/api/v1/webhooks/chatwoot/{instance_key}/{webhook_secret}
```

- Store `webhook_secret` per instance (random 32+ chars, generated at instance creation, stored in `platform_metadata_encrypted`).
- Controller compares with `hmac.compare_digest`; wrong/missing secret → 404 (not 403, to avoid confirming valid instance keys).
- URL shown once in the UI/API (`GET /instances/{key}` already exposes config — include the full webhook URL there).
- **Rollout cost:** every inbox's webhook on the Chatwoot server must be re-registered with the new URL (scriptable via `rails runner` over the API channels — one command per inbox).
- Works with Chatwoot v4.7.0 as-is (no Chatwoot feature dependency).

### Option B — HMAC signature verification

Verify a signature header on the raw body (`X-Chatwoot-Signature`, HMAC-SHA256 with a shared secret).

- Cleaner cryptographically, body is signed not just the URL.
- **Blocker:** Chatwoot API-channel webhooks historically do **not** sign payloads (HMAC exists for some webhook types in newer versions only). Must be verified against the prod Chatwoot v4.7.0 first — if it doesn't sign, this option is dead unless Chatwoot is patched.

### Option C — network-level only

Firewall the wootify port so only the Chatwoot server (and admin IPs) can reach it.

- Zero code change; pairs naturally with how you're handling API auth (decision 1: managed at network/CORS layer).
- Protects the webhook path too, as long as the firewall rules stay correct.
- Residual risk: anything else ever co-located on an allowed IP (or a misconfigured rule) can still forge; no defense in depth.

### Recommended combination

**A + C**: firewall as the outer layer (you're doing this anyway), secret path tokens as the in-code guarantee so a network mistake doesn't reopen the hole.

## Implementation sketch (Option A)

1. `models.py` / migration: no new column needed — store in `platform_metadata_encrypted` as `webhook_secret` (already encrypted at rest).
2. `instance_service.py`: generate `secrets.token_urlsafe(32)` on instance create (backfill existing instances lazily on first read).
3. `api_v1_controller.py`: new route `/webhooks/chatwoot/{instance_key}/{secret}` (+ enterprise variant); constant-time compare; keep the old route returning 404 (delete it — do NOT keep it working "for compatibility", that defeats the fix).
4. Rollout script: for each Chatwoot API-channel inbox, `rails runner` update `callback_webhook_url` to the new URL (same technique used for inbox 38).
5. Cutover: deploy code → run rollout script → verify one live reply → done.

## Verification after fix

- `curl` the webhook without secret → 404, nothing delivered.
- `curl` with wrong secret → 404.
- Real operator reply via Chatwoot → delivered to Bale (regression check).
- `GET /api/v1/instances` no longer reveals enough to forge (instance_key alone is useless).
