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
