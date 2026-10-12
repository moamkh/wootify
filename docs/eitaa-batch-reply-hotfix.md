# Eitaa replies to outgoing attachment batches

## Confirmed root cause (2026-10-12)

The supplied screenshot belongs to account 3 / inbox 57 / conversation 2922,
not the initially supplied conversation 2930. Production read-only inspection
confirmed that customer message 85899 (Eitaa 95, text `b`) replied to Eitaa
image 92. That image belongs to outgoing Chatwoot message 85888. Its separate
caption was Eitaa 93.

Wootify sends attachments first and captions separately. To keep caption edits
targeting text rather than the image, the caption owns the direct
chatwoot_message_id mapping. The image receipt has no direct Chatwoot ID, but
retains batch_message_id=85888. Inbound reply lookup only checked the direct ID,
so replies to images lost their preview. Ordinary text replies worked.

## Exact change

In backend/src/wootify/application/messaging/chatwoot_bridge.py,
ingest_platform_event now resolves an Eitaa outgoing batch receipt without a
direct Chatwoot ID through its batch_message_id. A canonical outgoing mapping
must exist in the exact same local conversation. The existing instance,
Chatwoot conversation and platform peer filters remain enforced.

It sends content_attributes.in_reply_to using the original Chatwoot message ID
and persists both parent IDs. All files in a batch can resolve to the same
original Chatwoot message. Caption editing, send order, retry checkpoints and
the one-to-one mapping constraints remain unchanged. No migration, frontend
change, contact identifier write or WhatsApp/Evolution change is needed.

Existing batch receipts already contain the required metadata; no database
backfill is required for future replies. This does not repair already-created
Chatwoot messages or resend any customer messages.

## Regression verification

backend/tests/test_eitaa_tester_regressions.py covers normalized text, location
and contact replies to operator messages; replies to both images and the caption
in a multi-file batch; and absent/cross-conversation canonical mappings. The
batch image cases failed before the fix and passed afterward. Existing multipart
reply encoding and outbound caption-retry tests are also run.

Verification completed: 123 tests passed across Eitaa tester regressions,
multipart content attributes, Eitaa echo handling and Bale PV adapter tests.

## Production hotfix deployment

Deployed source commit a17469d to production 192.168.20.76 on 2026-10-12
at approximately 16:00 Tehran / 12:30 UTC, retaining version 7.1.1. Only the
bridge source file and this documentation were installed. Eitaa renewal code,
frontend, instance configuration, sessions and other applications were preserved.

Backup: /opt/wootify-backups/eitaa-batch-reply-20261012T123015Z, containing the
previous source, configuration, deployment marker and verified database dump.
Backend import, service startup, HTTP health and version checks passed. Counts
before/after were identical: 8 instances, 1449 conversations, 29356 message
mappings and 420 contact mappings. Migration remained f7a0c02d21b3. No schema
migration, historical message replay or customer-message send was performed.

Installed source SHA256:
2aa8f78bff8ea440e3fe0b89671b9ff6e466da654cafb874773e524763c0adb4.
The server deployment marker records the hotfix commit. Demo was not updated
by this request. Historical message repair is separate; the existing `b`
message was not modified. No release tag was moved.
