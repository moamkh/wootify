import asyncio
from unittest.mock import patch

import pytest

from bale_pv_connector.ws_client import BaleWebSocketClient
from wootify.application.messaging.polling import BalePollingService


@pytest.mark.asyncio
async def test_close_fails_rpc_without_cancelling_its_caller():
    client = BaleWebSocketClient()
    future = asyncio.get_running_loop().create_future()
    client._pending_responses[1] = future
    async def rpc():
        return await future
    caller = asyncio.create_task(rpc())
    await asyncio.sleep(0)
    await client.close()
    with pytest.raises(ConnectionError, match='disconnected'):
        await caller
    assert not caller.cancelled()
    assert client._pending_responses == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [True, False])
async def test_manager_restarts_finished_enabled_poll_task(cancelled):
    service = BalePollingService()
    async def old():
        if cancelled:
            raise asyncio.CancelledError()
        raise ConnectionError('disconnected')
    dead = asyncio.create_task(old())
    await asyncio.sleep(0)
    service._poll_tasks['support'] = dead
    service._last_update_ids['support'] = '123'
    restarted = asyncio.Event()
    async def new(key):
        assert key == 'support'
        restarted.set()
        service._stop.set()
    with patch.object(service, '_list_enabled_instance_keys', return_value={'support'}), patch.object(service, '_run_instance', new=new):
        manager = asyncio.create_task(service._run_manager())
        await asyncio.wait_for(restarted.wait(), 2)
        await manager
    assert service._poll_tasks['support'] is not dead
    assert service._last_update_ids['support'] == '123'
