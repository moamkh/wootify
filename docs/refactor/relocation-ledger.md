# Refactor relocation ledger

This ledger is the audit trail for moving symbols during the object-oriented
reorganization. Keep one row for every baseline file or public symbol that is
renamed, split, wrapped, consolidated, or removed. Do not mark a row complete
until its behavior has coverage in the characterization or contract suite.

The baseline symbols and source locations come from:

```powershell
python scripts/inventory_baseline.py --write
```

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `pending` | No relocation has been performed. |
| `moved` | The implementation has a new owner and behavior tests pass. |
| `delegated` | The old import/API delegates to the new owner for compatibility. |
| `consolidated` | Multiple equivalent implementations have one owner; equivalence is recorded. |
| `dead` | Proved unreachable/unused and intentionally removed; evidence is linked. |
| `blocked` | Relocation requires an unresolved external decision or behavior fixture. |

## Ledger

| Baseline symbol/file | Baseline location | New owner | Status | Verification/evidence |
| --- | --- | --- | --- | --- |
| `BridgeService._extract_attachments`, `_extract_chatwoot_message_text`, `_extract_chatwoot_message_type`, `_is_forwardable_chatwoot_message`, `_is_chatwoot_message_deleted` | `backend/src/wootify/services/bridge_service.py` | `wootify.application.messaging.MessagePayloadParser` (legacy methods delegate) | `delegated` | `backend/tests/test_messaging_objects.py::test_message_parser_preserves_chatwoot_shape_fallbacks` |
| `BridgeService` Chatwoot id/contact/phone parsing helpers | `backend/src/wootify/services/bridge_service.py` | `wootify.application.messaging.MessagePayloadParser` | `delegated` | `backend/tests/test_messaging_objects.py::test_message_parser_preserves_chatwoot_shape_fallbacks` |
| `BridgeService._extract_destination`, `_choose_destination_chat_id`, `_looks_like_uuid`, `_split_prefixed_source_id` | `backend/src/wootify/services/bridge_service.py` | `wootify.application.messaging.DestinationResolver` | `delegated` | `backend/tests/test_messaging_objects.py::test_destination_resolver_prefers_expected_platform_prefix` |
| `BridgeService` media filename/content-type helpers | `backend/src/wootify/services/bridge_service.py` | `wootify.application.messaging.MediaNormalizer` | `delegated` | `backend/tests/test_messaging_objects.py::test_media_normalizer_uses_magic_bytes_and_extensions` |
| `ChatwootBridgeService._extract_peer_id`, `_strip_source_prefix`, `_extract_source_id`, `_extract_id`, `_extract_chatwoot_sender`, `_normalize_chatwoot_message_type`, `_is_chatwoot_bot_sender`, `_is_generic_contact_name` | `backend/src/wootify/services/chatwoot_bridge_service.py` | `wootify.application.messaging.ChatwootPayloadParser` | `delegated` | `backend/tests/test_messaging_objects.py::test_chatwoot_parser_legacy_and_platform_helpers` and existing adapter characterization suite |
| `ChatwootBridgeService._attachment_filename`, `_extract_chatwoot_attachments` | `backend/src/wootify/services/chatwoot_bridge_service.py` | `wootify.application.messaging.MediaNormalizer` / `ChatwootPayloadParser` | `delegated` | `backend/tests/test_messaging_objects.py::test_media_normalizer_uses_magic_bytes_and_extensions` |
| `ChatwootBridgeService._is_phone_number_destination`, `_is_foreign_contact_identifier`, `_normalize_bale_pv_phone` | `backend/src/wootify/services/chatwoot_bridge_service.py` | `wootify.application.messaging.ChatwootPayloadParser` | `delegated` | existing `test_bale_pv_phone_outbound.py` and `backend/tests/test_messaging_objects.py` |
| `BridgeService` status templates, status parsing and duplicate notification policy | `backend/src/wootify/services/bridge_service.py` | `wootify.application.messaging.NotificationPolicy` | `delegated` | `backend/tests/test_messaging_objects.py::test_notification_policy_status_aliases_templates_and_idempotency` |
| `BridgeService.sync_bale_pv_contacts`, `sync_bale_dialogs_to_chatwoot` orchestration loops | `wootify.application.messaging.bridge.BridgeService` | `wootify.application.messaging.workflows.BalePvSyncWorkflow` (legacy methods delegate) | `delegated` | `backend/tests/test_messaging_objects.py::test_bale_sync_workflow_preserves_non_bale_guard` plus existing platform adapter suite |
| Enterprise route configuration and lookup helpers | `services/enterprise_bale_service.py`, `services/enterprise_telegram_service.py` | `application/enterprise/policies.py::EnterpriseRoutePolicy` with service compatibility delegates | `consolidated` | `backend/tests/test_enterprise_policies.py` static and dynamic route cases |
| Enterprise message/label fallback helpers | `services/enterprise_bale_service.py`, `services/enterprise_telegram_service.py` | `application/enterprise/policies.py::EnterpriseMenuConfig` with service compatibility delegates | `consolidated` | `backend/tests/test_enterprise_policies.py::test_menu_config_uses_trimmed_override_and_fallback` |
| Enterprise live-session state transitions | `services/enterprise_bale_service.py`, `services/enterprise_telegram_service.py` | `application/enterprise/policies.py::EnterpriseSessionTransitionPolicy` with service compatibility delegates | `consolidated` | `backend/tests/test_enterprise_policies.py::test_session_policy_preserves_platform_state_and_closes_session` |
| Chatwoot webhook payload extraction and attachment MIME classification | `services/enterprise_bale_service.py`, `services/enterprise_telegram_service.py` | `application/enterprise/policies.py::EnterpriseChatwootPayloadPolicy` with service compatibility delegates | `consolidated` | `backend/tests/test_enterprise_policies.py` plus existing webhook/attachment suites |

## Completion rules

- Preserve public imports, route paths/methods/responses, settings names and
  defaults, database table/column metadata, platform keys, and frontend API
  request behavior unless an explicit compatibility shim is recorded.
- Record intentional consolidations and compatibility delegates rather than
  deleting the old row.
- The final comparison must have no `pending` rows and must include the test
  or contract that verifies every `moved`, `delegated`, `consolidated`, and
  `dead` row.
