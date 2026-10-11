import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from eitaa_cli.config import EitaaSettings
from eitaa_cli.errors import EitaaRPCError, EitaaTransportError
from eitaa_cli.session import SessionProfile

from wootify.plugins.eitaa_pv.connector import EitaaPvConnector, EitaaPvRuntime
from wootify.plugins.eitaa_pv.session_client import EitaaLoginRequired, EitaaRefreshPending, SessionAwareEitaaClient


def client(tmp_path):
    return SessionAwareEitaaClient(
        EitaaSettings(session_file=tmp_path / "session.json"),
        transport=SimpleNamespace(close=AsyncMock()),
        _loaded_profile=SessionProfile(name="test", token="old-token", imei="fixed-device", phone_number="989111111111", user={"id":42}),
    )


def renewed():
    return {"_":"eitaa_updates_token", "token":"new-token", "expire":3600, "date":123}


@pytest.mark.anyio
@pytest.mark.parametrize("marker", ["eitaa_updates_expire_token", "eitaa_token_updating"])
async def test_refresh_persists_device_and_replays_same_send_params(tmp_path, marker):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":marker}, renewed(), {"_":"updateShortSentMessage", "id":7}])
    params = {"message":"hello", "random_id":123456}
    assert (await c.invoke("messages.sendMessage", params))["id"] == 7
    first, refresh, replay = c._raw_invoke.await_args_list
    assert first.args == replay.args == ("messages.sendMessage", params)
    assert first.kwargs["token"] == "old-token" and replay.kwargs["token"] == "new-token"
    assert refresh.args[0] == "eitaaRefreshToken"
    assert refresh.args[1]["app_info"] == c.settings.web_profile.app_info(build_version=c.settings.build_version, app_version=c.settings.app_version)
    saved = await c.store.aget("test")
    assert saved.token == "new-token" and saved.imei == "fixed-device" and saved.user == {"id":42}
    assert not c.login_required and not c.refresh_pending


@pytest.mark.anyio
async def test_concurrent_expired_requests_share_one_rotation(tmp_path):
    c = client(tmp_path)
    ready = asyncio.Event()
    count = 0
    async def rpc(method, params=None, **kwargs):
        nonlocal count
        if method == "eitaaRefreshToken":
            await asyncio.sleep(0)
            return renewed()
        if kwargs["token"] == "old-token":
            count += 1
            if count == 8:
                ready.set()
            await ready.wait()
            return {"_":"eitaa_updates_expire_token"}
        return {"_":"updates.state", "pts":1}
    c._raw_invoke = AsyncMock(side_effect=rpc)
    results = await asyncio.gather(*(c.invoke("updates.getState", {}) for _ in range(8)))
    assert all(r["pts"] == 1 for r in results)
    assert sum(call.args[0] == "eitaaRefreshToken" for call in c._raw_invoke.await_args_list) == 1


@pytest.mark.anyio
async def test_restart_uses_atomically_saved_replacement(tmp_path):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, renewed(), {"_":"updates.state"}])
    await c.invoke("updates.getState", {})
    restarted = await SessionAwareEitaaClient.create(c.settings, profile="test", transport=SimpleNamespace(close=AsyncMock()))
    assert restarted.profile.token == "new-token" and restarted.profile.imei == "fixed-device"
    restarted._raw_invoke = AsyncMock(return_value={"_":"updates.state"})
    await restarted.invoke("updates.getState", {})
    assert restarted._raw_invoke.await_args.kwargs["token"] == "new-token"


@pytest.mark.anyio
async def test_real_wire_codec_encodes_refresh_and_decodes_rotated_token(tmp_path):
    c = client(tmp_path)
    c.transport.post = AsyncMock(side_effect=[
        c.codec.encode_constructor("eitaa_updates_expire_token", {}),
        c.codec.encode_constructor("eitaa_updates_token", {"token":"new-token","expire":3600,"date":123}),
        c.codec.encode_constructor("updates.state", {"pts":10,"qts":0,"date":123,"seq":1,"unread_count":0}),
    ])
    assert (await c.invoke("updates.getState", {}))["pts"] == 10
    envelopes = [c.codec.decode_any(call.args[0]) for call in c.transport.post.await_args_list]
    assert [e["token"] for e in envelopes] == ["old-token","old-token","new-token"]
    assert all(e["imei"] == "fixed-device" for e in envelopes)
    assert envelopes[1]["packed_data"] == c.codec.encode_method("eitaaRefreshToken", {
        "app_info":c.settings.web_profile.app_info(build_version=c.settings.build_version,app_version=c.settings.app_version)})


