"""Inbox-local connector notices. These routes are never platform recipients."""
from wootify.infrastructure.persistence.models import Conversation

STATUS_PEER = "__wootify_connector_status__"


async def publish_eitaa_status(db, instance_key, status):
    from wootify.application.messaging.chatwoot_bridge import chatwoot_bridge
    instance, cfg, client = chatwoot_bridge._chatwoot_client_for_instance(db, instance_key)
    account_id, inbox_id = int(cfg["account_id"]), int(cfg["inbox_id"])
    row = db.query(Conversation).filter(
        Conversation.instance_id == instance.id,
        Conversation.platform_conversation_id == STATUS_PEER,
        Conversation.chatwoot_inbox_id == str(inbox_id),
    ).first()
    content = f"Eitaa Connector — {status}\nThis is a read-only connector status conversation. Replies are not sent to Eitaa."
    if row is None:
        contact = await client.create_contact(account_id, {
            "name": "Eitaa Connector", "inbox_id": inbox_id,
            "additional_attributes": {"wootify_connector_status": instance_key},
        })
        payload = contact.get("payload", contact)
        contact_id = (payload.get("contact") or payload).get("id")
        if not contact_id:
            raise RuntimeError("Chatwoot did not return a connector contact ID")
        remote = await client.create_conversation(account_id, {
            "contact_id": contact_id, "inbox_id": inbox_id,
            "additional_attributes": {"wootify_connector_status": instance_key},
        })
        conversation_id = remote.get("id") or remote.get("payload", {}).get("id")
        if not conversation_id:
            raise RuntimeError("Chatwoot did not return a connector conversation ID")
        row = Conversation(
            instance_id=instance.id, platform_conversation_id=STATUS_PEER,
            chatwoot_conversation_id=str(conversation_id),
            chatwoot_contact_id=str(contact_id), chatwoot_inbox_id=str(inbox_id),
        )
        db.add(row)
        db.commit()
    conversation_id = int(row.chatwoot_conversation_id)
    messages = await client._request("GET", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages")
    notices = [m for m in messages.get("payload", []) if m.get("private") and str(m.get("content", "")).startswith("Eitaa Connector — ")]
    if notices and notices[-1].get("content") == content:
        return
    await client.post_message(account_id, conversation_id, {"content": content, "message_type": "outgoing", "private": True})
