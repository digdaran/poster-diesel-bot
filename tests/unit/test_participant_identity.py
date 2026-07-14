"""Тесты идентификации участников (п.7.1, 10.2, 10.3, 10.5, 20.1 ТЗ):
привязка только по подтверждённому номеру, подарочные покупки, приоритет
подтверждённого номера, переключатель ignore_phone_verification, мультиканальность."""

from __future__ import annotations

from app.models.enums import ChannelType
from app.services import participant_service as svc
from sqlalchemy.orm import Session


def test_manual_recipient_creates_unverified_participant_without_binding(session: Session) -> None:
    participant = svc.resolve_manual_recipient(session, "+7 999 000-11-22")
    assert participant.phone == "79990001122"
    assert participant.phone_verified is False
    assert participant.channel_bindings == []


def test_manual_recipient_reuses_existing_participant(session: Session) -> None:
    p1 = svc.resolve_manual_recipient(session, "79990001122")
    p2 = svc.resolve_manual_recipient(session, "89990001122")  # тот же номер, другой формат ввода
    assert p1.id == p2.id


def test_manual_recipient_sets_name_on_new_participant(session: Session) -> None:
    participant = svc.resolve_manual_recipient(session, "79990003334", full_name="Иван Иванов")
    assert participant.full_name == "Иван Иванов"


def test_manual_recipient_fills_name_if_previously_empty(session: Session) -> None:
    p1 = svc.resolve_manual_recipient(session, "79990003335")
    assert p1.full_name is None
    p2 = svc.resolve_manual_recipient(session, "79990003335", full_name="Пётр Петров")
    assert p1.id == p2.id
    assert p2.full_name == "Пётр Петров"


def test_manual_recipient_does_not_overwrite_existing_name(session: Session) -> None:
    p1 = svc.resolve_manual_recipient(session, "79990003336", full_name="Первое Имя")
    p2 = svc.resolve_manual_recipient(session, "79990003336", full_name="Другое Имя")
    assert p1.id == p2.id
    assert p2.full_name == "Первое Имя"


def test_manual_recipient_overwrites_name_when_allowed(session: Session) -> None:
    p1 = svc.resolve_manual_recipient(session, "79990003339", full_name="Первое Имя")
    p2 = svc.resolve_manual_recipient(
        session, "79990003339", full_name="Другое Имя", allow_overwrite=True
    )
    assert p1.id == p2.id
    assert p2.full_name == "Другое Имя"


def test_find_by_phone_returns_none_when_not_found(session: Session) -> None:
    assert svc.find_by_phone(session, "79990009999") is None


def test_find_by_phone_finds_regardless_of_input_format(session: Session) -> None:
    created = svc.resolve_manual_recipient(session, "79990003340", full_name="Найди Меня")
    found = svc.find_by_phone(session, "+7 999 000-33-40")
    assert found is not None
    assert found.id == created.id
    assert found.full_name == "Найди Меня"


def test_set_full_name_updates_participant(session: Session) -> None:
    participant = svc.resolve_manual_recipient(session, "79990003337")
    updated = svc.set_full_name(
        session, participant_id=participant.id, full_name="  Имя С Пробелами  "
    )
    assert updated.full_name == "Имя С Пробелами"


def test_set_full_name_ignores_blank_input(session: Session) -> None:
    participant = svc.resolve_manual_recipient(session, "79990003338")
    updated = svc.set_full_name(session, participant_id=participant.id, full_name="   ")
    assert updated.full_name is None


def test_confirm_channel_binding_creates_participant_and_verified_binding(session: Session) -> None:
    result = svc.confirm_channel_binding(
        session,
        channel=ChannelType.TELEGRAM,
        external_user_id="tg-1",
        phone_raw="79991112233",
        username="alice",
    )
    assert result.created_participant is True
    assert result.created_binding is True
    assert result.conflict is False
    assert result.participant.phone_verified is True
    assert result.binding.phone_verified is True
    assert result.binding.channel == ChannelType.TELEGRAM


def test_gift_purchase_then_owner_confirms_scenario(session: Session) -> None:
    """Сценарий п.10.5: подарочная покупка на номер Б (неподтверждённый), затем
    владелец Б подтверждает номер в канале — получает доступ ко всем номеркам."""
    recipient = svc.resolve_manual_recipient(session, "79995556677")
    assert recipient.phone_verified is False
    assert recipient.channel_bindings == []

    result = svc.confirm_channel_binding(
        session,
        channel=ChannelType.TELEGRAM,
        external_user_id="tg-owner-b",
        phone_raw="79995556677",
    )
    assert result.created_participant is False  # участник Б уже существовал
    assert result.participant.id == recipient.id
    assert result.participant.phone_verified is True  # приоритет подтверждённого номера
    assert result.conflict is False


