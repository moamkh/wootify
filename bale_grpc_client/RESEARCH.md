# Bale API Reverse Engineering — Research Notes

> **For future Kimi sessions:** This document explains exactly how the Bale Messenger API was discovered, what tools were used, and what remains to be done.

---

## How We Found the API

### Step 1: Open web.bale.ai in a browser

```bash
# Navigate to Bale's web client
https://web.bale.ai
```

The web client is a React application that communicates with Bale's backend using gRPC-Web/protobuf.

### Step 2: Monitor Network Traffic

Using the browser's DevTools (or Playwright's `browser_network_requests`), we observed:

1. **Authentication requests**:
   ```
   POST https://next-ws.bale.ai/bale.auth.v1.Auth/StartPhoneAuth
   Content-Type: application/grpc-web+proto
   ```

2. **Response headers**:
   ```
   grpc-status: 3
   grpc-message: PHONE_NUMBER_INVALID
   content-type: application/grpc-web+proto
   ```

### Step 3: Extract the JS Bundle

The main application bundle is at:
```
https://web.bale.ai/static/js/index.8df14b3475.js
```

We fetched this bundle and analyzed it using regex patterns to extract:
- Service definitions
- Method names
- Message structures
- Endpoint URLs

### Step 4: Key Patterns Used for Extraction

```javascript
// Find all service names
/serviceName:"(bale\.[a-z]+\.v\d+\.[A-Za-z]+)"/g

// Find all method names
/methodName:"([A-Za-z]+)"/g

// Find RPC class methods
/([A-Za-z]+)\(e,t\)\{return this\.rpc\.unary\(/g

// Find WebSocket endpoints
/(wss?:\/\/[a-z0-9.-]+)/g

// Find message field definitions
/function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\(\)\{return\{([a-zA-Z0-9_:$",\s]+)\}\}/g
```

---

## Complete Service Inventory

### bale.auth.v1.Auth
**Methods (26 total):**
```
StartPhoneAuth, ValidateCode, ValidatePassword, SignUp, GetAuthSessions,
TerminateSession, TerminateAllSessions, SignOut, LogOut, DeleteAccount,
ChangePhone, SendDeleteAccountVerificationCode, SendChangePhoneVerificationCode,
GetUserIdToken, GetTicket, GetBajeBamTicket, GetBaleTicket, GetJWTToken,
EnableTwoFactorAuthentication, IsTwoFactorAuthenticationEnabled, VerifyEmail,
RecoverPassword, VerifyPasswordRecovery, SetNewPassword, VerifyPassword,
DisableTwoFactorAuthentication
```

**StartPhoneAuth Request Fields** (from JS call site):
```javascript
StartPhoneAuth({
  phoneNumber: e,
  deviceTitle: i,
  sendCodeType: t,
  apiKey: a.apiKey,
  appId: a.id,
  deviceHash: (0,A.zC)(n),
  timeZone: void 0,
  imeiList: void 0,
  preferredLanguages: [],
  options: o
})
```

**ValidateCode Request Fields**:
```javascript
ValidateCode({
  code: e,
  transactionHash: t,
  isJwt: !0,
  futureAuthTokens: []
})
```

**StartPhoneAuth Response Fields** (from protobuf decoder):
```protobuf
message StartPhoneAuthResponse {
  string transactionHash = 1;
  bool isRegistered = 2;
  int32 activationType = 3;
  bool isImeiOk = 4;
  int32 sentCodeType = 5;
  Timestamp codeExpirationDate = 6;
  int32 nextSendCodeType = 7;
  Timestamp nextSendCodeWaitTime = 8;
  Timestamp codeTimeout = 9;
  repeated string exInfoAddress = 10;
  repeated int32 availableSendCodeTypes = 11;
}
```

### bale.fanoos.v1.fanoos (Messaging)
**Methods:** `Send`, `SendBatch`

### bale.users.v1.Users
**Methods (29 total):**
```
GetFullUser, GetContacts, LoadUsers, LoadFullUsers, LoadFullUsersSequentially,
LoadAvatars, LoadBlockedUsers, SearchContacts, AddContact, RemoveContact,
ImportContacts, ResetContacts, BlockUser, UnblockUser, EditName, EditAbout,
EditAvatar, RemoveAvatar, EditNickName, CheckNickName, EditBirthDate, EditSex,
EditParameter, GetParameters, GetUserPrivacyStatus, SetUserPrivacyStatus,
GetUserFullPrivacy, EditUserLocalName, EditMyPreferredLanguages,
EditMyTimeZone, NotifyAboutDeviceInfo, IsNameAllowed
```

