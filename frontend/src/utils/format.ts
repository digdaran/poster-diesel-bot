const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 2,
});

export function formatMoney(cents: number): string {
  return moneyFormatter.format(cents / 100);
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU");
}