@pytest.mark.anyio
async def test_cancelled_webhook_does_not_cancel_rotation(tmp_path):
    c = client(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def rpc(method, params=None, **kwargs):
        if method == "eitaaRefreshToken":
            entered.set()
            await release.wait()
            return renewed()
        if kwargs["token"] == "old-token":
            return {"_":"eitaa_updates_expire_token"}
        return {"_":"updates.state"}
    c._raw_invoke = AsyncMock(side_effect=rpc)
    waiter = asyncio.create_task(c.invoke("updates.getState", {}))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await c.invoke("updates.getState", {})
    assert (await c.store.aget("test")).token == "new-token"
    assert sum(call.args[0] == "eitaaRefreshToken" for call in c._raw_invoke.await_args_list) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("error", [EitaaTransportError("unreachable"), EitaaRPCError(500,"SERVER_ERROR"), TimeoutError()])
async def test_transient_refresh_errors_keep_session_and_back_off(tmp_path, error):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, error])
    with pytest.raises(EitaaRefreshPending):
        await c.invoke("updates.getState", {})
    assert c.profile.token == "old-token" and not c.login_required and c.refresh_pending
    assert c._retry_at > time.monotonic()
    with pytest.raises(EitaaRefreshPending, match="backoff"):
        await c.invoke("updates.getState", {})
    assert c._raw_invoke.await_count == 2
    c._retry_at = 0
    c._raw_invoke.side_effect = [renewed(), {"_":"updates.state"}]
    await c.invoke("updates.getState", {})
    assert not c.refresh_pending and c.profile.token == "new-token"


@pytest.mark.anyio
async def test_failed_token_save_retries_persistence_without_another_rotation(tmp_path):
    c = client(tmp_path)
    real_save = c.store.asave
    save_calls = 0
    async def save(profile):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("disk temporarily unavailable")
        await real_save(profile)
    c.store.asave = AsyncMock(side_effect=save)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, renewed(), {"_":"updates.state"}])
    with pytest.raises(EitaaRefreshPending):
        await c.invoke("updates.getState", {})
    assert c.profile.token == "old-token" and c._pending_profile.token == "new-token"
    c._retry_at = 0
    await c.invoke("updates.getState", {})
    assert sum(call.args[0] == "eitaaRefreshToken" for call in c._raw_invoke.await_args_list) == 1
    assert (await c.store.aget("test")).token == "new-token"


@pytest.mark.anyio
async def test_invalid_login_on_business_rpc_refreshes_before_requesting_otp(tmp_path):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[EitaaRPCError(401,"INVALID_LOGIN"), renewed(), {"_":"updates.state"}])
    await c.invoke("updates.getState", {})
    assert not c.login_required and c._raw_invoke.await_count == 3


@pytest.mark.anyio
@pytest.mark.parametrize("error", [EitaaRPCError(401,"INVALID_LOGIN"), EitaaRPCError(401,"SESSION_REVOKED")])
async def test_definitively_rejected_refresh_requires_login_without_deleting_token(tmp_path, error):
    c = client(tmp_path)
    await c.store.asave(c.profile)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, error])
    with pytest.raises(EitaaLoginRequired, match="phone login code"):
        await c.invoke("updates.getState", {})
    assert c.login_required and (await c.store.aget("test")).token == "old-token"
    with pytest.raises(EitaaLoginRequired):
        await c.invoke("updates.getState", {})
    assert c._raw_invoke.await_count == 2


