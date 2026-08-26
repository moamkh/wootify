"""
Bale Auth Protobuf Messages
===========================

Message structures reverse-engineered from web.bale.ai JS bundle.
Field numbers and types extracted from protobuf encoder/decoder functions.
"""

from typing import Dict, List, Optional

from .protobuf_wire import ProtobufMessage, ProtobufParser


class StartPhoneAuthRequest:
    """
    Request for bale.auth.v1.Auth/StartPhoneAuth

    Fields (from JS encoder analysis):
      1. phone_number      int64    (tag=8)
      2. app_id            int32    (tag=16)
      3. api_key           string   (tag=26)
      4. device_hash       bytes    (tag=34)
      5. device_title      string   (tag=42)
      6. time_zone         StringValue (tag=50)
      7. preferred_languages repeated string (tag=58)
      8. imei_list         ImeiList? (tag=66)
      9. send_code_type    int32    (tag=72)
     10. options           repeated int32 (tag=80/82)
    """

    def __init__(
        self,
        phone_number: str = "0",
        app_id: int = 0,
        api_key: str = "",
        device_hash: bytes = b"",
        device_title: str = "",
        time_zone: Optional[str] = None,
        preferred_languages: Optional[List[str]] = None,
        imei_list: Optional[bytes] = None,
        send_code_type: int = 0,
        options: Optional[List[int]] = None,
    ):
        self.phone_number = phone_number
        self.app_id = app_id
        self.api_key = api_key
        self.device_hash = device_hash
        self.device_title = device_title
        self.time_zone = time_zone
        self.preferred_languages = preferred_languages or []
        self.imei_list = imei_list
        self.send_code_type = send_code_type
        self.options = options or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, int(self.phone_number))
        msg.add_int32(2, self.app_id)
        msg.add_string(3, self.api_key)
        msg.add_bytes(4, self.device_hash)
        msg.add_string(5, self.device_title)
        if self.time_zone is not None:
            # StringValue wrapper: field 1 = string value
            inner = ProtobufMessage().add_string(1, self.time_zone)
            msg.add_message(6, inner)
        for lang in self.preferred_languages:
            msg.add_string(7, lang)
        if self.imei_list is not None:
            msg.add_bytes(8, self.imei_list)
        msg.add_int32(9, self.send_code_type)
        for opt in self.options:
            msg.add_int32(10, opt)
        return msg.serialize()


class StartPhoneAuthResponse:
    """
    Response from bale.auth.v1.Auth/StartPhoneAuth

    Fields:
      1. transaction_hash          string      (tag=10)
      2. is_registered             bool        (tag=16)
      3. activation_type           int32       (tag=24)
      4. is_imei_ok                bool        (tag=32)
      5. sent_code_type            int32       (tag=40)
      6. code_expiration_date      Timestamp   (tag=50)
      7. next_send_code_type       int32       (tag=56)
      8. next_send_code_wait_time  Timestamp   (tag=66)
      9. code_timeout              Timestamp?  (tag=74)
     10. ex_info_address           repeated string (tag=82)
     11. available_send_code_types repeated int32  (tag=90)
    """

    def __init__(self, data: bytes):
        parser = ProtobufParser(data)
        fields = parser.parse()

        self.transaction_hash = self._get_string(fields, 1)
        self.is_registered = bool(self._get_int(fields, 2, 0))
        self.activation_type = self._get_int(fields, 3, 0)
        self.is_imei_ok = bool(self._get_int(fields, 4, 0))
        self.sent_code_type = self._get_int(fields, 5, 0)
        self.code_expiration_date = self._get_bytes(fields, 6)
        self.next_send_code_type = self._get_int(fields, 7, 0)
        self.next_send_code_wait_time = self._get_bytes(fields, 8)
        self.code_timeout = self._get_bytes(fields, 9)
        self.ex_info_address = [v.decode("utf-8") for v in fields.get(10, [])]
        self.available_send_code_types = [v for v in fields.get(11, [])]

    @staticmethod
    def _get_string(fields: dict, num: int) -> str:
        vals = fields.get(num, [])
        return vals[0].decode("utf-8") if vals else ""

    @staticmethod
    def _get_int(fields: dict, num: int, default: int) -> int:
        vals = fields.get(num, [])
        return vals[0] if vals else default

    @staticmethod
    def _get_bytes(fields: dict, num: int) -> Optional[bytes]:
        vals = fields.get(num, [])
        return vals[0] if vals else None


class ValidateCodeRequest:
    """
    Request for bale.auth.v1.Auth/ValidateCode

    Fields:
      1. transaction_hash   string   (tag=10)
      2. code               string   (tag=18)
      3. is_jwt             BoolValue (tag=26)
      4. future_auth_tokens repeated string (tag=34)
    """

    def __init__(
        self,
        transaction_hash: str = "",
        code: str = "",
        is_jwt: Optional[bool] = None,
        future_auth_tokens: Optional[List[str]] = None,
    ):
        self.transaction_hash = transaction_hash
        self.code = code
        self.is_jwt = is_jwt
        self.future_auth_tokens = future_auth_tokens or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.transaction_hash)
        msg.add_string(2, self.code)
        if self.is_jwt is not None:
            inner = ProtobufMessage().add_bool(1, self.is_jwt)
            msg.add_message(3, inner)
        for token in self.future_auth_tokens:
            msg.add_string(4, token)
        return msg.serialize()


class AuthResult:
    """
    Response containing user, config, and JWT.

    Fields:
      2. user   User        (tag=18)
      3. config Config      (tag=26)
      4. jwt    StringValue (tag=34)
    """

    def __init__(self, data: bytes):
        parser = ProtobufParser(data)
        fields = parser.parse()

        self.user_raw = fields.get(2, [None])[0]
        self.config_raw = fields.get(3, [None])[0]
        self.jwt_raw = fields.get(4, [None])[0]

        # JWT is wrapped in StringValue: field 1 = value
        self.jwt = None
        if self.jwt_raw:
            jwt_parser = ProtobufParser(self.jwt_raw)
            jwt_fields = jwt_parser.parse()
            jwt_vals = jwt_fields.get(1, [])
            if jwt_vals:
                self.jwt = jwt_vals[0].decode("utf-8")
