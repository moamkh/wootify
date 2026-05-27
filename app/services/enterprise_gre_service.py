"""
Module Overview
---------------
Purpose: Validates whether a phone number is GRE-eligible.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.models import EnterpriseGreStatus

logger = logging.getLogger("app.services.enterprise_gre")


@dataclass(frozen=True)
class EnterpriseGreValidationResult:
    """Represents the outcome of a GRE lookup."""

    normalized_phone: Optional[str]
    gre_status: EnterpriseGreStatus
    message: str = ""


class GreValidationError(Exception):
    """Raised when the GRE API itself is unreachable or misconfigured."""

    pass


class EnterpriseGreValidator:
    """Validates whether a phone number is GRE-eligible."""

    def __init__(self) -> None:
        """Initialize with a persistent async HTTP client."""
        timeout = httpx.Timeout(
            connect=5.0,
            read=float(settings.ENTERPRISE_GRE_API_TIMEOUT_SECONDS),
            write=5.0,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(timeout=timeout)
        self._backdoor_phones = self._load_backdoor_phones()

    @staticmethod
    def _load_backdoor_phones() -> set[str]:
        raw = str(settings.ENTERPRISE_GRE_BACKDOOR_PHONES or "").strip()
        if not raw:
            return set()
        return {p.strip() for p in raw.split(",") if p.strip()}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def validate_phone(
        self, phone_number: str
    ) -> EnterpriseGreValidationResult:
        """Normalize the submitted phone and classify the GRE lookup result."""
        normalized_phone = self._normalize_phone_number(phone_number)
        if not normalized_phone:
            return EnterpriseGreValidationResult(
                normalized_phone=None,
                gre_status=EnterpriseGreStatus.unknown,
                message="Phone number normalization failed.",
            )

        api_phone = self._normalize_phone_for_apiserver(normalized_phone)
        if not api_phone:
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.unknown,
                message="Phone number could not be formatted for API.",
            )

        # Backdoor / test numbers (configured via settings)
        if api_phone in self._backdoor_phones:
            logger.info("gre_backdoor_phone_used phone=%s", api_phone)
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.eligible,
                message="Backdoor eligibility applied.",
            )

        url = str(settings.ENTERPRISE_GRE_API_URL or "").strip()
        if not url:
            logger.error("gre_api_url_not_configured")
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.unknown,
                message="GRE API URL is not configured.",
            )

        try:
            response = await self._client.post(
                url=url,
                headers={
                    "Content-Type": "application/json",
                },
                json={
                    "mobile": api_phone,
                    "code": "",
                },
            )
            response.raise_for_status()
            response_data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "gre_api_http_error phone=%s status=%s response=%s",
                api_phone,
                exc.response.status_code,
                exc.response.text[:200],
            )
            # If the API itself errors (e.g. 401 auth failure), propagate
            # so callers can decide whether to treat as temporary.
            raise GreValidationError(
                f"GRE API returned {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "gre_api_request_error phone=%s error=%s",
                api_phone,
                str(exc),
            )
            raise GreValidationError(f"GRE API unreachable: {exc}") from exc
        except Exception as exc:
            logger.exception("gre_api_unexpected_error phone=%s", api_phone)
            raise GreValidationError(f"Unexpected GRE error: {exc}") from exc

        status_code = response_data.get("statusCode", 0)
        message = response_data.get("message", "")

        logger.info(
            "gre_api_response phone=%s status_code=%s message=%s",
            api_phone,
            status_code,
            message,
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
        else:
            return EnterpriseGreValidationResult(
                normalized_phone=normalized_phone,
                gre_status=EnterpriseGreStatus.ineligible,
                message=f"Unexpected status code: {status_code}",
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

        digits = re.sub(r"\D", "", normalized)
        if len(digits) == 11:
            return f"0{digits[1:]}"
        elif len(digits) == 12:
            return f"0{digits[2:]}"
        return None
