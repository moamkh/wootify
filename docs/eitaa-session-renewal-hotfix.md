# Eitaa session renewal hotfix — 2026-10-11

This hotfix follows the v7.1.1 release; it does not move or replace that tag.

## Root cause

Eitaa's `eitaa_updates_expire_token` and `eitaa_token_updating` responses are
recoverable token-lifecycle notifications. Eitaa Web pauses requests, invokes
`eitaaRefreshToken` using the existing token and device identity, stores the
`eitaa_updates_token` response, and resumes interrupted RPCs. Wootify previously
latched these notifications as permanent `auth_expired`/`login_required` states.
The installed eitaa-cli schema supported renewal, but its client did not implement
automatic refresh.

Reference: https://web.eitaa.com/mtproto.worker-Zk-UnVZ2.js

## Implementation

- `plugins/eitaa_pv/session_client.py`: a session-aware EitaaClient subclass.
  Every normal RPC, including media requests, handles expiration and token updates.
  `401 INVALID_LOGIN` on an ordinary RPC attempts renewal first.
- Renewal is single-flight per instance/client. Concurrent expired requests share
  the same task; stale failures cannot overwrite a newer token. A cancelled caller
  does not cancel renewal or atomic persistence.
- Persist the fresh token with the existing profile/IMEI/user before resuming
  traffic. Keep a pending replacement in memory if storage temporarily fails,
  retrying persistence rather than rotating again.
- Network, server, malformed-response, and repeated token-updating failures use
  bounded retries/backoff rather than forcing OTP. A definitively rejected renewal
  or revoked session requires phone login; the saved session file is not deleted.
- Replay original method parameters and original media transport kind. The same
  outgoing random ID is retained. Ambiguous business-RPC transport failures are
  not automatically replayed.
- Connector wiring uses this client and reports `token_refresh_pending` separately
  from `login_required`. Explicit successful OTP resets recovery state; unsuccessful
  sign-in responses do not pretend to authenticate the connector.
- Remove the old permanent-expiry latch. Cursor, queued events, routes, and message
  mappings are not reset during renewal.

## Verification

24 dedicated renewal tests cover actual wire encoding/decoding, concurrent
rotation, restarts, cancellation, durable persistence, transient failures, revoked
sessions, explicit OTP, media request replay, and preservation of update cursors.
The full backend/Bale client suite passed (420 tests before the final removal of
the unused latch field).

A production probe using the saved session successfully renewed its token and
returned `updates.state` without a phone code. No customer message was sent by
this diagnostic probe. Never copy tokens or raw session documents into logs/docs.

## Rollout

Backend-only patch; no frontend rebuild, dependency upgrade, new configuration,
or schema migration. Keep a private backup of source/configuration/database and
the pre-renewal session before rollout. The database delivery cursor remains the
source of truth; this fix does not automatically import historical gaps.

The deployment currently uses one application process per server. Independent
processes must not share the same Eitaa session file without additional
cross-process coordination. Genuine server-side session revocation still requires
a new code; automatic renewal cannot guarantee immunity from revocation.
