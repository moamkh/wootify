"""
Module Overview
---------------
Purpose: HTTP client wrappers for Novin SMS API polling.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.utils.logging_utils import redact_secret, truncate_text

logger = logging.getLogger("app.services.enterprise_sms")


class NovinSmsClient:
    """Client for Novin SMS incremental polling endpoint."""

    @staticmethod
    def _build_last_id_endpoint(base_url: str, last_id: int) -> str:
        """Build endpoint URL by injecting LastId as a path segment."""
        clean_url = str(base_url or "").strip().rstrip("/")
        last_id_text = str(int(last_id))

        if "<LastId>" in clean_url:
            return clean_url.replace("<LastId>", last_id_text)
        if clean_url.endswith("/LastId"):
            return f"{clean_url.rsplit('/', 1)[0]}/{last_id_text}"
        return f"{clean_url}/{last_id_text}"

    async def fetch_since(
        self,
        *,
        url: str,
        last_id: int,
        token: str = "",
        token_header: str = "Authorization",
        token_prefix: str = "Bearer",
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """Fetch SMS rows from the upstream Novin API starting from the supplied id boundary."""
        raw_endpoint = str(url or "").strip()
        if not raw_endpoint:
            raise ValueError("enterprise_sms_api_url is required")

        try:
            last_id_value = int(last_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("enterprise_sms_last_id must be an integer") from exc

        headers: dict[str, str] = {"Accept": "application/json"}
        # Some admin copy/paste flows can persist JWTs with internal whitespace/newlines.
        raw_token = "".join(str(token or "").split())
        header_name = str(token_header or "Authorization").strip() or "Authorization"
        prefix = str(token_prefix or "").strip()
        if raw_token:
            headers[header_name] = f"{prefix} {raw_token}".strip()

        endpoint = self._build_last_id_endpoint(raw_endpoint, last_id_value)

        safe_token = redact_secret(raw_token) if raw_token else ""
        safe_headers = dict(headers)
        if raw_token and header_name in safe_headers:
            safe_headers[header_name] = safe_headers[header_name].replace(raw_token, safe_token)

        logger.info(
            "novin_sms.fetch start method=GET url=%s source_url=%s last_id=%s last_id_type=%s timeout_seconds=%s headers=%s",
            endpoint,
            raw_endpoint,
            last_id_value,
            type(last_id).__name__,
            max(5, int(timeout_seconds)),
            safe_headers,
        )

        try:
            async with httpx.AsyncClient(timeout=max(5, int(timeout_seconds))) as client:
                response = await client.get(endpoint, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body_preview = truncate_text(exc.response.text if exc.response is not None else "", 600)
            logger.error(
                "novin_sms.fetch http_status_error url=%s status=%s response=%s",
                endpoint,
                exc.response.status_code if exc.response is not None else None,
                body_preview,
            )
            raise
        except httpx.RequestError as exc:
            logger.error(
                "novin_sms.fetch request_error url=%s error=%s",
                endpoint,
                str(exc),
            )
            raise

        logger.info(
            "novin_sms.fetch response status=%s content_type=%s",
            response.status_code,
            response.headers.get("content-type"),
        )
        logger.debug("novin_sms.fetch response_body=%s", truncate_text(response.text, 600))

        if not isinstance(data, dict):
            raise RuntimeError("novin sms api returned invalid JSON payload")

        succeeded = bool(data.get("succeeded"))
        if not succeeded:
            message = str(data.get("message") or "novin sms api request failed").strip()
            raise RuntimeError(message)

        rows = data.get("data")
        if rows is None:
            data["data"] = []
        elif not isinstance(rows, list):
            raise RuntimeError("novin sms api returned invalid data field")

        logger.info(
            "novin_sms.fetch done succeeded=%s rows=%s message=%s",
            succeeded,
            len(data.get("data") or []),
            str(data.get("message") or "").strip(),
        )

        return data
