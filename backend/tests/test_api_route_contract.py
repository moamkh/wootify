"""Contract checks for the public v1 route partition.

The expected set is intentionally kept independent of the implementation
router so a route cannot silently disappear while the controller is split.
"""

from fastapi.routing import APIRoute

from wootify.controllers import _api_v1_impl
from wootify.controllers.api_v1_controller import router


EXPECTED_ROUTES = {
    (method, path)
    for method, path in {
        ("GET", "/api/v1/platform-types"),
        ("GET", "/api/v1/features"),
        ("GET", "/api/v1/instances"),
        ("POST", "/api/v1/instances"),
        ("GET", "/api/v1/instances/{instance_key}"),
        ("GET", "/api/v1/instances/{instance_key}/health"),
        ("PATCH", "/api/v1/instances/{instance_key}"),
        ("DELETE", "/api/v1/instances/{instance_key}"),
        ("POST", "/api/v1/instances/{instance_key}/chatwoot/inbox"),
        ("POST", "/api/v1/webhooks/chatwoot/{instance_key}"),
        ("POST", "/api/v1/webhooks/chatwoot/{instance_key}/enterprise/{route_key}"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/auth/send-code"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/auth/validate-code"),
        ("GET", "/api/v1/instances/{instance_key}/bale-pv/auth/status"),
        ("POST", "/api/v1/instances/{instance_key}/instagram-pv/check"),
        ("POST", "/api/v1/instances/{instance_key}/instagram-pv/reconnect"),
        ("POST", "/api/v1/instances/{instance_key}/instagram-pv/challenge/start"),
        ("POST", "/api/v1/instances/{instance_key}/instagram-pv/challenge/validate-code"),
        ("POST", "/api/v1/instances/{instance_key}/instagram-pv/challenge/resume"),
        ("GET", "/api/v1/instances/{instance_key}/bale-pv/contacts"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/sync-contacts"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/sync-dialogs"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/remove-chatwoot-contacts"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/resolve-phone"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/send-by-phone"),
        ("POST", "/api/v1/instances/{instance_key}/bale-pv/debug-load-users"),
        ("GET", "/api/v1/instances/{instance_key}/bale-pv/dialogs"),
        ("POST", "/api/v1/simulate/platform/{instance_key}"),
        ("GET", "/api/v1/instances/{instance_key}/conversations"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/manuals"),
        ("POST", "/api/v1/instances/{instance_key}/enterprise/manuals"),
        ("DELETE", "/api/v1/instances/{instance_key}/enterprise/manuals/{asset_id}"),
        ("PATCH", "/api/v1/instances/{instance_key}/enterprise/manuals/{asset_id}"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/catalog"),
        ("PUT", "/api/v1/instances/{instance_key}/enterprise/catalog"),
        ("PATCH", "/api/v1/instances/{instance_key}/enterprise/catalog"),
        ("DELETE", "/api/v1/instances/{instance_key}/enterprise/catalog"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/manual-groups"),
        ("POST", "/api/v1/instances/{instance_key}/enterprise/manual-groups"),
        ("PUT", "/api/v1/instances/{instance_key}/enterprise/manual-groups/{group_id}"),
        ("DELETE", "/api/v1/instances/{instance_key}/enterprise/manual-groups/{group_id}"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/manual-groups-with-manuals"),
        ("POST", "/api/v1/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}"),
        ("DELETE", "/api/v1/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}"),
        ("POST", "/api/v1/instances/{instance_key}/enterprise/chatwoot/inboxes/{route_key}"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/sessions"),
        ("GET", "/api/v1/instances/{instance_key}/enterprise/sms-sync"),
        ("PATCH", "/api/v1/instances/{instance_key}/enterprise/sms-sync"),
        ("POST", "/api/v1/instances/{instance_key}/enterprise/sms-sync/run"),
        ("GET", "/api/v1/instances/{instance_key}/conversations/{conversation_id}"),
        ("GET", "/api/v1/instances/{instance_key}/conversations/{conversation_id}/messages"),
        ("GET", "/api/v1/version"),
    }
}


def _signature(routes):
    return {(method, route.path) for route in routes for method in (route.methods or ())}


def test_focused_routers_preserve_complete_route_contract():
    """The facade has exactly the baseline route set and declaration order."""

    assert _signature(router.routes) == EXPECTED_ROUTES
    assert [(route.path, route.name) for route in router.routes] == [
        (route.path, route.name) for route in _api_v1_impl.router.routes
    ]
    assert all(isinstance(route, APIRoute) for route in router.routes)
