"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger("app.utils.logging")


def redact_secret(secret: Optional[str], keep_start: int = 4, keep_end: int = 4) -> str:
    """Redact secret."""
    if not secret:
        return ""
    secret = str(secret)
    if len(secret) <= keep_start + keep_end + 3:
        return "***"
    return f"{secret[:keep_start]}***{secret[-keep_end:]}"


def log_sms_to_file(
    phone_number: str,
    text: str,
    status: str,
    sms_id: Optional[int] = None,
    output_dir: Optional[Path] = None,
) -> None:
    """
    Log SMS message to filesystem.

    Args:
        phone_number: Normalized phone number (e.g., '989123456789')
        text: SMS message text
        status: Send status ('sent', 'failed', 'dropped', etc.)
        sms_id: Optional SMS ID for tracking
        output_dir: Output directory path. If None, uses data/tmp-enterprise-smoke

    Creates directory structure: output_dir/ph_<phonenumber>/
    Creates file: <timestamp>_<sms_id>.txt with format:
        <sms_text>

        --- Status: <status> ---
    """
    if not output_dir:
        from app.config import settings
        root_dir = Path(__file__).resolve().parents[2]  # Go to repo root
        output_dir = root_dir / "data" / "tmp-enterprise-smoke"

    # Create phone-specific folder
    phone_folder = output_dir / f"ph_{phone_number}"
    try:
        phone_folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(
            "sms_file_log.mkdir_failed phone=%s output_dir=%s error=%s",
            phone_number,
            output_dir,
            str(e),
        )
        return

    # Generate filename with timestamp and SMS ID
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sms_id_str = f"_{sms_id}" if sms_id is not None else ""
    filename = f"{timestamp}{sms_id_str}.txt"
    filepath = phone_folder / filename

    # Build content
    content = f"{text}\n\n--- Status: {status} ---"

    # Write to file
    try:
        filepath.write_text(content, encoding="utf-8")
        logger.debug(
            "sms_file_log.written phone=%s filename=%s status=%s",
            phone_number,
            filename,
            status,
        )
    except Exception as e:
        logger.error(
            "sms_file_log.write_failed phone=%s filename=%s error=%s",
            phone_number,
            filename,
            str(e),
        )


def truncate_text(text: Optional[str], max_len: int) -> str:
    """Truncate text."""
    if text is None:
        return ""
    text = str(text)
    if max_len <= 0 or len(text) <= max_len:
        return text
    return f"{text[:max_len]}...(truncated, len={len(text)})"


