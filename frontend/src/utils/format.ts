const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 2,
});

export function formatMoney(cents: number): string {
  return moneyFormatter.format(cents / 100);
}

export function formatDateTime(iso: string): string {
  // Бэкенд отдаёт naive datetime (без указания зоны), но по конвенции
  // (DECISIONS_LOG.md №11) они всегда означают UTC. Без явного маркера
  // зоны `new Date(...)` трактует строку как локальное время браузера,
  // из-за чего конвертация в локальную зону не происходит вообще.
  const utcIso = /[Zz]|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utcIso).toLocaleString("ru-RU");
}
