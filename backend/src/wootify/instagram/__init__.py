"""Instagram PV (personal-account Direct Messages) platform package.

Self-contained Instagram integration for the Wootify connector, kept separate
from the Bale/Telegram code so it can evolve (or be removed) independently:

* ``connector.py``        — ``InstagramPvConnector``: instagrapi session
  management, outbound DM sends, simulated long-poll inbox fetching with
  durable per-thread watermarks, media downloads, auth/health state.
* ``adapter.py``          — ``InstagramPvAdapter``: normalizes connector
  updates into the event shape consumed by ``ChatwootBridgeService``.
* ``polling_service.py``  — ``InstagramPollingService``: per-instance poll
  loop supervision, adapter-runtime registration, delivery failure handling
  with an in-memory per-instance retry queue, runtime-state persistence.

Registered under the platform key ``instagram_pv_enterprise``.
"""

from wootify.plugins.instagram.connector import InstagramPvConnector, instagram_pv

__all__ = ["InstagramPvConnector", "instagram_pv"]
