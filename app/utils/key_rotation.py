"""
Module Overview
---------------
Purpose: One-shot encryption key rotation for secrets stored in the database.

When DATA_ENCRYPTION_KEY changes, rows written under the old key can no longer
be decrypted. Setting DATA_ENCRYPTION_KEY_PREVIOUS to the old key (or the
sentinel 'dev-fallback' for the deterministic development key) lets startup
re-encrypt those rows with the current key exactly once. Fernet is
authenticated, so decrypting with the wrong key always raises — the fallback
per row is safe and the pass is idempotent. Remove DATA_ENCRYPTION_KEY_PREVIOUS
from the environment after a successful rotation.

Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Instance
from app.utils.crypto_utils import JsonEncryptor

logger = logging.getLogger('app.utils.key_rotation')

#: Encrypted columns on the instances table that store JSON payloads.
ENCRYPTED_INSTANCE_COLUMNS = (
    'platform_metadata_encrypted',
    'chatwoot_config_encrypted',
    'proxy_config_encrypted',
)


def rotate_instance_encryption(
    db: Session,
    current: JsonEncryptor,
    previous: JsonEncryptor,
) -> dict[str, Any]:
    """Re-encrypt instance config columns from the previous key to the current one.

    For every non-empty encrypted column: if the current key already decrypts
    it, the row is left untouched; otherwise the previous key is tried and, on
    success, the value is re-encrypted with the current key. Values that
    neither key can decrypt are logged (without the ciphertext) and left
    as-is so a later attempt with the correct previous key can still fix them.
    """
    stats: dict[str, Any] = {'rotated': 0, 'already_current': 0, 'undecryptable': 0}
    dirty = False

    for row in db.query(Instance).all():
        for column in ENCRYPTED_INSTANCE_COLUMNS:
            token = getattr(row, column, '') or ''
            if not token:
                continue
            try:
                current.decrypt_json(token)
                stats['already_current'] += 1
                continue
            except Exception:
                pass
            try:
                payload = previous.decrypt_json(token)
            except Exception:
                stats['undecryptable'] += 1
                logger.error(
                    'key_rotation: undecryptable value instance_key=%s column=%s',
                    row.instance_key,
                    column,
                )
                continue
            setattr(row, column, current.encrypt_json(payload))
            stats['rotated'] += 1
            dirty = True

    if dirty:
        db.commit()
    logger.info(
        'key_rotation: rotated=%s already_current=%s undecryptable=%s',
        stats['rotated'],
        stats['already_current'],
        stats['undecryptable'],
    )
    return stats
