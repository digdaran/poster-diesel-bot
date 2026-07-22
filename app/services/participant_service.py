"""Сервис идентификации участников (п.7.1, 10.2, 10.3, 10.5 ТЗ).

Единственная точка входа для всей логики идентификации: привязка канала только
по подтверждённому номеру, приоритет подтверждённого номера, подарочные покупки
на неподтверждённый номер, переключатель `ignore_phone_verification`. Каналы
(Telegram и др.) и API панели вызывают только эти функции, никогда не создают
`ChannelBinding`/`Participant` напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.phone import normalize_phone
from app.models.base import utcnow
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, ManualRegistrationStatus, PaymentStatus
from app.models.manual_registration import ManualRegistration
from app.models.participant import Participant
from app.models.payment import Payment


@dataclass(frozen=True)
class IdentifyResult:
    participant: Participant
    binding: ChannelBinding | None
    created_participant: bool
    created_binding: bool
    conflict: bool = False
    """True, если аккаунт канала уже был привязан к ДРУГОМУ номеру, чем переданный
    сейчас (п.7.1 ТЗ: "повторная привязка того же аккаунта к другому телефону
    запрещена и фиксируется в аудите"). В этом случае привязка НЕ меняется —
    возвращается существующий участник; вызывающая сторона обязана залогировать
    конфликт в аудит (см. app.services.audit_service)."""


def get_participant_by_channel(
    session: Session, *, channel: ChannelType, external_user_id: str
) -> Participant | None:
    """Находит участника по привязке канала (используется каналом для проверки
    доступа при каждом обращении пользователя, п.10.2 ТЗ)."""
    binding = session.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel == channel, ChannelBinding.external_user_id == external_user_id
        )
    ).scalar_one_or_none()
    return binding.participant if binding else None


def get_binding(
    session: Session, *, channel: ChannelType, external_user_id: str
) -> ChannelBinding | None:
    return session.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel == channel, ChannelBinding.external_user_id == external_user_id
        )
    ).scalar_one_or_none()


def can_access_own_account(binding: ChannelBinding, *, ignore_phone_verification: bool) -> bool:
    """Доступ к своей учётке (история, номерки, п.10.2 ТЗ) есть, если привязка
    подтверждена ИЛИ включён `ignore_phone_verification` (п.7.1 ТЗ). Обратимость:
    если флаг позже выключат, привязки с `phone_verified=False` доступ теряют."""
    return binding.phone_verified or ignore_phone_verification


def is_participant_blocked(session: Session, *, participant_id: int) -> bool:
    """Заблокирован ли участник в панели (`Participant.is_blocked`, п.6.2 ТЗ) —
    проверяется перед созданием любой покупки (см. вызовы в
    `payment_service.create_payment` / `manual_registration_service.create_manual_registration`),
    чтобы блокировка реально останавливала транзакции, а не была только фильтром
    в списке участников."""
    return session.execute(
        select(Participant.is_blocked).where(Participant.id == participant_id)
    ).scalar_one()


def has_active_purchase(session: Session, *, participant_id: int) -> bool:
    """Есть ли у участника незавершённая покупка — продуктовое решение:
    не более одной активной покупки одновременно, глобально для всех каналов
    продажи (бот, подарочные покупки, ручная регистрация оператором), см.
    DECISIONS.md. Активная покупка = PENDING онлайн-платёж ИЛИ PENDING ручная
    регистрация. Вызывается внутри `db.immediate_session()` в
    `payment_service.create_payment` / `manual_registration_service.create_manual_registration`,
    чтобы проверка и последующая вставка были атомарны (тот же механизм
    сериализации писателей SQLite, что и для пула номеров — см. CLAUDE.md)."""
    has_pending_payment = session.execute(
        select(Payment.id)
        .where(Payment.participant_id == participant_id, Payment.status == PaymentStatus.PENDING)
        .limit(1)
    ).first()
    if has_pending_payment is not None:
        return True
    has_pending_manual = session.execute(
        select(ManualRegistration.id)
        .where(
            ManualRegistration.participant_id == participant_id,
            ManualRegistration.status == ManualRegistrationStatus.PENDING,
        )
        .limit(1)
    ).first()
    return has_pending_manual is not None


def _find_participant_by_phone(session: Session, phone: str) -> Participant | None:
    return session.execute(
        select(Participant).where(Participant.phone == phone)
    ).scalar_one_or_none()


def find_by_phone(session: Session, phone_raw: str) -> Participant | None:
    """Публичный поиск участника по номеру (для автоподстановки имени в форме
    ручной регистрации, п.14.2/14.3 ТЗ — см. DECISIONS.md)."""
    return _find_participant_by_phone(session, normalize_phone(phone_raw))


def resolve_manual_recipient(
    session: Session,
    phone_raw: str,
    full_name: str | None = None,
    *,
    allow_overwrite: bool = False,
) -> Participant:
    """Ручной ввод номера, режим по умолчанию (`ignore_phone_verification=False`,
    п.7.1, 10.3 ТЗ): назначает получателя покупки по номеру, НЕ создаёт привязку
    канала и НЕ открывает доступ к чужой учётке. Если участника с номером нет —
    создаётся с `phone_verified=False`, без привязок (подарочная покупка, п.10.5).

    `full_name`, если передано, заполняется только если у участника ещё нет имени
    (fill-if-empty), если только `allow_overwrite=True` — тогда перезаписывает
    существующее имя. Вызывающая сторона (backend/api/manual_registrations.py)
    обязана передавать `allow_overwrite=True` только для Super Admin — см.
    DECISIONS.md."""
    phone = normalize_phone(phone_raw)
    participant = _find_participant_by_phone(session, phone)
    name = full_name.strip() if full_name else None
    if participant is None:
        participant = Participant(phone=phone, phone_verified=False, full_name=name or None)
        session.add(participant)
        session.flush()
    elif name and (allow_overwrite or not participant.full_name):
        participant.full_name = name
        session.flush()
    return participant


def set_full_name(session: Session, *, participant_id: int, full_name: str) -> Participant:
    """Устанавливает имя участника (после запроса в боте после подтверждения
    номера, или через редактирование в панели). Пустая строка не сохраняется."""
    participant = session.get(Participant, participant_id)
    assert participant is not None
    name = full_name.strip()
    if name:
        participant.full_name = name
    session.flush()
    return participant


def confirm_channel_binding(
    session: Session,
    *,
    channel: ChannelType,
    external_user_id: str,
    phone_raw: str,
    username: str | None = None,
) -> IdentifyResult:
    """Подтверждение владения номером в канале (напр. Telegram `request_contact`,
    п.7.1, 10.2 ТЗ). Привязывает аккаунт канала к участнику с этим номером (создаёт
    участника, если его нет) с `phone_verified=True`. Приоритет подтверждённого
    номера: если участник уже существовал неподтверждённым (подарочная покупка,
    п.10.5), он становится подтверждённым.

    Если аккаунт канала уже привязан к ДРУГОМУ участнику — привязка не меняется,
    возвращается `conflict=True` (см. IdentifyResult).
    """
    phone = normalize_phone(phone_raw)
    existing_binding = get_binding(session, channel=channel, external_user_id=external_user_id)

    if existing_binding is not None:
        conflict = existing_binding.participant.phone != phone
        return IdentifyResult(
            participant=existing_binding.participant,
            binding=existing_binding,
            created_participant=False,
            created_binding=False,
            conflict=conflict,
        )

    participant = _find_participant_by_phone(session, phone)
    created_participant = False
    if participant is None:
        participant = Participant(phone=phone, phone_verified=True)
        session.add(participant)
        session.flush()
        created_participant = True

    binding = ChannelBinding(
        participant_id=participant.id,
        channel=channel,
        external_user_id=external_user_id,
        username=username,
        phone_verified=True,
        linked_at=utcnow(),
    )
    session.add(binding)
    session.flush()

    participant.phone_verified = True  # только что добавили подтверждённую привязку
    session.flush()

    return IdentifyResult(
        participant=participant,
        binding=binding,
        created_participant=created_participant,
        created_binding=True,
    )


def bind_channel_ignoring_verification(
    session: Session,
    *,
    channel: ChannelType,
    external_user_id: str,
    phone_raw: str,
    username: str | None = None,
) -> IdentifyResult:
    """Ручной ввод номера при включённом `ignore_phone_verification` (п.7.1, 10.3 ТЗ):
    открывает доступ к учётной записи этого номера и создаёт привязку канала, БЕЗ
    подтверждения владения номером. Привязка создаётся с `phone_verified=False`
    (по факту номер не проверен) — доступ обеспечивается тем, что
    `can_access_own_account()` учитывает глобальный флаг отдельно от этого поля,
    что делает переключатель обратимым (п.7.1 ТЗ: после выключения флага доступ
    остаётся только у подлинно подтверждённых привязок).
    """
    existing_binding = get_binding(session, channel=channel, external_user_id=external_user_id)
    if existing_binding is not None:
        phone = normalize_phone(phone_raw)
        conflict = existing_binding.participant.phone != phone
        return IdentifyResult(
            participant=existing_binding.participant,
            binding=existing_binding,
            created_participant=False,
            created_binding=False,
            conflict=conflict,
        )

    phone = normalize_phone(phone_raw)
    participant = _find_participant_by_phone(session, phone)
    created_participant = False
    if participant is None:
        participant = Participant(phone=phone, phone_verified=False)
        session.add(participant)
        session.flush()
        created_participant = True

    binding = ChannelBinding(
        participant_id=participant.id,
        channel=channel,
        external_user_id=external_user_id,
        username=username,
        phone_verified=False,
        linked_at=utcnow(),
    )
    session.add(binding)
    session.flush()

    return IdentifyResult(
        participant=participant,
        binding=binding,
        created_participant=created_participant,
        created_binding=True,
    )
