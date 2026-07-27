"""Регресс-тест на LOG №58: VK жёстко ограничивает label инлайн-кнопки 40
символами (`VKAPIError_911`) — подпись кнопки «📎 Прислать квитанцию» в
`_send_pending_payments` не должна включать номер счёта (может быть сколь
угодно длинным из-за префикса розыгрыша до 20 символов), иначе хендлер
`on_my_tickets` падает с необработанным исключением и участник вместо счетов
видит «Не понял команду» (см. DECISIONS_LOG.md №58)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.services import payment_service as payment_svc

from channels.vk import handlers as h


class _FakeMessage:
    def __init__(self, peer_id: int) -> None:
        self.peer_id = peer_id
        self.answer = AsyncMock()


async def test_vk_receipt_button_label_stays_within_vk_40_char_limit() -> None:
    payment = payment_svc.PendingPaymentInfo(
        payment_id=1,
        giveaway_name="Постер",
        quantity=1,
        amount=99999999,
        invoice_no="A" * 20 + "-00001",  # максимальная длина префикса розыгрыша
        has_receipt=False,
    )

    msg = _FakeMessage(peer_id=123)
    await h._send_pending_payments(123, [payment], answer_target=msg)

    keyboard_json = msg.answer.await_args.kwargs["keyboard"]
    assert keyboard_json is not None

    import json

    buttons = json.loads(keyboard_json)["buttons"]
    label = buttons[0][0]["action"]["label"]
    assert len(label.encode("utf-16-le")) // 2 <= 40, label


@pytest.mark.parametrize("count", [1, 2, 3])
async def test_vk_receipt_button_added_only_for_missing_receipt(count: int) -> None:
    payments = [
        payment_svc.PendingPaymentInfo(
            payment_id=i,
            giveaway_name=f"Постер {i}",
            quantity=1,
            amount=10000,
            invoice_no=f"TST-{i:05d}",
            has_receipt=(i % 2 == 0),
        )
        for i in range(1, count + 1)
    ]

    msg = _FakeMessage(peer_id=123)
    await h._send_pending_payments(123, payments, answer_target=msg)

    kwargs = msg.answer.await_args.kwargs
    keyboard_json = kwargs["keyboard"]

    import json

    expected_buttons = sum(1 for p in payments if not p.has_receipt)
    if expected_buttons == 0:
        assert keyboard_json is None
    else:
        buttons = [btn for row in json.loads(keyboard_json)["buttons"] for btn in row]
        assert len(buttons) == expected_buttons
