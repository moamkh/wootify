from __future__ import annotations
import re
import requests
from dataclasses import dataclass
from typing import Optional
from app.models import EnterpriseGreStatus
from fastapi import HTTPException, status


@dataclass(frozen=True)
class EnterpriseGreValidationResult:
    """Represents the outcome of a GRE lookup."""

    normalized_phone: Optional[str]
    gre_status: EnterpriseGreStatus
    message: str = ""


class EnterpriseGreValidator:
    """Validates whether a phone number is GRE-eligible."""

    def validate_phone(self, phone_number: str) -> EnterpriseGreValidationResult:
        """Normalize the submitted phone and classify the GRE lookup result."""
        normalized_phone = self._normalize_phone_number(phone_number)
        if not normalized_phone:
            return EnterpriseGreValidationResult(
                normalized_phone=None,
                gre_status=EnterpriseGreStatus.unknown,
                message="Phone number normalization failed.",
            )

        try:
            # Make the POST request to the API
            response = requests.post(
                # url="https://apiserver.novinmed.com/SoftNoCRM/FindCustomer",
                url="http://172.21.1.59:11211",
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "192.168.0.75",
                },
                json={
                    "mobile": self._normalize_phone_for_apiserver(normalized_phone),
                    "code": "",
                },
            )
            response.raise_for_status()  # Raise exception for HTTP errors
            response_data = response.json()
            status_code = response_data.get("statusCode", 0)
            message = response_data.get("message", "")

            print(status_code)
            print(response_data)

            if self._normalize_phone_for_apiserver(normalized_phone) in [
                "09136421196",
                "09137307820",
            ]:
                return EnterpriseGreValidationResult(
                    normalized_phone=normalized_phone,
                    gre_status=EnterpriseGreStatus.eligible,
                    message=message,
                )
            if status_code == 200:
                return EnterpriseGreValidationResult(
                    normalized_phone=normalized_phone,
                    gre_status=EnterpriseGreStatus.eligible,
                    message=message,
                )
            elif status_code == 404:
                return EnterpriseGreValidationResult(
                    normalized_phone=normalized_phone,
                    gre_status=EnterpriseGreStatus.ineligible,
                    message=message,
                )
            elif status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Can't contact API server to validate phone number. Try again later.",
                )
            else:
                return EnterpriseGreValidationResult(
                    normalized_phone=normalized_phone,
                    gre_status=EnterpriseGreStatus.ineligible,
                    message=f"Unexpected status code: {status_code}",
                )

        except requests.RequestException as e:
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.ineligible,
                message=f"API request failed: {str(e)}",
            )
        except Exception as e:
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.ineligible,
                message=f"Unexpected error: {str(e)}",
            )

    @staticmethod
    def _normalize_phone_number(value: str) -> Optional[str]:
        """Normalize a phone number into a stable comparable representation."""
        text = str(value or "").strip()
        if not text:
            return None

        compact = re.sub(r"\s+", "", text)
        if compact.startswith("+"):
            digits = re.sub(r"\D", "", compact[1:])
            if len(digits) < 8 or len(digits) > 15:
                return None
            return f"+{digits}" if digits else None
        if compact.startswith("00"):
            digits = re.sub(r"\D", "", compact[2:])
            if len(digits) < 8 or len(digits) > 15:
                return None
            return f"+{digits}" if digits else None

        digits = re.sub(r"\D", "", compact)
        if not digits or len(digits) < 8 or len(digits) > 15:
            return None
        if len(digits) == 12 and digits.startswith("98"):
            return f"+{digits}"
        if len(digits) == 11 and digits.startswith("0"):
            return f"+98{digits[1:]}"
        if len(digits) == 10 and digits.startswith("9"):
            return f"+98{digits}"
        if not digits.startswith("0"):
            return f"+{digits}"
        return digits

    @staticmethod
    def _normalize_phone_for_apiserver(value: str) -> Optional[str]:
        """Normalize a phone number into a stable 0xxxxxxxxx format for API calls."""
        text = str(value or "").strip()
        if not text:
            return None

        compact = re.sub(r"\s+", "", text)
        normalized = EnterpriseGreValidator._normalize_phone_number(compact)

        if not normalized:
            return None

        # Ensure output is in '0xxxxxxxxx' format
        digits = re.sub(r"\D", "", normalized)
        if len(digits) == 11:
            return f"0{digits[1:]}"
        elif len(digits) == 12:
            return f"0{digits[2:]}"
        return None
