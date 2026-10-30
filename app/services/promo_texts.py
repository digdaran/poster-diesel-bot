"""Тексты акции «за каждые N купленных за раз — M в подарок» для ботов и уведомлений
(DECISIONS_LOG.md №87). Общие для Telegram и VK, чтобы формулировки не расходились.
Терминология — «постер/экземпляр/номер» (LOG №35/№41)."""

from __future__ import annotations

from app.models.giveaway import Giveaway

GIFT_MARK = "🎁"


def giveaway_badge(giveaway: Giveaway) -> str:
    """Короткая пометка к названию в списке постеров: « 🎁 5+1» (или пусто без акции)."""
    if not giveaway.has_promo:
        return ""
    return f" {GIFT_MARK} {giveaway.promo_buy_n}+{giveaway.promo_gift_m}"


def promo_line(giveaway: Giveaway) -> str:
    """Строка с условиями акции для экрана выбора количества (или пусто)."""
    if not giveaway.has_promo:
        return ""
    return (
        f"\n{GIFT_MARK} Акция: за каждые {giveaway.promo_buy_n} экз. в одной покупке — "
        f"{giveaway.promo_gift_m} экз. этого же постера в подарок!"
    )


def quantity_label(giveaway: Giveaway, quantity: int) -> str:
    """Подпись кнопки количества: «5+1», если за это количество положен подарок, иначе «5»."""
    gift = giveaway.gift_quantity_for(quantity)
    return f"{quantity}+{gift}" if gift else str(quantity)


def order_gift_line(giveaway: Giveaway, quantity: int) -> str:
    """Строка для шага подтверждения заказа: сколько придёт в подарок, либо подсказка,
    сколько докупить до следующего подарка (если это ещё возможно по остатку тиража)."""
    if not giveaway.has_promo:
        return ""
    assert giveaway.promo_buy_n is not None and giveaway.promo_gift_m is not None
    gift = giveaway.gift_quantity_for(quantity)
    line = ""
    if gift:
        line = (
            f"\n{GIFT_MARK} + {gift} экз. в подарок по акции — всего получите "
            f"{quantity + gift} экз."
        )
    remainder = quantity % giveaway.promo_buy_n
    need = giveaway.promo_buy_n - remainder
    if remainder and quantity + need <= giveaway.free_tickets_count:
        line += (
            f"\n💡 Добавьте ещё {need} экз. — и получите "
            f"{'ещё ' if gift else ''}{giveaway.promo_gift_m} экз. в подарок!"
        )
    return line


def invoice_gift_suffix(gift_quantity: int) -> str:
    """Дополнение к тексту созданного счёта: « (+1 экз. в подарок 🎁)»."""
    return f" (+{gift_quantity} экз. в подарок {GIFT_MARK})" if gift_quantity else ""


def delivery_codes(paid_codes: list[str], gift_codes: list[str]) -> list[str]:
    """Коды для сообщения о выдаче: подарочные помечены значком 🎁."""
    return paid_codes + [f"{code} {GIFT_MARK}" for code in gift_codes]


def delivery_intro(base: str, *, gifts_issued: int, gifts_not_issued: int) -> str:
    """Вводный текст сообщения о выдаче. Без подарков — `base` без изменений. Иначе
    добавляет, сколько подарков выдано и сколько будет выдано вручную в следующем тираже."""
    if not gifts_issued and not gifts_not_issued:
        return base
    head = base.removesuffix(", номера:").rstrip(":")
    parts = [head + "."]
    if gifts_issued:
        parts.append(
            f"{GIFT_MARK} Плюс {gifts_issued} экз. в подарок по акции — "
            f"подарочные номера отмечены значком {GIFT_MARK}."
        )
    if gifts_not_issued:
        parts.append(gifts_not_issued_text(gifts_not_issued))
    return "\n".join(parts) + "\n\nНомера:"


def gifts_not_issued_text(count: int) -> str:
    return (
        f"⚠️ {count} подарочных экз. по акции не хватило в этом тираже — они будут выданы "
        "вручную в следующем тираже."
    )
