import type { BarGroupDatum } from "../components/charts/BarChart";

// Названия групп графика — не бесконечны (коллекции копятся годами, каналы
// прибавляются реже), но список без предела рано или поздно делает столбцы
// нечитаемыми, а цвета серий — повторяющимися. Оставляем top-N по сумме всех
// серий, остальное схлопываем в одну группу "Прочее" (см. ReportsPage.tsx).
export function bucketGroupTail(
  groups: BarGroupDatum[],
  seriesKeys: string[],
  maxVisible: number,
  otherLabel = "Прочее",
): BarGroupDatum[] {
  if (groups.length <= maxVisible) return groups;

  const totalOf = (g: BarGroupDatum) => seriesKeys.reduce((sum, k) => sum + (g.values[k] ?? 0), 0);
  const sorted = [...groups].sort((a, b) => totalOf(b) - totalOf(a));
  const head = sorted.slice(0, maxVisible);
  const tail = sorted.slice(maxVisible);

  const otherValues: Record<string, number> = {};
  for (const key of seriesKeys) {
    otherValues[key] = tail.reduce((sum, g) => sum + (g.values[key] ?? 0), 0);
  }
  return [...head, { label: otherLabel, values: otherValues }];
}
