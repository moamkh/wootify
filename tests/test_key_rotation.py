"""Tests for one-shot encryption key rotation (app/utils/key_rotation.py)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Instance
from app.utils.crypto_utils import JsonEncryptor
from app.utils.key_rotation import rotate_instance_encryption


def _session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_instance(db, encryptor: JsonEncryptor, key: str = 'k1') -> Instance:
    row = Instance(
        instance_key=key,
        platform_type_id='pt-1',
        is_enabled=True,
        platform_metadata_encrypted=encryptor.encrypt_json({'token': 'abc'}),
        chatwoot_config_encrypted=encryptor.encrypt_json({'api_token': 'xyz'}),
        proxy_config_encrypted='',
    )
    db.add(row)
    db.commit()
    return row


def test_rotates_old_key_rows_to_current_key():
    old = JsonEncryptor(Fernet.generate_key().decode())
    new = JsonEncryptor(Fernet.generate_key().decode())
    db = _session()
    _make_instance(db, old)

    stats = rotate_instance_encryption(db, new, old)

    assert stats == {'rotated': 2, 'already_current': 0, 'undecryptable': 0}
    row = db.query(Instance).one()
    assert new.decrypt_json(row.platform_metadata_encrypted) == {'token': 'abc'}
    assert new.decrypt_json(row.chatwoot_config_encrypted) == {'api_token': 'xyz'}
    with pytest.raises(Exception):
        old.decrypt_json(row.platform_metadata_encrypted)


def test_skips_rows_already_on_current_key_and_is_idempotent():
    old = JsonEncryptor(Fernet.generate_key().decode())
    new = JsonEncryptor(Fernet.generate_key().decode())
    db = _session()
    _make_instance(db, new, key='new-row')
    _make_instance(db, old, key='old-row')

    first = rotate_instance_encryption(db, new, old)
    assert first['rotated'] == 2
    assert first['already_current'] == 2

    second = rotate_instance_encryption(db, new, old)
    assert second == {'rotated': 0, 'already_current': 4, 'undecryptable': 0}


def test_undecryptable_values_are_left_untouched():
    old = JsonEncryptor(Fernet.generate_key().decode())
    wrong = JsonEncryptor(Fernet.generate_key().decode())
    new = JsonEncryptor(Fernet.generate_key().decode())
    db = _session()
    row = _make_instance(db, old)
    original_token = row.platform_metadata_encrypted

    stats = rotate_instance_encryption(db, new, wrong)

    assert stats['undecryptable'] == 2
    assert stats['rotated'] == 0
    db.expire_all()
    assert db.query(Instance).one().platform_metadata_encrypted == original_token


def test_dev_fallback_sentinel_resolves_to_dev_key():
    from app.utils.crypto_utils import DEV_FALLBACK_SENTINEL, _development_fallback_key

    dev = JsonEncryptor(DEV_FALLBACK_SENTINEL)
    assert dev._fernet._signing_key == JsonEncryptor(_development_fallback_key())._fernet._signing_key
    payload = dev.encrypt_json({'a': 1})
    assert JsonEncryptor(_development_fallback_key()).decrypt_json(payload) == {'a': 1}