### bale.ramz.v1.Ramz (Password)
**Methods:** `SetPassword`, `CheckPassword`, `CheckPasswordSet`, `ForgetPassword`, `ValidateOTP`, `ChangePhoneNumber`, `ConfirmPhoneNumber`

### bale.feedback.v1.FeedBack
**Methods:** `SendFeedBack`

### bale.report.v1.Report
**Methods:** `ReportInappropriateContent`, `ReportDismiss`

---

## Network Architecture

### Unary RPC Transport
```
POST https://next-ws.bale.ai/{service}/{method}
Content-Type: application/grpc-web+proto
x-grpc-web: 1
```

**Required Headers:**
```
mt_app_version: 157595
app_version: 157595
browser_type: 1
mt_browser_type: 1
browser_version: 148.0.0.0
mt_browser_version: 148.0.0.0
os_type: 3
mt_os_type: 3
session_id: <timestamp>
mt_session_id: <timestamp>
x-grpc-web: 1
```

### WebSocket Transport
```
wss://next-ws.bale.ai  (primary)
wss://maviz-ws.bale.ai (fallback)
```

**WebSocket Message Format** (protobuf):
```protobuf
message ServerPack {
  bool terminateSession = ?;
  Response response = ?;
  Update update = ?;
}
```

The `update` field carries real-time events like new messages.

---

## gRPC-Web Frame Format

```
[1 byte flags][4 bytes length][N bytes payload]
```

- **Flags**: `0x00` = uncompressed, `0x01` = compressed
- **Length**: Big-endian uint32
- **Payload**: Protobuf serialized message

---

## What Remains To Be Done

### Priority 1: Protobuf Definitions
The biggest blocker is the lack of `.proto` files. Options to obtain them:

1. **Extract from JS bundle**: The JS contains protobuf encoder/decoder functions. Each field is referenced with wire type and field number:
   ```javascript
   // Example: field 1, wire type 2 (length-delimited), string
   ""!==e.transactionHash && t.uint32(10).string(e.transactionHash)
   // 10 = (1 << 3) | 2  → field 1, wire type 2
   ```

2. **Use protobufjs CLI** to decode the binary messages captured from the browser

3. **Contact Bale** for official API access (unlikely for unofficial client)

### Priority 2: Test Auth Flow
Once protobuf is working:
1. Call `StartPhoneAuth` with a real phone number
2. Capture the response binary
3. Decode to verify message structure
4. Call `ValidateCode` with the SMS code
5. Extract and store the JWT token

### Priority 3: Implement WebSocket Listener
1. Connect to `wss://next-ws.bale.ai`
2. Send authentication handshake
3. Parse incoming `update` messages
4. Map updates to message objects

### Priority 4: File Handling
- File upload uses `Nasim` file storage service
- Methods found: `GetNasimFileUrl`, `GetNasimFileUploadUrl`, `GetNasimFilePublicUrl`
- File upload likely uses HTTP POST with multipart/form-data

---

## Tools for Continued Research

### Browser DevTools
```javascript
// In browser console on web.bale.ai:
// 1. Monitor all network requests
// 2. Inspect WebSocket frames
// 3. Set breakpoints on RPC calls
```

### Playwright Script
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    
    # Monitor network
    page.on("request", lambda req: print(f">>> {req.method} {req.url}"))
    page.on("response", lambda resp: print(f"<<< {resp.status} {resp.url}"))
    
    page.goto("https://web.bale.ai")
    # Enter phone, capture auth requests
```

### Binary Analysis
```bash
# Capture gRPC-Web binary payload
curl -s -X POST https://next-ws.bale.ai/bale.auth.v1.Auth/StartPhoneAuth \
  -H "content-type: application/grpc-web+proto" \
  -H "x-grpc-web: 1" \
  --data-binary @request.bin \
  -o response.bin

# Inspect with xxd
xxd response.bin | head -20
```

---

## Files in This Package

```
bale_grpc_client/
├── __init__.py           # Package exports
├── base_client.py        # HTTP transport, headers, gRPC-Web framing
├── auth_client.py        # bale.auth.v1.Auth methods
├── messaging_client.py   # bale.fanoos.v1.fanoos methods
├── users_client.py       # bale.users.v1.Users methods
├── websocket_client.py   # WSS real-time connection
├── exceptions.py         # Custom exceptions
└── proto/                # (TODO) Protobuf definitions
    ├── auth.proto
    ├── fanoos.proto
    ├── users.proto
    └── ...
```

---

## Research History

- **2026-06-01**: Initial discovery by analyzing web.bale.ai JS bundle
- Extracted 6 services, 80+ methods, WebSocket endpoints, auth flow
- Confirmed: Bale does NOT use MTProto (Telegram protocol) or HTTP Bot API
- Confirmed: Uses custom gRPC-Web + WebSocket hybrid architecture
