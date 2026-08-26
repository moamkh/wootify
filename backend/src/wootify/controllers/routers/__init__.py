"""Focused HTTP routers for the versioned Wootify API.

The endpoint implementations remain in ``_api_v1_impl`` for now.  Each
module in this package owns the route registration for one API area; this
keeps the public behavior stable while allowing the handlers to be migrated
to dedicated application objects incrementally.
"""

from . import bale_pv_instagram, enterprise, instances_platforms, mappings_system, webhooks_simulation

__all__ = [
    "bale_pv_instagram",
    "enterprise",
    "instances_platforms",
    "mappings_system",
    "webhooks_simulation",
]
