# Instagram personal-account DMs

The `instagram_pv_enterprise` plugin uses instagrapi's private mobile API.
The tested dependency is pinned to 2.18.17. Browser authentication and mobile
API authentication are separate sessions; a successful browser login does not
by itself establish the connector session.

## Setup

Create an Instagram PV instance and configure either username/password or an
Instagram session cookie. Configure its proxy when Instagram is unreachable
directly. The polling service passes that proxy through adapter registration
and media downloads. Secrets belong in the panel's encrypted configuration,
not source files or command-line arguments.

Use Check connectivity to verify both the account and DM inbox. If Instagram
requires a checkpoint, use Start challenge, submit the code or approve the
login, then Resume challenge. Reconnect preserves the saved device identity.
Repeated background logins are paused after checkpoint, bad-password and 2FA
errors until credentials change or an explicit reconnect/challenge is started.

Sessions use the project runtime directory, with existing `data` installations
supported. A session file is named after the instance key. Watermark sidecars
are account session state and should be preserved across restarts.

## Delivery behavior

- The first connection establishes a durable time cutoff; older history is
  not imported automatically. New threads and messages after that cutoff are
  delivered, including messages received while the service was offline.
- The normal inbox and message requests are paginated. Individual threads
  expand their message window until the delivery checkpoint is reached.
- Message requests are accepted when an agent replies, not merely when polled.
- Message IDs use `thread_id|item_id` consistently for replies, outgoing
  mapping, unsending and echo deduplication.
- Outbound media is limited to JPG/JPEG, PNG or WebP photos; MP4 video
  (H.264/AAC); and M4A/AAC voice messages. GIFs, documents, MP3/Ogg audio and
  other formats are rejected before a caption can be sent. The SDK's
  `direct_send_file` is a photo/video helper, not an arbitrary document transport.
- Failed downloads or Chatwoot deliveries remain queued with backoff. Their
  watermarks are not committed until delivery; restart can redeliver them.
- Chatwoot deletes unsend the corresponding Instagram message once; duplicate
  Chatwoot callbacks are ignored after the first successful unsend. Editing,
  typing indicators and read-receipt synchronization are not provided by this
  connector. Media captions are separate text messages on Instagram.

## Verification

Run regression tests with the project Python environment:

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_instagram_delivery.py
```

The interactive smoke probe prompts for the password without echoing it:

```powershell
.venv/Scripts/python.exe scripts/instagram_smoke.py --username YOUR_USERNAME --instance YOUR_INSTANCE
```

Use `--proxy http://HOST:PORT` for a proxy and `--challenge` for interactive
checkpoint verification. The probe tests authentication and two inbox polls;
it does not send messages, bridge customer content or acknowledge delivery.

An unreadable encrypted configuration is isolated so it cannot stop every
polling service. The affected instance still needs its original encryption
key restored or its settings re-saved; isolation does not recover lost keys.
