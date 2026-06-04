# Instructions for Future Kimi Sessions

## What This Package Is

This is a **reverse-engineered Python client** for Bale Messenger's internal API. Bale does NOT use Telegram's MTProto or a standard HTTP Bot API for user accounts. Instead, it uses:

1. **gRPC-Web over HTTPS** for RPC calls (auth, messaging, user management)
2. **WebSocket (WSS)** for real-time updates (new messages, notifications)

## How to Continue Development

### Option A: Extract Protobuf Definitions from JS Bundle (Recommended)

The web.bale.ai JS bundle contains protobuf encoder/decoder functions. Each field reveals its field number and wire type:

```javascript
// In the JS bundle, search for patterns like:
t.uint32(10).string(e.transactionHash)  // field 1, wire type 2, string
!1!==e.isRegistered && t.uint32(16).bool(e.isRegistered)  // field 2, wire type 0, bool
0!==e.activationType && t.uint32(24).int32(e.activationType)  // field 3, wire type 0, int32
```

Decode field numbers: `uint32(N)` where `N = (field_number << 3) | wire_type`
- Wire type 0 = varint (int32, int64, bool, enum)
- Wire type 2 = length-delimited (string, bytes, embedded messages)

**Steps:**
1. Navigate to `https://web.bale.ai` in Playwright
2. Fetch the JS bundle: `https://web.bale.ai/static/js/index.*.js`
3. Search for encoder functions around each RPC method
4. Build `.proto` files from the extracted field definitions
5. Use `protoc` to generate Python classes

### Option B: Capture and Decode Real Binary Messages

1. Open `https://web.bale.ai` in a real browser (Chrome/Edge)
2. Open DevTools → Network tab
3. Enter a phone number and click "Send Code"
4. Right-click the `StartPhoneAuth` request → "Copy as cURL"
5. Save the request body to a file: `request.bin`
6. Save the response body to a file: `response.bin`
7. Use `protoc --decode_raw < response.bin` to inspect the structure

### Option C: Use protobuf.js to Decode from Browser

In the browser console on web.bale.ai:
```javascript
// The page already loads protobuf.js
// Access the protobuf runtime and inspect message types
console.log(Object.keys(protobuf.roots));  // Find the root namespace
```

## Running the Current Code

```bash
cd C:\Users\amin\Desktop\wootify_instance_manager\bale_grpc_client
pip install -e .
python -c "from bale_grpc_client import BaleAuthClient; print('OK')"
```

## Testing Against Bale

**WARNING**: Use a test phone number. Do NOT use your primary Bale account.

```python
import asyncio
from bale_grpc_client import BaleAuthClient

async def test():
    auth = BaleAuthClient()
    try:
        result = await auth.start_phone_auth("+989123456789")
        print(result)
    except Exception as e:
        print(f"Expected error (protobuf not implemented): {e}")
    await auth.close()

asyncio.run(test())
```

## WebSocket Testing

```python
import asyncio
from bale_grpc_client.websocket_client import BaleWebSocketClient

async def test_ws():
    ws = BaleWebSocketClient(jwt_token="your-jwt-here")
    await ws.connect()
    # Wait for updates...
    await asyncio.sleep(30)
    await ws.close()

asyncio.run(test_ws())
```

## What Was Fixed in the Main Project

The `feature/bale-pv-enterprise` branch in `wootify_instance_manager` has:
- ✅ Frontend UI for Bale PV (phone number, auth buttons)
- ✅ Backend API endpoints for auth
- ✅ Database migration for bale_pv_enterprise platform type
- ❌ Working connector (Balethon doesn't support phone auth)

**The connector needs to be rewritten** to use this gRPC-Web client instead of Balethon.

## Files to Read First

1. `RESEARCH.md` — Complete discovery notes
2. `bale_grpc_client/base_client.py` — Transport layer
3. `bale_grpc_client/auth_client.py` — Auth methods
4. `bale_grpc_client/websocket_client.py` — Real-time updates
