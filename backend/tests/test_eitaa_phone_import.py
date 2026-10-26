"""No-network regressions for account-scoped Eitaa phone discovery/import."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wootify.plugins.eitaa_pv.connector import EitaaPvConnector, EitaaPvRuntime, EitaaPreSendError

PHONE = "989904905660"
USER = {"_": "user", "id": 24709251, "access_hash": 991, "phone": PHONE}


def make_connector(*, contacts=None, dialogs=None, import_result=None, pages=None):
    client = SimpleNamespace(profile=SimpleNamespace(authenticated=True))
    client.dialogs = SimpleNamespace(list=AsyncMock(return_value=dialogs or {
        "_": "messages.dialogs", "dialogs": [], "users": [], "messages": []}))
    remaining_pages = list(pages or [])

    async def invoke(method, params):
        if method == "contacts.getContacts":
            return contacts if contacts is not None else {"_": "contacts.contacts", "users": []}
        if method == "messages.getDialogs":
            return remaining_pages.pop(0)
        assert method == "contacts.importContacts"  # No send RPC is permitted.
        if callable(import_result):
            return import_result(params["contacts"][0])
        return import_result if import_result is not None else {
            "_": "contacts.importedContacts", "imported": [], "retry_contacts": [], "users": []}

    client.invoke = AsyncMock(side_effect=invoke)
    result = EitaaPvConnector()
    result._runtimes["support"] = EitaaPvRuntime("support", "", Path("unused.json"), client)
    return result, client


def correlated(contact, user=None):
    user = USER if user is None else user
    return {"_": "contacts.importedContacts", "imported": [
        {"client_id": contact["client_id"], "user_id": user["id"]}],
        "retry_contacts": [], "users": [user]}


@pytest.mark.anyio
async def test_incident_3515_imports_unknown_phone_and_caches_only_support_peer():
    connector, client = make_connector(import_result=correlated)
    other = SimpleNamespace(peer_refs={"24709251": "user:24709251:OTHER_ACCOUNT_HASH"})
    connector._runtimes["other"] = other
    assert (await connector.resolve_phone_to_user("support", "+" + PHONE)) == USER
    request = client.invoke.call_args_list[-1]
    assert request.args[0] == "contacts.importContacts"
    contact = request.args[1]["contacts"][0]
    assert contact["phone"] == "+" + PHONE and contact["_"] == "inputPhoneContact"
    assert 0 < contact["client_id"] < 2**63
    assert connector._runtimes["support"].peer_refs == {"24709251": "user:24709251:991"}
    assert other.peer_refs["24709251"].endswith("OTHER_ACCOUNT_HASH")


@pytest.mark.anyio
async def test_known_contact_does_not_fetch_dialogs_or_import():
    connector, client = make_connector(contacts={"users": [USER]})
    assert (await connector.resolve_phone_to_user("support", "09904905660")) == USER
    assert client.invoke.await_count == 1
    client.dialogs.list.assert_not_awaited()


@pytest.mark.anyio
async def test_explicit_import_link_allows_hidden_phone():
    hidden = {key: value for key, value in USER.items() if key != "phone"}
    connector, _ = make_connector(import_result=lambda c: correlated(c, hidden))
    assert (await connector.resolve_phone_to_user("support", PHONE))["id"] == USER["id"]


@pytest.mark.parametrize("case", ["unrelated_client", "missing_user", "no_hash", "zero_hash", "boolean_hash"])
@pytest.mark.anyio
async def test_unverified_import_never_uses_first_user(case):
    def response(contact):
        user = dict(USER)
        if case == "no_hash":
            user.pop("access_hash")
        elif case == "zero_hash":
            user["access_hash"] = 0
        elif case == "boolean_hash":
            user["access_hash"] = True
        result = correlated(contact, user)
        if case == "unrelated_client":
            result["imported"][0]["client_id"] += 1
        elif case == "missing_user":
            result["imported"][0]["user_id"] += 1
        return result
    connector, _ = make_connector(import_result=response)
    with pytest.raises(RuntimeError, match="eitaa_phone_unresolved_for_account"):
        await connector.resolve_phone_to_user("support", PHONE)
    assert not connector._runtimes["support"].peer_refs


@pytest.mark.anyio
async def test_correlated_user_with_conflicting_phone_rejected():
    connector, _ = make_connector(import_result=lambda c: correlated(c, {**USER, "phone": "989111111111"}))
    with pytest.raises(RuntimeError, match="identity_mismatch"):
        await connector.resolve_phone_to_user("support", PHONE)


@pytest.mark.anyio
async def test_multiple_imported_ids_are_ambiguous():
    def response(contact):
        result = correlated(contact)
        result["imported"].append({"client_id": contact["client_id"], "user_id": 123})
        return result
    connector, _ = make_connector(import_result=response)
    with pytest.raises(RuntimeError, match="eitaa_phone_ambiguous"):
        await connector.resolve_phone_to_user("support", PHONE)


@pytest.mark.anyio
async def test_import_retry_request_is_not_not_found():
    connector, _ = make_connector(import_result=lambda c: {
        "retry_contacts": [c["client_id"]], "users": [], "imported": []})
    with pytest.raises(EitaaPreSendError, match="retry_requested"):
        await connector.resolve_phone_to_user("support", PHONE)


def first_page():
    return {"_": "messages.dialogsSlice", "count": 101,
        "dialogs": [{"peer": {"_": "peerUser", "user_id": 7}, "top_message": 80}],
        "messages": [{"id": 80, "date": 1234, "peer_id": {"_": "peerUser", "user_id": 7}}],
        "users": [{"_": "user", "id": 7, "phone": "989111111111", "access_hash": 8}]}


@pytest.mark.anyio
async def test_dialog_beyond_first_page_resolved_without_import():
    connector, client = make_connector(dialogs=first_page(), pages=[{
        "_": "messages.dialogs", "dialogs": [], "users": [USER]}])
    assert (await connector.resolve_phone_to_user("support", PHONE)) == USER
    page = client.invoke.call_args_list[-1]
    assert page.args == ("messages.getDialogs", {
        "exclude_pinned": True, "offset_date": 1234, "offset_id": 80,
        "offset_peer": {"_": "inputPeerUser", "user_id": 7, "access_hash": 8},
        "limit": 100, "hash": 0})


@pytest.mark.anyio
async def test_repeating_dialog_cursor_is_bounded_then_targeted_import():
    connector, client = make_connector(dialogs=first_page(), pages=[first_page()], import_result=correlated)
    assert (await connector.resolve_phone_to_user("support", PHONE))["id"] == USER["id"]
    assert sum(call.args[0] == "messages.getDialogs" for call in client.invoke.call_args_list) == 1


@pytest.mark.anyio
async def test_bare_dialog_peer_does_not_trigger_first_hit_search():
    page = first_page()
    page["users"] = []
    connector, client = make_connector(dialogs=page, import_result=correlated)
    assert (await connector.resolve_phone_to_user("support", PHONE)) == USER
    assert [call.args[0] for call in client.invoke.call_args_list] == [
        "contacts.getContacts", "contacts.importContacts"]


@pytest.mark.parametrize("response", [{"_": "contacts.contactsNotModified"}, {}])
@pytest.mark.anyio
async def test_incomplete_contacts_response_not_reported_as_missing(response):
    connector, client = make_connector(contacts=response)
    with pytest.raises(EitaaPreSendError, match="lookup_incomplete"):
        await connector.resolve_phone_to_user("support", PHONE)
    assert client.invoke.await_count == 1


@pytest.mark.anyio
async def test_empty_import_explains_account_discoverability_not_unregistered():
    connector, _ = make_connector()
    with pytest.raises(RuntimeError, match="check phone discoverability"):
        await connector.resolve_phone_to_user("support", PHONE)


@pytest.mark.anyio
async def test_invalid_phone_never_calls_eitaa():
    connector, client = make_connector()
    with pytest.raises(RuntimeError, match="invalid_phone"):
        await connector.resolve_phone_to_user("support", "123")
    client.invoke.assert_not_awaited()


@pytest.mark.anyio
async def test_ambiguous_saved_contact_does_not_import():
    connector, client = make_connector(contacts={"users": [USER, {**USER, "id": 8}]})
    with pytest.raises(RuntimeError, match="ambiguous"):
        await connector.resolve_phone_to_user("support", PHONE)
    assert client.invoke.await_count == 1


@pytest.mark.anyio
async def test_concurrent_resolution_serializes_single_import():
    connector, client = make_connector(import_result=correlated)
    original = client.invoke.side_effect
    saved = False

    async def invoke(method, params):
        nonlocal saved
        if method == "contacts.getContacts" and saved:
            return {"users": [USER]}
        if method == "contacts.importContacts":
            await asyncio.sleep(0)
            saved = True
        return await original(method, params)

    client.invoke.side_effect = invoke
    results = await asyncio.gather(*(connector.resolve_phone_to_user("support", PHONE) for _ in range(2)))
    assert all(r["id"] == USER["id"] for r in results)
    assert sum(c.args[0] == "contacts.importContacts" for c in client.invoke.call_args_list) == 1
