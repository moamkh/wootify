"""Filename/content-type normalization used by both bridge directions."""
from __future__ import annotations

import mimetypes
import os.path
from typing import Optional
from urllib.parse import urlparse


class MediaNormalizer:
    """Normalize media metadata without performing I/O."""

    _extensions = {
        "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "video/mp4": ".mp4",
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    }

    @staticmethod
    def attachment_filename(attachment: dict, data_url: str) -> str:
        """Return a Chatwoot attachment filename with a usable extension."""
        base_name = str(attachment.get("file_name") or attachment.get("filename") or "").strip() or "file"
        name, ext = os.path.splitext(base_name)
        if not ext:
            _, url_ext = os.path.splitext(urlparse(data_url).path)
            if url_ext and "." in url_ext:
                ext = url_ext
            else:
                content_type = str(attachment.get("content_type") or attachment.get("file_type") or "").strip()
                ext = mimetypes.guess_extension(content_type) if content_type else ""
        ext = ext.strip()
        if ext == ".": ext = ""
        return f"{name}{ext}" if ext else name

    @classmethod
    def preferred_extension(cls, content_type: str) -> Optional[str]:
        return cls._extensions.get(str(content_type or "").strip().lower())

    @staticmethod
    def guess_content_type(content: bytes) -> Optional[str]:
        if not content:
            return None
        if content.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
        if content.startswith(b"\xff\xd8\xff"): return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")): return "image/gif"
        if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP": return "image/webp"
        if content.startswith(b"OggS"): return "audio/ogg"
        if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE": return "audio/wav"
        if content.startswith(b"ID3") or (len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0): return "audio/mpeg"
        if len(content) > 8 and content[4:8] == b"ftyp": return "video/mp4"
        return None

    @classmethod
    def for_chatwoot(cls, *, filename: Optional[str], content_type: Optional[str], content: bytes) -> tuple[str, Optional[str]]:
        name = str(filename or "").strip() or "file"
        ctype = str(content_type or "").strip().lower()
        if not ctype or ctype == "application/octet-stream":
            ctype = (mimetypes.guess_type(name)[0] or cls.guess_content_type(content) or ctype).lower()
        if "." not in name.rsplit("/", 1)[-1] and ctype:
            ext = cls.preferred_extension(ctype) or (mimetypes.guess_extension(ctype) or "")
            if ext: name = f"{name}{ext}"
        return name, ctype or None

    @classmethod
    def for_platform(cls, filename: Optional[str], content_type: Optional[str], url: Optional[str]) -> str:
        name = str(filename or "").strip() or "file"
        if "." in name.rsplit("/", 1)[-1]: return name
        ctype = str(content_type or "").strip().lower()
        ext = cls.preferred_extension(ctype) or (mimetypes.guess_extension(ctype) or "") if ctype else ""
        if not ext and url:
            guessed = mimetypes.guess_type(url)[0]
            ext = cls.preferred_extension(guessed) or (mimetypes.guess_extension(guessed) or "") if guessed else ""
        return f"{name}{ext}" if ext else name
