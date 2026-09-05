"""Regression tests for account-wide Chatwoot webhook routing."""

from types import SimpleNamespace

from wootify.presentation.http.routers._handler_controller import (
    _instance_matches_chatwoot_inbox,
)


def test_runtime_matches_only_its_configured_chatwoot_inbox():
    """An account webhook event must not be delivered to another PV instance."""
    runtime = SimpleNamespace(
        chatwoot={"inbox_id": 48, "inbox_name": "Support Bale PV"},
        platform_metadata={},
    )

    assert _instance_matches_chatwoot_inbox(
        runtime, inbox_id="48", inbox_name="Support Bale PV"
    )
    assert not _instance_matches_chatwoot_inbox(
        runtime, inbox_id="44", inbox_name="Mina Bale PV"
    )
