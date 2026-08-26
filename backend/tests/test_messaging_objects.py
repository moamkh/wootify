"""Characterization tests for the extracted application messaging objects."""
from __future__ import annotations

from wootify.application.messaging.destination_resolver import DestinationResolver
from wootify.application.messaging.media_normalizer import MediaNormalizer
from wootify.application.messaging.notification_policy import NotificationPolicy
from wootify.application.messaging.payload_parser import ChatwootPayloadParser, MessagePayloadParser
from wootify.application.messaging.workflows import BalePvSyncWorkflow
from wootify.services.bridge_service import BridgeService
from wootify.services.chatwoot_bridge_service import ChatwootBridgeService


def test_message_parser_preserves_chatwoot_shape_fallbacks() -> None:
    payload = {
        "message": {"content": " nested text", "attachments": [{"id": 2}]},
        "processed_message_content": " processed",
        "content_attributes": {"in_reply_to_message_id": 17},
        "conversation": {"meta": {"sender": {"id": "44"}}},
    }
    assert MessagePayloadParser.extract_message_text(payload) == "nested text"
    assert MessagePayloadParser.extract_attachments(payload) == [{"id": 2}]
    assert MessagePayloadParser.extract_parent_message_id(payload) == "17"
    assert MessagePayloadParser.extract_contact_id(payload) == "44"
    assert BridgeService._extract_chatwoot_message_text(payload) == "nested text"


def test_destination_resolver_prefers_expected_platform_prefix() -> None:
    payload = {"conversation": {"contact_inbox": {"source_id": "BALE_PV:928"}}}
    assert DestinationResolver.extract_destination(
        payload, expected_prefix="BALE_PV", known_prefixes={"BALE_PV", "TELEGRAM"}
    ) == ("928", "BALE_PV:928")
    assert BridgeService._choose_destination_chat_id("550e8400-e29b-41d4-a716-446655440000", "928") == "928"


def test_media_normalizer_uses_magic_bytes_and_extensions() -> None:
    assert MediaNormalizer.guess_content_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert MediaNormalizer.for_chatwoot(
        filename="photo", content_type="application/octet-stream", content=b"\x89PNG\r\n\x1a\nrest"
    ) == ("photo.png", "image/png")
    assert BridgeService._normalize_filename_for_platform("voice", "audio/ogg", None) == "voice.ogg"
    assert ChatwootBridgeService._attachment_filename({"filename": "photo"}, "https://x.test/a.jpg") == "photo.jpg"


def test_notification_policy_status_aliases_templates_and_idempotency() -> None:
    assert NotificationPolicy.normalize_status("reopened") == "open"
    assert NotificationPolicy.status_event({"changed_attributes": {"status": ["pending", "resolved"]}}, "conversation_updated")
    policy = NotificationPolicy()
    assert policy.text("open", operator_name="Ada", platform_metadata={"chatwoot_status_message_open_by_operator": "Opened by {operator_name}"}) == "Opened by Ada"
    assert not policy.is_duplicate("i", "c", "open")
    policy.mark("i", "c", "open")
    assert policy.is_duplicate("i", "c", "open")


def test_chatwoot_parser_legacy_and_platform_helpers() -> None:
    payload = {"conversation": {"messages": [{"source_id": "BALE_PV:456", "attachments": [{"id": 1}]}]}}
    assert ChatwootPayloadParser.extract_source_id(payload) == "BALE_PV:456"
    assert ChatwootBridgeService._extract_chatwoot_attachments(payload) == [{"id": 1}]
    assert ChatwootBridgeService._strip_source_prefix("BALE_PV:456") == "456"


def test_bale_sync_workflow_preserves_non_bale_guard() -> None:
    class Service:
        @staticmethod
        def _platform_key(_runtime: object) -> str:
            return "telegram"

    import asyncio

    result = asyncio.run(BalePvSyncWorkflow(Service()).sync_contacts(None, "i", object()))
    assert result == {"ok": False, "detail": "not_bale_pv_instance"}
