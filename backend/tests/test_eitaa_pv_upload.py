from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wootify.plugins.eitaa_pv.connector import EitaaPvConnector


@pytest.mark.asyncio
@pytest.mark.parametrize('name,mime,constructor', [
    ('photo.jpg', 'image/jpeg', 'inputMediaUploadedPhoto'),
    ('document.pdf', 'application/pdf', 'inputMediaUploadedDocument'),
])
async def test_uploaded_media_is_finalized_on_upload_transport(tmp_path, name, mime, constructor):
    path = tmp_path / name
    path.write_bytes(b'attachment' * 5000)
    peer = {'_': 'inputPeerUser', 'user_id': 1, 'access_hash': 0}
    calls = []

    async def invoke(method, params, *, kind='client'):
        calls.append((method, params, kind))
        if method.startswith('upload.'):
            return True
        # The regular messaging host cannot see the uploaded parts.
        assert kind == 'upload'
        return {'_': 'updates'}

    runtime = SimpleNamespace(client=SimpleNamespace(
        peers=SimpleNamespace(resolve=AsyncMock(return_value=peer)), invoke=invoke,
    ))
    await EitaaPvConnector._send_media_with_checksum(
        runtime, 'user:1:0', path, content_type=mime, caption='', reply_to=None,
    )
    assert len(calls) == 3  # two parts, one finalization
    assert all(kind == 'upload' for _, _, kind in calls)
    assert calls[-1][0] == 'messages.sendMedia'
    assert calls[-1][1]['media']['_'] == constructor
    assert calls[-1][1]['media']['file']['id'] == calls[0][1]['file_id']
