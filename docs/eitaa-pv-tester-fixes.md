# Eitaa PV tester follow-up (2026-10-10)

Implementation notes included in Wootify v7.1.1. Release publication does not imply deployment to a server.

## Contacts and names

- Normalize phone numbers and reuse an exact Chatwoot account contact across platform/instance maps.
- Repair older phone-less duplicates using the incoming platform's verified phone; keep the former contact as a local alias. No remote merge/deletion or identifier write.
- Keep durable creation/recovery anchors aligned with the preferred contact.
- Fail closed on ambiguous phone matches or conflicting peer ownership. Names alone are never identity evidence.
- Preserve meaningful existing Chatwoot names. New Eitaa contacts use the private dialog peer's first/last name, then username, then a generated Eitaa User label. The authenticated sender is not used as the customer's identity.

## Media and outbound failures

- Send all attachments separately, in their supplied order, without captions; then send the original nonempty text once.
- Commit each acknowledged part to message mappings. Retries skip acknowledged parts and resume the remaining attachment/text plan.
- Derive stable Eitaa random IDs from the outgoing delivery/part key, so retries use the same protocol idempotency token.
- Treat missing send acknowledgements as failures, not successes. Mark the Chatwoot bubble failed with external_error and retain the private failure note. The existing webhook queue re-arms failed deliveries on native Chatwoot Retry.
- On success restore the bubble to sent. A send acknowledgement is not proof the customer has read/received it.
- Controller timeouts also request failed status/private-note notification.

## Update delivery architecture

Eitaa previously scanned up to 100 dialogs and 20 messages per dialog with process-local watermarks. Only processing failures entered the shared retry queue; tuple-keyed entity payloads could not be serialized correctly.

The new path is:

1. Read updates.getState once for initial baseline, or restore the saved pts/date/qts/seq cursor.
2. Fetch updates.getDifference and retain the returned batch and next cursor in memory until persistence succeeds.
3. Atomically commit every raw event to inbound_event_retries and the next cursor to instance_runtime_state.last_platform_update_id. Encode entities as a JSON-safe list.
4. Acknowledge the batch only after that commit. A database failure retries the same batch; a restart resumes from the previously committed cursor.
5. The shared worker resolves attachments and delivers to Chatwoot, with per-peer ordering, retries/backoff, and message-mapping deduplication. Explicit deletion events update the already-mapped Chatwoot row.

No new database tables or schema migration are required. Initial adoption starts at the current upstream state; it is not automatic historic backfill. updates.differenceTooLong stops with a visible recovery-required error rather than silently discarding history.

## Polling

The Eitaa Web worker reads help.getConfig.schedule_period_foreground_ms, falling back to 5000 ms, and switches to background scheduling when its page is inactive. On 2026-10-10 the production session returned foreground=35000 ms, background=180000 ms, background delay=10000 ms.

Wootify is an active connector, so it uses the foreground value dynamically instead of the previous fixed configured history-scan interval. The instance form displays automatic server-controlled polling.

Source inspected: https://web.eitaa.com/mtproto.worker-Zk-UnVZ2.js

## Replies, deletions and locations

- Read the nested messageReplyHeader.reply_to_msg_id, plus the old flattened field for compatibility. Resolve the parent only inside the same instance, platform peer and Chatwoot conversation.
- Native quoted replies require the original parent to have been bridged. Old/unmapped or cross-conversation parents require history import or a separate textual-quote fallback; the implementation does not guess a target.
- Consume updateDeleteMessages and updateDeleteChannelMessages rather than infer deletion from missing history entries. Channel IDs remain scoped. Global deletes resolve only unambiguous private/group mappings in this instance.
- A deleted original that was never bridged has no Chatwoot row to modify. Channel update gaps signaled through updateChannelTooLong require a dedicated channel-difference recovery path; it is not covered by the account difference implementation.
- Convert geo/live-geo/venue media to Google Maps coordinate URLs, not downloads. Render contact cards as text. A live location is a coordinate snapshot, not an embedded live tracker.

## Production case 2836

Contact 4004 (Ali Dabbagh) retained a WhatsApp JID and phone +989904905660, but had no Eitaa instance-owned mapping. The old bridge only offered phone-based outbound resolution for Bale. Incoming Eitaa traffic created phone-less contact 5215 (Dabagh), mapped to Eitaa user 24709251.

The earlier production hotfix verified the phone against Eitaa, recorded a local alias and repaired conversation 2836's route while preserving its WhatsApp identifier. This tester follow-up additionally fixes canonical phone matching, media ordering, stream delivery and related behavior locally.
