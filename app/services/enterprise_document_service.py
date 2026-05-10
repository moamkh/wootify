"""
Module Overview
---------------
Purpose: Service-layer business logic for enterprise Bale asset storage and metadata.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import repo_root
from app.models import EnterpriseDocumentAsset, EnterpriseDocumentAssetType
from app.repositories.enterprise_document_asset_repository import EnterpriseDocumentAssetRepository
from app.services.instance_service import InstanceService

logger = logging.getLogger('app.services.enterprise_documents')


class EnterpriseDocumentService:
    """Service for enterprise document asset workflows."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self._instances = InstanceService()
        self._repo_cls = EnterpriseDocumentAssetRepository
        self._storage_root = repo_root / 'data' / 'enterprise_assets'

    def list_manuals(self, db: Session, instance_key: str) -> list[EnterpriseDocumentAsset]:
        """List enterprise manuals for an instance."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        return self._repo_cls(db).list_for_instance(runtime.instance.id, asset_type=EnterpriseDocumentAssetType.manual)

    def get_catalog(self, db: Session, instance_key: str) -> Optional[EnterpriseDocumentAsset]:
        """Get the active enterprise catalog for an instance."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        return self._repo_cls(db).get_active_catalog(runtime.instance.id)

    async def upload_manual(
        self,
        db: Session,
        instance_key: str,
        *,
        display_name: str,
        link_url: Optional[str],
        upload: UploadFile,
    ) -> EnterpriseDocumentAsset:
        """Upload a new manual asset."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        normalized_link_url = self._normalize_optional_link_url(link_url)
        filename = self._safe_filename(upload.filename)
        if not filename:
            raise ValueError('manual file is required')
        content, resolved_content_type = await self._read_and_validate_pdf(upload, filename)

        relative_path = self._write_file(
            instance_key,
            folder='manuals',
            filename=filename,
            content=content,
        )
        row = EnterpriseDocumentAsset(
            instance_id=runtime.instance.id,
            asset_type=EnterpriseDocumentAssetType.manual,
            display_name=str(display_name or '').strip() or Path(filename).stem,
            link_url=normalized_link_url,
            storage_path=relative_path.as_posix(),
            original_filename=filename,
            content_type=resolved_content_type,
            size_bytes=len(content),
            sort_order=self._repo_cls(db).next_sort_order(runtime.instance.id, EnterpriseDocumentAssetType.manual),
            is_active=True,
        )
        self._repo_cls(db).save(row)
        db.commit()
        db.refresh(row)
        return row

    async def replace_catalog(
        self,
        db: Session,
        instance_key: str,
        *,
        display_name: Optional[str],
        link_url: str,
        upload: UploadFile,
    ) -> EnterpriseDocumentAsset:
        """Replace the active enterprise catalog."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        normalized_link_url = self._normalize_required_link_url(link_url)
        filename = self._safe_filename(upload.filename)
        if not filename:
            raise ValueError('catalog file is required')
        content, resolved_content_type = await self._read_and_validate_pdf(upload, filename)

        repo = self._repo_cls(db)
        existing = repo.get_active_catalog(runtime.instance.id)
        relative_path = self._write_file(
            instance_key,
            folder='catalog',
            filename=filename,
            content=content,
        )

        repo.deactivate_catalogs(runtime.instance.id)
        row = EnterpriseDocumentAsset(
            instance_id=runtime.instance.id,
            asset_type=EnterpriseDocumentAssetType.catalog,
            display_name=str(display_name or '').strip() or Path(filename).stem,
            link_url=normalized_link_url,
            storage_path=relative_path.as_posix(),
            original_filename=filename,
            content_type=resolved_content_type,
            size_bytes=len(content),
            sort_order=1,
            is_active=True,
        )
        repo.save(row)
        db.commit()
        db.refresh(row)

        if existing:
            self._delete_file_quietly(existing.storage_path)
        return row

    def delete_asset(self, db: Session, instance_key: str, asset_id: str) -> bool:
        """Delete a manual or catalog asset."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        row = self._repo_cls(db).get_by_id(asset_id)
        if not row or row.instance_id != runtime.instance.id:
            return False
        storage_path = row.storage_path
        db.delete(row)
        db.commit()
        self._delete_file_quietly(storage_path)
        return True

    def update_manual_metadata(
        self,
        db: Session,
        instance_key: str,
        asset_id: str,
        *,
        display_name: Optional[str] = None,
        link_url: Optional[str] = None,
    ) -> Optional[EnterpriseDocumentAsset]:
        """Update manual display name and/or link URL without changing file content."""
        runtime = self._require_enterprise_runtime(db, instance_key)
        row = self._repo_cls(db).get_by_id(asset_id)
        if not row or row.instance_id != runtime.instance.id:
            return None
        if row.asset_type != EnterpriseDocumentAssetType.manual:
            raise ValueError('asset is not a manual')

        changed = False
        if display_name is not None:
            normalized_name = str(display_name).strip()
            if not normalized_name:
                raise ValueError('display_name cannot be empty')
            row.display_name = normalized_name
            changed = True

        if link_url is not None:
            row.link_url = self._normalize_optional_link_url(link_url)
            changed = True

        if not changed:
            raise ValueError('nothing to update')

        self._repo_cls(db).save(row)
        db.commit()
        db.refresh(row)
        return row

    def read_asset_bytes(self, db: Session, asset_id: str) -> tuple[EnterpriseDocumentAsset, bytes]:
        """Load an asset payload from storage."""
        row = self._repo_cls(db).get_by_id(asset_id)
        if not row:
            raise ValueError('asset not found')
        file_path = self._storage_root / Path(str(row.storage_path or '').replace('\\', '/'))
        if not file_path.exists():
            raise FileNotFoundError(f'enterprise asset file missing: {file_path}')
        return row, file_path.read_bytes()

    ENTERPRISE_PLATFORM_KEYS = {'bale_enterprise', 'telegram_enterprise'}

    def _require_enterprise_runtime(self, db: Session, instance_key: str):
        """Require an enterprise runtime (Bale or Telegram)."""
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if not runtime:
            raise ValueError('instance not found')
        if str(runtime.platform_type.key or '').strip().lower() not in self.ENTERPRISE_PLATFORM_KEYS:
            raise ValueError('instance is not an enterprise instance')
        return runtime

    def _write_file(self, instance_key: str, *, folder: str, filename: str, content: bytes) -> Path:
        """Persist an uploaded enterprise asset to local storage."""
        target_dir = self._storage_root / str(instance_key).strip() / str(folder).strip()
        target_dir.mkdir(parents=True, exist_ok=True)
        unique_name = f'{uuid.uuid4().hex}_{filename}'
        target_path = target_dir / unique_name
        target_path.write_bytes(content)
        logger.info(
            'enterprise asset stored instance_key=%s folder=%s filename=%s bytes=%s path=%s',
            instance_key,
            folder,
            filename,
            len(content),
            target_path,
        )
        return target_path.relative_to(self._storage_root)

    @staticmethod
    def _safe_filename(value: Optional[str]) -> str:
        """Sanitize a client-supplied filename."""
        raw = Path(str(value or '').strip()).name
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', raw)
        return safe.strip('._') or 'document.pdf'

    async def _read_and_validate_pdf(self, upload: UploadFile, filename: str) -> tuple[bytes, str]:
        """Read and validate an uploaded PDF file."""
        try:
            await upload.seek(0)
        except Exception:
            logger.debug('failed to seek upload stream before reading filename=%s', filename, exc_info=True)

        content = await upload.read()
        resolved_content_type = self._validate_pdf(
            content,
            filename,
            declared_content_type=str(upload.content_type or '').strip() or None,
        )
        return content, resolved_content_type

    @staticmethod
    def _validate_pdf(
        content: bytes,
        filename: str,
        *,
        declared_content_type: Optional[str] = None,
    ) -> str:
        """Ensure the uploaded file looks like a PDF."""
        name = str(filename or '').strip().lower()
        if not name.endswith('.pdf'):
            raise ValueError('only PDF files are supported')
        if not content:
            raise ValueError('uploaded file is empty')

        signature_index = content[:1024].find(b'%PDF-')
        if signature_index < 0:
            logger.warning(
                'enterprise pdf validation failed filename=%s content_type=%s size_bytes=%s header_sample=%s',
                filename,
                declared_content_type,
                len(content),
                content[:32].hex(),
            )
            raise ValueError('uploaded file is not a valid PDF')

        if signature_index > 0:
            logger.info(
                'enterprise pdf validation accepted preamble filename=%s content_type=%s preamble_bytes=%s',
                filename,
                declared_content_type,
                signature_index,
            )

        normalized_content_type = str(declared_content_type or '').strip().lower()
        if normalized_content_type in {'application/pdf', 'application/x-pdf'}:
            return normalized_content_type
        return 'application/pdf'

    def _delete_file_quietly(self, storage_path: Optional[str]) -> None:
        """Delete a stored asset file if it exists."""
        if not storage_path:
            return
        try:
            file_path = self._storage_root / Path(str(storage_path).replace('\\', '/'))
            if file_path.exists():
                file_path.unlink()
        except Exception:
            logger.exception('failed to delete enterprise asset storage_path=%s', storage_path)

    @staticmethod
    def _normalize_required_link_url(value: Optional[str]) -> str:
        """Validate and normalize required HTTP(S) link values."""
        text = str(value or '').strip()
        if not text:
            raise ValueError('link_url is required')
        parsed = urlparse(text)
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('link_url must be a valid absolute http(s) URL')
        return text

    @staticmethod
    def _normalize_optional_link_url(value: Optional[str]) -> str:
        """Validate and normalize optional HTTP(S) link values."""
        text = str(value or '').strip()
        if not text:
            return ''
        parsed = urlparse(text)
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('link_url must be a valid absolute http(s) URL')
        return text
