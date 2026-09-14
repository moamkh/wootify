# Eitaa PV protocol inventory

Status: source-verified against `https://web.eitaa.com` on 2026-09-12.  Dynamic
packet capture is configured separately in `C:\Users\amin\Desktop\bale_pv_test\trafic monitor\captured_eitaa`.

## Key conclusion

Eitaa Web is a TL (Type Language) client, not a gRPC-Web/WebSocket client like
`bale_pv_connector`. Its transport class is equivalent to:

```js
fetch(baseUrl, { method: "POST", body: tlEnvelope })
  .then(response => response.arrayBuffer())
```

The body is raw TL bytes. Each API method is wrapped in Eitaa's `eitaaObject`
envelope (`token`, `imei`, `packed_data`, `layer`, `flags`); authenticated
responses provide a bearer token. This is stateless HTTPS transport, not the
conventional long-lived encrypted MTProto socket.

There is no browser `WebSocket(...)` construction in the Eitaa Web production
bundle.  The source label `websocket` is only a server-selection cache key;
the selected object is the HTTPS transport above.

## HTTPS transport endpoints

All application RPCs are multiplexed through these base URLs rather than
having one REST URL per operation.

| Purpose | Base URLs |
| --- | --- |
| Main/client RPC | `https://hasan.eitaa.ir/eitaa/`, `https://hosna.eitaa.com/eitaa/`, `https://armita.eitaa.com/eitaa/`, `https://majid.eitaa.com/eitaa/`, `https://alireza.eitaa.com/eitaa/`, `https://mostafa.eitaa.com/eitaa/`, `https://sajad.eitaa.ir/eitaa/`, `https://bagher.eitaa.ir/eitaa/`, `https://sadegh.eitaa.ir/eitaa/`, `https://kazem.eitaa.ir/eitaa/` |
| Downloads | `https://mohsen.eitaa.com/eitaa/`, `https://ghasem.eitaa.com/eitaa/`, `https://hadi.eitaa.com/eitaa/`, `https://hossein.eitaa.com/eitaa/`, `https://vahid.eitaa.com/eitaa/` |
| Uploads | `https://alzheimer.eitaa.com/eitaa/`, `https://fateme.eitaa.com/eitaa/`, `https://ali.eitaa.com/eitaa/`, `https://meysam.eitaa.com/eitaa/` |
| Development | `https://dev3.eitaa.com/eitaa/index.php` |

The web client chooses a server at random from the relevant pool, tracks
failed servers, and retries another one.  The monitor must therefore capture
both `eitaa.com` and `eitaa.ir`.

## Bale PV feature parity map

| Bale PV capability | Eitaa TL call(s) | Connector work |
| --- | --- | --- |
| Request login code | `auth.sendCode` | Serialize `phone_number`, `api_id`, `api_hash`, and `CodeSettings` inside the Eitaa envelope. |
| Verify login code | `auth.signIn` | Persist the returned authorization token/session. |
| Two-factor password | `account.getPassword`, `auth.checkPassword`, recovery calls | Required for accounts that return `SESSION_PASSWORD_NEEDED`. |
| Contacts/users | `contacts.getContacts`, `users.getUsers`, `users.getFullUser`, `contacts.resolveUsername` | Resolve peer id/access hash before sending to a user. |
| Dialog list | `messages.getDialogs`, `messages.getPinnedDialogs`, `messages.getPeerDialogs` | Supports folders, offsets, pagination, pinned dialogs. |
| Message history | `messages.getHistory`, `messages.getMessages`, `messages.search` | Input peer and offsets are required. |
| Send text | `messages.sendMessage` | Use a cryptographically random `random_id`; response is an `Updates` object or `updateShortSentMessage`. |
| Send/edit/delete/read | `messages.sendMedia`, `messages.sendMultiMedia`, `messages.editMessage`, `messages.deleteMessages`, `messages.readHistory`, `messages.readMentions` | Upload first for media; normalize returned updates into Chatwoot events. |
| Typing/drafts | `messages.setTyping`, `messages.saveDraft`, `messages.getAllDrafts` | Optional parity; useful for polished outbound UX. |
| Upload/download media | `upload.saveFilePart`, `upload.saveBigFilePart`, `messages.uploadMedia`, `upload.getFile`, `upload.getFile2`, `upload.getCdnFile` | Use the separate upload/download pools. |
| Live updates | `http_wait`, `updates.getState`, `updates.getDifference`, `updates.getChannelDifference` | Long-poll; no WebSocket client is needed. |

## Realtime algorithm

1. After authentication, call `updates.getState` and store `pts`, `qts`,
   `date`, and `seq`.
2. Keep one `http_wait(max_delay=500, wait_after=150,
   max_wait=25000)` request outstanding.  It is Eitaa Web's 25-second
   long-poll connection.
3. Decode incoming TL update containers and process `updates`,
   `updatesCombined`, `updateShort`, and `updatesTooLong`.
4. When sequence/PTS gaps occur (or `updatesTooLong` arrives), call
   `updates.getDifference(pts, date, qts)`.  Continue through
   `updates.differenceSlice` until `updates.difference` or
   `updates.differenceEmpty`.
5. For channel gaps, use `updates.getChannelDifference` with that channel's
   stored PTS.

Important update constructors for a Chatwoot adapter: `updateNewMessage`,
`updateNewChannelMessage`, `updateEditMessage`, `updateEditChannelMessage`,
`updateDeleteMessages`, `updateDeleteChannelMessages`,
`updateReadHistoryInbox`, `updateReadHistoryOutbox`, and user/chat typing
updates.

## Implementation boundary

The Bale connector's protobuf encoder and gRPC-Web WebSocket client cannot be
reused for Eitaa. Reusable higher-level concepts are its async client shape,
session lifecycle, dialog/history normalization, outbound message adapter, and
update-to-Chatwoot mapping. Eitaa needs a TL serializer/parser, the
`eitaaObject` envelope, token/session storage, and the embedded Eitaa schema.

The Eitaa production bundle already embeds the TL constructors and methods,
including `auth.sendCode`, `auth.signIn`, `messages.getDialogs`,
`messages.getHistory`, `messages.sendMessage`, `upload.saveFilePart`,
`updates.getState`, and `updates.getDifference`.  It is the authoritative
schema source to version alongside a connector implementation.
