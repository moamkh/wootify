# TODO — SSRF + unbounded fetch in media resolution

**Status:** OPEN — needs decision on allowlist vs blocklist
**Severity:** Critical (SSRF) + High (memory-exhaustion DoS)
**Found:** bug-bounty round 3 (reports: `security.md` SEC-03, `adapters.md` F4)

---

## The problem

**Exact location:** `app/utils/media_utils.py:33-36` (function `resolve_media`)

```python
if raw.startswith("http://") or raw.startswith("https://"):
    resp = await file_client.get(raw, follow_redirects=True)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type")
```

Whenever a message carries an attachment as a URL (`attachment.data_url` in the Chatwoot webhook payload), the **server fetches that URL itself** and forwards the bytes to Bale/Telegram.

Three defects in those four lines:

1. **No host restriction.** The URL can point anywhere — including the internal network the server sits on (`http://192.168.x.x/...`, `http://172.21.x.x/...`), loopback services (`http://127.0.0.1:8500/...`), or cloud metadata endpoints (`http://169.254.169.254/...`). The response body is then uploaded to the messenger chat, which is an **exfiltration channel**: attacker posts a webhook with `data_url: http://internal-host/secret`, and the "attachment" that arrives in the Bale chat contains the internal response. This is textbook SSRF.
2. **`follow_redirects=True` with no re-check.** Even if you allowlist the initial host, a redirect to `http://169.254.169.254/...` is followed blindly. Any fix must validate the URL **after** redirects too (or disable redirects).
3. **No size cap, no timeout, full buffering.** `resp.content` reads the entire body into RAM. A URL pointing at an endless/large stream (or a `data:` URL — the branch right above, `media_utils.py:27-31`, which base64-decodes with no length check either) is a one-request memory-exhaustion DoS. Note `/api/v1/simulate/platform/{key}` accepts `content_base64` with no limit as well (`schemas/api_v1.py`, `SimulatedAttachment`) — and per decision 1 the API stays unauthenticated, so this is reachable by anyone on the network.

**Second SSRF path (different file, same class):** the enterprise SMS-sync config (`ENTERPRISE_SMS_API_URL` / per-instance sync config settable via the API) makes the server send the **SMS provider token** to a configured URL. If an attacker (or a mistake) points that URL at their own host, every sync leaks the token. Whatever fix is chosen for media URLs should also be applied to "configurable outbound URLs that carry credentials".

## Why it wasn't fixed in the bug-bounty rounds

A host allowlist could break a legitimate flow I can't enumerate from the code alone — e.g. if Chatwoot attachment URLs ever point at a different host (object storage, CDN), or if staff paste external image URLs that the bridge re-fetches. Blocking the obvious bad targets is safe, but the "correct" final policy depends on where attachments legitimately live in your deployment.

## Options

### Option A — host allowlist (strictest)

Only fetch from known-good hosts: the Chatwoot base URL host (`tm.novinmed.com`) and Bale/Telegram file domains. Everything else → reject.

- **Pros:** smallest attack surface; redirects can't escape (validate final URL host too).
- **Cons:** any legit attachment source not on the list breaks (silently = failed sends). Needs you to confirm where attachments can come from.

### Option B — network blocklist + hard limits (recommended minimum)

Reject private/loopback/reserved ranges and cap the download:

- Resolve the hostname; refuse if **any** resolved IP is in `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`. (Note: your own Chatwoot is on `192.168.20.76` — a private range — so the blocklist must carry an explicit exception for the configured `CHATWOOT_BASE_URL` host, or media from Chatwoot itself breaks. This is exactly why this needs your eyes.)
- Re-validate after every redirect hop (or `follow_redirects=False` and refuse 3xx).
- Stream with a hard cap (e.g. 25 MB) — abort when exceeded; `Content-Length` pre-check where present but don't trust it.
- Connect/read timeouts (e.g. 10s/30s) instead of client defaults.
- Same treatment for `data:` and `content_base64`: reject payloads over the cap before decoding.

- **Pros:** keeps working with arbitrary external attachment URLs; kills the internal-network and metadata-endpoint attacks and the memory DoS.
- **Cons:** DNS-rebinding and exotic bypasses need the resolve-and-pin done carefully (resolve once, connect to the resolved IP with the Host header / SNI pinned — otherwise a hostname that resolves internal-then-external can race you).

### Option C — both

Allowlist for URL-classified attachments **and** the size/timeout caps from B. Belt and suspenders once you've confirmed the full set of legit sources.

## Recommendation

**B now, upgrade to C** once you've confirmed the complete list of legitimate attachment hosts (likely just the Chatwoot server). Whichever you pick, apply the same guard to the SMS-sync URL config (reject private ranges there too — there is no legitimate reason for the SMS API to be on a private IP from the bridge's perspective... unless there is, which is again your deployment knowledge).

## Implementation sketch (Option B)

1. New helper in `media_utils.py`: `validate_fetch_url(url, allowed_private_hosts)` → resolves DNS (`socket.getaddrinfo`), checks IPs against blocked ranges + exception list, returns the pinned address or raises.
2. `resolve_media`: use the helper, `follow_redirects=False` (refuse 3xx or loop with re-validation, max 3 hops), read via `resp.aiter_bytes()` with running byte count, abort over cap.
3. Config: `MEDIA_FETCH_MAX_BYTES: int = 25 * 1024 * 1024`, `MEDIA_FETCH_TIMEOUT_SECONDS: int = 30`.
4. `schemas/api_v1.py`: `content_base64` / `SimulatedAttachment` get `max_length` (base64 inflates ~4/3, so cap ≈ 34 MB chars for a 25 MB binary cap).
5. SMS-sync config validation: reject private-range hosts on save (controller-level, where the config is patched).

## Verification after fix

- Webhook with `data_url: http://169.254.169.254/latest/meta-data/` → rejected, error logged, no fetch.
- `data_url` pointing at `127.0.0.1:<wootify port>` (self-recursion!) → rejected.
- Legit attachment from the Chatwoot host → still delivered (regression).
- 100 MB URL → aborted at cap, server RAM flat.
- Redirect chain ending at a private IP → rejected.
