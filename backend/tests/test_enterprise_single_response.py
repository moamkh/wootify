from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wootify.application.enterprise.bale import (
    ADDRESS_TEHRAN_ALBORZ_LABEL,
    EnterpriseBaleService,
)
from wootify.infrastructure.persistence.models import (
    EnterpriseGreStatus,
    EnterpriseUserState,
)


def _eligible_user():
    return SimpleNamespace(
        gre_status=EnterpriseGreStatus.eligible,
        current_group_id="old-group",
        current_state=EnterpriseUserState.address_menu,
    )


@pytest.mark.asyncio
async def test_address_result_and_root_keyboard_are_sent_as_one_message():
    service = EnterpriseBaleService()
    service._send_text = AsyncMock(return_value={"ok": True})
    runtime = SimpleNamespace(
        instance=SimpleNamespace(instance_key="enterprise-test"),
        platform_metadata={"enterprise_address_tehran_alborz_text": "Address"},
    )
    user = _eligible_user()

    result = await service._handle_address_menu(
        SimpleNamespace(), runtime, user, "123", ADDRESS_TEHRAN_ALBORZ_LABEL
    )

    assert result == {"message": "address_sent", "status": "ok"}
    service._send_text.assert_awaited_once()
    args = service._send_text.await_args.args
    kwargs = service._send_text.await_args.kwargs
    assert args == ("enterprise-test", "123", "Address")
    assert kwargs["reply_markup"] == service._eligible_root_markup()
    assert user.current_state == EnterpriseUserState.eligible_root
    assert user.current_group_id is None


@pytest.mark.asyncio
async def test_phone_error_and_prompt_are_combined_into_one_message():
    service = EnterpriseBaleService()
    service._send_text = AsyncMock(return_value={"ok": True})

    await service._send_phone_prompt(
        "enterprise-test",
        "123",
        platform_metadata={"enterprise_phone_prompt_text": "Share phone"},
        leading_text="Invalid phone",
    )

    service._send_text.assert_awaited_once_with(
        "enterprise-test",
        "123",
        "Invalid phone\n\nShare phone",
        reply_markup=service._phone_prompt_markup(),
    )
