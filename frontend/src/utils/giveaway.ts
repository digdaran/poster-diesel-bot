import type { Giveaway } from "../api/types";

/**
 * Регистрация закрыта навсегда — необратимо («Закрыть регистрацию навсегда» после того
 * как была открыта) либо коллекция заархивирована (архивация всегда требует уже закрытой
 * регистрации). Отличается от «ещё не открыта» (opened_at === null), которая обратима и
 * закрытием не считается.
 *
 * Зеркалит `Giveaway.is_closed_forever` / `Giveaway.closed_forever_clause()` в
 * app/models/giveaway.py — держать в синхронизации при изменении.
 */
export function isClosedForever(giveaway: Giveaway): boolean {
  return giveaway.is_archived || (giveaway.opened_at !== null && !giveaway.is_registration_open);
}

/** Условия акции «N + M в подарок» в виде «5+1», или null, если акции нет. */
export function formatPromo(
  giveaway: Pick<Giveaway, "promo_buy_n" | "promo_gift_m">,
): string | null {
  if (!giveaway.promo_buy_n || !giveaway.promo_gift_m) return null;
  return `${giveaway.promo_buy_n}+${giveaway.promo_gift_m}`;
}