@pytest.mark.anyio
async def test_explicit_otp_request_bypasses_failed_session_gate(tmp_path):
    c = client(tmp_path)
    c.login_required = True
    c._raw_invoke = AsyncMock(return_value={"_":"auth.sentCode"})
    await c.invoke("auth.sendCode", {"phone_number":"test"}, token="")
    assert c._raw_invoke.await_args.kwargs["token"] == ""
    c.reset_session_recovery()
    assert not c.login_required and not c.refresh_pending


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["upload", "download"])
async def test_media_calls_retry_on_original_transport_kind(tmp_path, kind):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, renewed(), {"_":"boolTrue"}])
    await c.invoke("upload.saveFilePart", {"file_id":17,"file_part":0,"bytes":b"data"}, kind=kind)
    first, refresh, replay = c._raw_invoke.await_args_list
    assert first.kwargs["kind"] == replay.kwargs["kind"] == kind
    assert refresh.kwargs.get("kind", "client") == "client"
    assert first.args == replay.args


@pytest.mark.anyio
async def test_ambiguous_business_timeout_is_never_automatically_resent(tmp_path):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=EitaaTransportError("upload timed out"))
    with pytest.raises(EitaaTransportError):
        await c.invoke("messages.sendMedia", {"random_id":17})
    assert c._raw_invoke.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("response", [{"_":"eitaa_token_updating"}, {"_":"eitaa_updates_expire_token"}, {"_":"eitaa_updates_token","token":""}])
async def test_malformed_or_pending_refresh_is_not_permanent_logout(tmp_path, response):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, response])
    with pytest.raises(EitaaRefreshPending):
        await c.invoke("updates.getState", {})
    assert not c.login_required and c.profile.token == "old-token"


@pytest.mark.anyio
async def test_unsolicited_replacement_is_saved_before_retry(tmp_path):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[renewed(), {"_":"updates.state"}])
    await c.invoke("updates.getState", {})
    assert c._raw_invoke.await_count == 2
    assert (await c.store.aget("test")).token == "new-token"


@pytest.mark.anyio
async def test_repeated_rotation_signals_have_bounded_replays(tmp_path):
    c = client(tmp_path)
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, renewed(), {"_":"eitaa_token_updating"},
        {**renewed(),"token":"third-token"}, {"_":"eitaa_updates_expire_token"}])
    with pytest.raises(EitaaRefreshPending, match="did not settle"):
        await c.invoke("updates.getState", {})
    assert c._raw_invoke.await_count == 5 and not c.login_required


@pytest.mark.anyio
async def test_refresh_keeps_difference_cursor_and_ack_boundary(tmp_path):
    c = client(tmp_path)
    c.dialogs.list = AsyncMock(return_value={"dialogs":[]})
    c._raw_invoke = AsyncMock(side_effect=[{"_":"eitaa_updates_expire_token"}, renewed(), {"_":"updates.differenceEmpty","date":101,"seq":2}])
    connector = EitaaPvConnector()
    r = EitaaPvRuntime("test","",tmp_path / "session.json",c,update_state={"pts":10,"date":100,"qts":0,"seq":1},web_poll_interval=35)
    connector._runtimes["test"] = r
    connector._set_presence = AsyncMock()
    assert (await connector.get_updates("test"))["result"] == []
    assert r.update_state["date"] == 100 and r.pending_update_state["date"] == 101
    assert r.update_state["pts"] == r.pending_update_state["pts"] == 10
    connector.acknowledge_updates("test")
    assert r.update_state["date"] == 101


@pytest.mark.anyio
async def test_connector_reports_only_confirmed_auth_rejection_as_login_required(tmp_path):
    c = client(tmp_path)
    connector = EitaaPvConnector()
    connector._runtimes["test"] = EitaaPvRuntime("test","",tmp_path / "session.json",c)
    c.refresh_pending = True
    state = await connector.get_connection_state("test")
    assert state["detail"] == "token_refresh_pending"
    c.refresh_pending = False
    c.login_required = True
    assert (await connector.get_connection_state("test"))["detail"] == "login_required"
    with pytest.raises(EitaaLoginRequired):
        connector._authenticated_runtime("test")
