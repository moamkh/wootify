"""
Bale gRPC-Web Client
====================
A reverse-engineered Python client for Bale Messenger's gRPC-Web/protobuf API.

This package was created by analyzing the Bale web client (web.bale.ai)
JavaScript bundle to extract service definitions, message structures, and
authentication flows.

Services Discovered
-------------------
- bale.auth.v1.Auth       → Authentication (phone auth, JWT tokens)
- bale.fanoos.v1.fanoos   → Messaging (Send, SendBatch)
- bale.users.v1.Users     → User management (contacts, profiles, privacy)
- bale.ramz.v1.Ramz       → Password/security operations
- bale.feedback.v1.FeedBack → Feedback submission
- bale.report.v1.Report   → Content reporting

Architecture
------------
1. Unary RPCs use gRPC-Web over HTTPS to https://next-ws.bale.ai
2. Real-time updates use a custom WebSocket at wss://next-ws.bale.ai
   or wss://maviz-ws.bale.ai
3. The WebSocket carries protobuf messages with fields:
   - response  → RPC responses
   - update    → Server push (new messages, notifications)
   - terminateSession → Force logout

Usage Example
-------------
    from bale_grpc_client import BaleAuthClient

    auth = BaleAuthClient()
    result = await auth.start_phone_auth("+989123456789")
    print(result.transaction_hash)

    validated = await auth.validate_code(
        transaction_hash=result.transaction_hash,
        code="123456"
    )
    print(validated.jwt_token)
"""

__version__ = "0.1.0"

from .auth_client import BaleAuthClient
from .exceptions import BaleRpcError, BaleAuthError, BaleNotImplementedError

__all__ = [
    "BaleAuthClient",
    "BaleRpcError",
    "BaleAuthError",
    "BaleNotImplementedError",
]
