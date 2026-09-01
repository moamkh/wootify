"""Unit characterization tests for shared Enterprise policies."""

from types import SimpleNamespace

from wootify.application.enterprise import (
    EnterpriseChatwootPayloadPolicy,
    EnterpriseMenuConfig,
    EnterpriseRoutePolicy,
    EnterpriseSessionTransitionPolicy,
)
from wootify.services.enterprise_bale_service import EnterpriseBaleService
from wootify.services.enterprise_telegram_service import EnterpriseTelegramService


class _Repository:
    def __init__(self, session=None):
        self.session = session
        self.saved = []

    def get_unresolved_for_user_route(self, user_id, route_key):
        self.lookup = (user_id, route_key)
        return self.session

    def save(self, row):
        self.saved.append(row)


def test_static_bale_routes_keep_metadata_key_mapping():
    policy = EnterpriseRoutePolicy(
        static_routes={
            "sales": {
                "state": "live_sales",
                "waiting_text_key": "sales_waiting",
            }
        },
        text_key_map={"waiting": "waiting_text_key"},
    )

    assert policy.require({}, "sales")["state"] == "live_sales"
    assert policy.text({"sales_waiting": "  waiting  "}, "sales", "waiting") == "waiting"
    assert policy.text({}, "sales", "waiting") is None


def test_service_route_delegates_preserve_platform_route_shapes():
    bale_metadata = {"enterprise_customer_service_waiting_text": "  wait  "}
    assert EnterpriseBaleService._route_text(
        bale_metadata, "customer_service", "waiting"
    ) == "wait"
    assert EnterpriseBaleService._require_route("sales")["state"].value == "live_sales"

    telegram_metadata = {
        "enterprise_routes": [{"route_key": "sales", "waiting_text": "wait"}]
    }
    telegram_service = EnterpriseTelegramService.__new__(EnterpriseTelegramService)
    assert telegram_service._route_text(
        telegram_metadata, "sales", "waiting"
    ) == "wait"


def test_dynamic_telegram_routes_match_display_name_and_text():
    policy = EnterpriseRoutePolicy()
    metadata = {
        "enterprise_routes": [
            {"route_key": "sales", "display_name": " فروش ", "waiting_text": "wait"}
        ]
    }

    route = policy.match_display_name(metadata, "فروش")
    assert route["route_key"] == "sales"
    assert policy.text(metadata, "sales", "waiting") == "wait"


def test_menu_config_uses_trimmed_override_and_fallback():
    config = EnterpriseMenuConfig()
    assert config.label({"button": "  custom  "}, "button", "default") == "custom"
    assert config.message({}, "missing", "default") == "default"
    assert config.not_configured({"enterprise_not_configured_text": " آماده "}, "fallback") == "آماده"


def test_chatwoot_payload_policy_preserves_extraction_precedence_and_classification():
    payload = {
        "conversation": {"id": " 42 "},
        "message": {
            "id": "nested-id",
            "content": " nested text ",
            "attachments": [{"id": 1}, "invalid"],
            "message_type": 3,
        },
        "content": " direct text ",
        "message_type": "outgoing",
    }

    assert EnterpriseChatwootPayloadPolicy.extract_conversation_id(payload) == "42"
    assert EnterpriseChatwootPayloadPolicy.extract_message_id(payload) == "nested-id"
    assert EnterpriseChatwootPayloadPolicy.extract_message_text(payload) == "direct text"
    assert EnterpriseChatwootPayloadPolicy.extract_attachments(payload) == [{"id": 1}]
    assert EnterpriseChatwootPayloadPolicy.is_forwardable(payload, "message_created")
    assert EnterpriseChatwootPayloadPolicy.normalize_content_type(
        filename="document.unknown", content_type="application/octet-stream", content=b"%PDF-1.7"
    ) == "application/pdf"


def test_session_policy_preserves_platform_state_and_closes_session():
    session = SimpleNamespace(user_present=True, status="open")
    repository = _Repository(session)
    user = SimpleNamespace(id=7, current_state="live_sales")
    policy = EnterpriseSessionTransitionPolicy(
        non_live_states=frozenset({"root"}),
        closed_status="closed_by_user",
    )

    assert policy.is_live_state("live_sales")
    assert policy.active_session(lambda _db: repository, object(), user) is session
    assert repository.lookup == (7, "live_sales")
    policy.close_session(lambda _db: repository, object(), session)
    assert (session.user_present, session.status) == (False, "closed_by_user")
    policy.mark_user_present(lambda _db: repository, object(), session)
    assert session.user_present is True
    policy.set_user_state(lambda _db: repository, object(), user, "manual_menu")
    assert user.current_state == "manual_menu"