def test_manual_input_does_not_grant_access_when_flag_off(session: Session) -> None:
    """Ручной ввод при выключенном ignore_phone_verification НЕ создаёт привязку
    и не открывает доступ к чужой учётке (п.7.1, 10.3 ТЗ) — только назначает
    получателя покупки."""
    # Кто-то уже владеет номером и подтвердил его в своём канале.
    owner = svc.confirm_channel_binding(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-owner", phone_raw="79997778899"
    ).participant

    # Другой человек вводит этот же номер вручную (напр. пытаясь "проверить" чужую учётку).
    resolved = svc.resolve_manual_recipient(session, "79997778899")
    assert resolved.id == owner.id  # тот же участник (единый ключ — телефон)
    # Но никакой НОВОЙ привязки для стороннего аккаунта не создаётся — резолвинг
    # получателя вообще не трогает ChannelBinding.
    assert len(resolved.channel_bindings) == 1
    assert resolved.channel_bindings[0].external_user_id == "tg-owner"


def test_channel_account_cannot_rebind_to_different_phone(session: Session) -> None:
    """Повторная привязка того же аккаунта канала к другому телефону запрещена
    (п.7.1 ТЗ) — привязка не меняется, возвращается conflict=True."""
    first = svc.confirm_channel_binding(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-1", phone_raw="79991112233"
    )
    second = svc.confirm_channel_binding(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-1", phone_raw="79994445566"
    )
    assert second.conflict is True
    assert second.created_binding is False
    assert second.participant.id == first.participant.id
    assert second.participant.phone == "79991112233"  # привязка НЕ переехала на новый номер


def test_multichannel_same_participant_by_phone(session: Session) -> None:
    """Мультиканальность: подтверждение одного и того же номера во втором канале
    ведёт в ту же учётку (п.7.1, 10.4, 20.1 ТЗ)."""
    r1 = svc.confirm_channel_binding(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-1", phone_raw="79991112233"
    )
    r2 = svc.confirm_channel_binding(
        session, channel=ChannelType.VK, external_user_id="vk-1", phone_raw="79991112233"
    )
    assert r1.participant.id == r2.participant.id
    assert len(r2.participant.channel_bindings) == 2
    channels = {b.channel for b in r2.participant.channel_bindings}
    assert channels == {ChannelType.TELEGRAM, ChannelType.VK}


def test_ignore_phone_verification_off_denies_access_for_unverified_binding(
    session: Session,
) -> None:
    result = svc.bind_channel_ignoring_verification(
        session, channel=ChannelType.VK, external_user_id="vk-2", phone_raw="79993334455"
    )
    assert result.binding.phone_verified is False
    # Пока флаг явно "включён" на уровне бизнес-процесса — доступ разрешаем.
    assert svc.can_access_own_account(result.binding, ignore_phone_verification=True) is True
    # Флаг выключили — доступ немедленно теряется (обратимость, п.7.1 ТЗ).
    assert svc.can_access_own_account(result.binding, ignore_phone_verification=False) is False


def test_ignore_phone_verification_on_creates_binding_and_participant(session: Session) -> None:
    result = svc.bind_channel_ignoring_verification(
        session, channel=ChannelType.VK, external_user_id="vk-3", phone_raw="79992223344"
    )
    assert result.created_participant is True
    assert result.created_binding is True
    assert result.participant.phone == "79992223344"
    assert result.binding.phone_verified is False
    assert result.participant.phone_verified is False  # само по себе участник не "подтверждён"


def test_get_participant_by_channel_returns_none_when_no_binding(session: Session) -> None:
    assert (
        svc.get_participant_by_channel(
            session, channel=ChannelType.TELEGRAM, external_user_id="unknown"
        )
        is None
    )


def test_get_participant_by_channel_returns_participant_when_bound(session: Session) -> None:
    result = svc.confirm_channel_binding(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-42", phone_raw="79990000000"
    )
    found = svc.get_participant_by_channel(
        session, channel=ChannelType.TELEGRAM, external_user_id="tg-42"
    )
    assert found is not None
    assert found.id == result.participant.id
