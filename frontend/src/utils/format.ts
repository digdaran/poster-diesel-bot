const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 2,
});

export function formatMoney(cents: number): string {
  return moneyFormatter.format(cents / 100);
}

// Бэкенд отдаёт naive datetime (без указания зоны), но по конвенции
// (DECISIONS_LOG.md №11) они всегда означают UTC. Без явного маркера зоны
// `new Date(...)` трактует строку как локальное время браузера, из-за чего
// конвертация в локальную зону не происходит вообще (и любая математика над
// разницей во времени сдвигается на офсет браузера относительно UTC).
function parseUtcIso(iso: string): Date {
  const utcIso = /[Zz]|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utcIso);
}

export function formatDateTime(iso: string): string {
  return parseUtcIso(iso).toLocaleString("ru-RU");
}

export function formatRelativeTime(iso: string): string {
  const diffMin = Math.round((Date.now() - parseUtcIso(iso).getTime()) / 60_000);
  if (diffMin < 1) return "только что";
  if (diffMin < 60) return `${diffMin} мин назад`;
  const diffHours = Math.round(diffMin / 60);
  if (diffHours < 24) return `${diffHours} ч назад`;
  return formatDateTime(iso);
}
