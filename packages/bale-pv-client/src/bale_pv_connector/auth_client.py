"""
Bale Authentication Client
==========================
Implements phone-number authentication flow discovered from web.bale.ai.

Auth Flow
---------
1. StartPhoneAuth(phoneNumber, deviceTitle, sendCodeType, apiKey, appId, deviceHash, ...)
   → returns {transactionHash, isRegistered, activationType, sentCodeType, ...}

2. ValidateCode(transactionHash, code, isJwt=True, futureAuthTokens=[])
   → returns {user, jwt, ...}

3. GetJWTToken(...) → refresh or obtain new JWT

Endpoints
---------
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/StartPhoneAuth
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/ValidateCode
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/ValidatePassword
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/SignUp
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetAuthSessions
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/TerminateSession
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/TerminateAllSessions
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/SignOut
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/LogOut
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/DeleteAccount
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/ChangePhone
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/SendDeleteAccountVerificationCode
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/SendChangePhoneVerificationCode
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetUserIdToken
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetTicket
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetBajeBamTicket
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetBaleTicket
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/GetJWTToken
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/EnableTwoFactorAuthentication
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/IsTwoFactorAuthenticationEnabled
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/VerifyEmail
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/RecoverPassword
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/VerifyPasswordRecovery
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/SetNewPassword
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/VerifyPassword
- POST https://next-ws.bale.ai/bale.auth.v1.Auth/DisableTwoFactorAuthentication
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional

import httpx

from .base_client import BaleBaseClient, RpcMethod
from .exceptions import BaleAuthError, BaleNotImplementedError
from .protobuf_wire import grpc_web_frame, parse_grpc_web_response
from .auth_messages import StartPhoneAuthRequest, StartPhoneAuthResponse, ValidateCodeRequest, AuthResult

logger = logging.getLogger("bale_pv_connector.auth")


class BaleAuthClient(BaleBaseClient):
    """Client for Bale authentication service (bale.auth.v1.Auth)."""

    SERVICE = "bale.auth.v1.Auth"

    # API key discovered from web.bale.ai JS bundle
    # These may need to be updated if Bale rotates them
    DEFAULT_API_KEY = "C28D46DC4C3A7A26564BFCC48B929086A95C93C98E789A19847BEE8627DE4E7D"
    DEFAULT_APP_ID = "f9283dba-0645-483a-9ca8-b87c1d0f0344"

    def _method(self, name: str) -> RpcMethod:
        return RpcMethod(service_name=self.SERVICE, method_name=name)

    def _generate_device_hash(self, phone: str) -> bytes:
        """Generate a device hash similar to the web client.

        The web client uses (0,A.zC)(n) which hashes some device identifier.
        We use a deterministic 32-byte SHA256 hash truncated to match expected size.
        """
        data = f"{phone}:{self.session_id}:desktop_client"
        return hashlib.sha256(data.encode()).digest()

    async def start_phone_auth(
        self,
        phone_number: str,
        device_title: str = "Desktop Client",
        send_code_type: int = 0,
        api_key: Optional[str] = None,
        app_id: Optional[str] = None,
        preferred_languages: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Step 1: Request SMS code for phone authentication.

        Args:
            phone_number: Phone number in international format (e.g. 9891...)
            device_title: Device name shown in auth sessions
            send_code_type: Code delivery method (0 = SMS)
            api_key: Bale API key (uses default if not provided)
            app_id: Bale app ID (uses default if not provided)
            preferred_languages: List of preferred language codes

        Returns:
            Dict with transactionHash, isRegistered, activationType, etc.

        Raises:
            BaleAuthError: If the phone number is invalid or rate limited
        """
        api_key = api_key or self.DEFAULT_API_KEY
        app_id_val = app_id or self.DEFAULT_APP_ID

        # Convert app_id UUID to int if needed, or keep as string
        # The JS sends app_id as a UUID string but the protobuf field is int32
        # Looking at the JS: appId is passed as `a.id` which might be an int
        # For now try 0 since the web client sends app_id=0 in some cases
        try:
            app_id_int = int(app_id_val)
        except (ValueError, TypeError):
            app_id_int = 0

        device_hash = self._generate_device_hash(phone_number)

        req = StartPhoneAuthRequest(
            phone_number=phone_number,
            app_id=app_id_int,
            api_key=api_key,
            device_hash=device_hash,
            device_title=device_title,
            send_code_type=send_code_type,
            preferred_languages=preferred_languages or [],
        )

        payload = grpc_web_frame(req.serialize())
        url = f"{self.host}/{self.SERVICE}/StartPhoneAuth"
        headers = self._build_headers()

        logger.debug("StartPhoneAuth -> %s", phone_number)
        response = await self._client.post(url, headers=headers, content=payload)

        msg, grpc_status, grpc_message = parse_grpc_web_response(response.content)

        # Fallback: check HTTP headers for grpc-status if not found in body trailers
        if grpc_status == 0 and not msg:
            grpc_status = int(response.headers.get("grpc-status", "0"))
            grpc_message = response.headers.get("grpc-message", "")

        if grpc_status != 0:
            raise BaleAuthError(f"StartPhoneAuth failed: {grpc_message} (status={grpc_status})")

        if not msg:
            raise BaleAuthError("StartPhoneAuth returned empty response")

        resp = StartPhoneAuthResponse(msg)
        return {
            "ok": True,
            "transaction_hash": resp.transaction_hash,
            "is_registered": resp.is_registered,
            "activation_type": resp.activation_type,
            "sent_code_type": resp.sent_code_type,
            "code_expiration_date": resp.code_expiration_date,
            "next_send_code_type": resp.next_send_code_type,
            "next_send_code_wait_time": resp.next_send_code_wait_time,
            "code_timeout": resp.code_timeout,
            "ex_info_address": resp.ex_info_address,
            "available_send_code_types": resp.available_send_code_types,
        }

    async def validate_code(
        self,
        transaction_hash: str,
        code: str,
        is_jwt: bool = True,
        future_auth_tokens: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Step 2: Validate SMS code received on the phone.

        Args:
            transaction_hash: Hash from start_phone_auth response
            code: SMS code entered by user
            is_jwt: Request JWT token in response
            future_auth_tokens: List of future auth tokens

        Returns:
            Dict with user info and JWT token
        """
        req = ValidateCodeRequest(
            transaction_hash=transaction_hash,
            code=code,
            is_jwt=is_jwt,
            future_auth_tokens=future_auth_tokens or [],
        )

        payload = grpc_web_frame(req.serialize())
        url = f"{self.host}/{self.SERVICE}/ValidateCode"
        headers = self._build_headers()

        logger.debug("ValidateCode -> transaction=%s", transaction_hash)
        response = await self._client.post(url, headers=headers, content=payload)

        msg, grpc_status, grpc_message = parse_grpc_web_response(response.content)

        # Fallback: check HTTP headers for grpc-status if not found in body trailers
        if grpc_status == 0 and not msg:
            grpc_status = int(response.headers.get("grpc-status", "0"))
            grpc_message = response.headers.get("grpc-message", "")

        if grpc_status != 0:
            raise BaleAuthError(f"ValidateCode failed: {grpc_message} (status={grpc_status})")

        if not msg:
            raise BaleAuthError("ValidateCode returned empty response")

        result = AuthResult(msg)
        return {
            "ok": True,
            "jwt": result.jwt,
            "user_raw": result.user_raw,
            "config_raw": result.config_raw,
        }

    async def get_jwt_token(self) -> Dict[str, Any]:
        """Get or refresh JWT token for the current session."""
        raise BaleNotImplementedError(
            "get_jwt_token requires protobuf serialization."
        )

    async def sign_out(self) -> Dict[str, Any]:
        """Sign out from current session."""
        raise BaleNotImplementedError(
            "sign_out requires protobuf serialization."
        )

    async def get_auth_sessions(self) -> Dict[str, Any]:
        """List all active authentication sessions."""
        raise BaleNotImplementedError(
            "get_auth_sessions requires protobuf serialization."
        )
