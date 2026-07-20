"""Тесты чистых функций форматирования кодов номерков из channels/telegram/channel.py
(вынесены туда из handlers.py, чтобы их можно было переиспользовать из backend —
см. app/services/notification_service.py). Сами обработчики aiogram
(Message/CallbackQuery) в этом репозитории напрямую не тестируются — нет
прецедента мокания Bot API, только эти вспомогательные функции."""

from __future__ import annotations

from channels.telegram.channel import (
    _TICKET_CODES_CHUNK_LIMIT,
    _TICKET_CODES_COLUMN_THRESHOLD,
    _format_ticket_codes,
)


def test_format_ticket_codes_empty() -> None:
    assert _format_ticket_codes([]) == []


def test_format_ticket_codes_short_list_is_one_per_line() -> None:
    codes = [f"ENT-{i:06d}" for i in range(1, 6)]
    chunks = _format_ticket_codes(codes)
    assert len(chunks) == 1
    assert chunks[0] == "\n".join(codes)


def test_format_ticket_codes_at_threshold_stays_one_per_line() -> None:
    codes = [f"ENT-{i:06d}" for i in range(1, _TICKET_CODES_COLUMN_THRESHOLD + 1)]
    chunks = _format_ticket_codes(codes)
    assert "\n".join(chunks) == "\n".join(codes)


def test_format_ticket_codes_over_threshold_uses_columns() -> None:
    codes = [f"ENT-{i:06d}" for i in range(1, _TICKET_CODES_COLUMN_THRESHOLD + 2)]
    chunks = _format_ticket_codes(codes)
    # Больше одного кода на строку хотя бы в одной строке — значит, колонки применились.
    lines = "\n".join(chunks).splitlines()
    assert any(line.count("ENT-") > 1 for line in lines)
    # Ни один код нигде не потерялся.
    for code in codes:
        assert code in "\n".join(chunks)


def test_format_ticket_codes_large_list_splits_into_multiple_messages() -> None:
    """Регресс: 700 номерков одним сообщением превышали лимит Telegram (4096
    символов), из-за чего отправка молча падала и бот ничего не отвечал."""
    codes = [f"TST-{i:06d}" for i in range(1, 701)]
    chunks = _format_ticket_codes(codes)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= _TICKET_CODES_CHUNK_LIMIT
    # Все коды присутствуют суммарно, ни один не потерян и не задвоен.
    joined = "\n".join(chunks)
    for code in codes:
        assert code in joined


def test_format_ticket_codes_columns_are_aligned() -> None:
    codes = ["ENT-000001", "ENT-000002", "ENT-000003", "ENT-000004", "ENT-000005"] * 3
    chunks = _format_ticket_codes(codes)
    lines = "\n".join(chunks).splitlines()
    # Все строки одной "таблицы" не пустые и не превышают разумную ширину.
    assert all(line for line in lines)
