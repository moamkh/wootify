from unittest.mock import AsyncMock, patch
import pytest
from wootify.models import Instance, PlatformType, Conversation
from wootify.application.messaging.connector_status import STATUS_PEER, publish_eitaa_status
from wootify.application.messaging.chatwoot_bridge import chatwoot_bridge
from wootify.infrastructure.security.crypto import encryptor


def instance(db):
    p = PlatformType(key="eitaa_pv_enterprise", display_name="Eitaa", capabilities_json={}, metadata_schema_json={})
    db.add(p)
    db.flush()
    i = Instance(instance_key="status-test", platform_type_id=p.id, is_enabled=True,
                 platform_metadata_encrypted="", proxy_config_encrypted="",
                 chatwoot_config_encrypted=encryptor.encrypt_json({"account_id":1,"inbox_id":57,"base_url":"http://chatwoot","token":"test"}))
    db.add(i)
    db.commit()
    return i


@pytest.mark.anyio
async def test_status_notes_reuse_route_and_do_not_touch_identifiers(db_session):
    i = instance(db_session)
    client = AsyncMock()
    client.create_contact.return_value = {"payload": {"contact": {"id": 10}}}
    client.create_conversation.return_value = {"id": 20}
    client._request.return_value = {"payload": []}
    with patch.object(chatwoot_bridge, "_chatwoot_client_for_instance", return_value=(i, {"account_id":1,"inbox_id":57}, client)):
        await publish_eitaa_status(db_session, i.instance_key, "Login required")
        content = client.post_message.call_args.args[2]["content"]
        client._request.return_value = {"payload": [{"private":True,"content":content}]}
        await publish_eitaa_status(db_session, i.instance_key, "Login required")
    assert client.create_contact.await_count == 1
    assert "identifier" not in client.create_contact.call_args.args[1]
    assert client.post_message.await_count == 1
    assert client.post_message.call_args.args[2]["private"] is True


@pytest.mark.parametrize("event", ["message_created", "message_updated", "conversation_status_changed"])
@pytest.mark.anyio
async def test_status_route_blocks_every_outbound_operation(db_session, event):
    i = instance(db_session)
    db_session.add(Conversation(instance_id=i.id, platform_conversation_id=STATUS_PEER,
                                chatwoot_conversation_id="20", chatwoot_inbox_id="57"))
    db_session.commit()
    result = await chatwoot_bridge.handle_chatwoot_webhook(db_session, i.instance_key, {
        "event":event,"conversation":{"id":20},"content":"send this","message_type":"outgoing",
    })
    assert result["reason"] == "connector_status_read_only"
