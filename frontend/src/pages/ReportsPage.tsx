import { useEffect, useMemo, useState } from "react";
import { GiveawaysApi, ReportsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { LoadingState, EmptyStateRow } from "../components/EmptyState";
import { LineChart } from "../components/charts/LineChart";
import { BarChart, type BarGroupDatum, type BarSeriesSpec } from "../components/charts/BarChart";
import { DateRangePicker } from "../components/charts/DateRangePicker";
import { TimelineBrush } from "../components/charts/TimelineBrush";
import { formatMoney, formatMoneyCompact } from "../utils/format";
import { CHANNEL_LABELS } from "../utils/channels";
import { bucketGroupTail } from "../utils/charts";
import type {
  ChannelSalesRow,
  Giveaway,
  RevenueByGiveawayRow,
  SalesByPeriodRow,
} from "../api/types";

// Больше этого числа коллекций/провайдеров/каналов на графике — сворачиваем
// хвост в "Прочее" (см. utils/charts.ts): иначе горизонтальные бары растягивают
// страницу до бесконечности, а цвета серий начинают повторяться.
const MAX_CHART_CATEGORIES = 8;

const ONLINE_OFFLINE_LABELS: Record<string, string> = {
  online: "Онлайн",
  offline: "Офлайн — итого",
  offline_cash: "Офлайн — наличные (касса)",
  offline_cashless: "Офлайн — безнал (QR оператора)",
};

const REVENUE_SERIES: BarSeriesSpec[] = [
  { key: "revenue_online", label: "Эквайринг", colorVar: "--series-1" },
  { key: "revenue_offline_cash", label: "Офлайн, наличные (касса)", colorVar: "--series-2" },
  {
    key: "revenue_offline_cashless",
    label: "Офлайн, безнал (QR оператора)",
    colorVar: "--series-3",
  },
];

const ONLINE_OFFLINE_CHART_KEYS = ["online", "offline_cash", "offline_cashless"] as const;

// Таймлайн-обзор под графиком продаж всегда рисуется по дням за это окно,
// независимо от выбранной гранулярности/диапазона основного графика — это
// просто «карта местности», по которой перетаскиванием выбирается диапазон.
const BRUSH_WINDOW_DAYS = 180;

type Granularity = "hour" | "day" | "month";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return isoDate(d);
}

function defaultDateFrom(): string {
  return daysAgo(29);
}

function formatPeriodLabel(period: string, granularity: Granularity): string {
  if (granularity === "hour") {
    const [datePart, hourPart] = period.split("T");
    const dateParts = datePart?.split("-") ?? [];
    if (dateParts.length === 3 && hourPart) {
      return `${dateParts[2]}.${dateParts[1]} ${hourPart}:00`;
    }
    return period;
  }
  const parts = period.split("-");
  if (granularity === "day" && parts.length === 3) {
    return `${parts[2]}.${parts[1]}`;
  }
  if (parts.length >= 2) {
    return `${parts[1]}.${parts[0]}`;
  }
  return period;
}

export function ReportsPage() {
  const { hasPermission } = useAuth();
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [giveawayId, setGiveawayId] = useState<number | undefined>(undefined);
  const [summary, setSummary] = useState<{
    revenue_online: number;
    revenue_offline: number;
    revenue_offline_cash: number;
    revenue_offline_cashless: number;
    revenue_total: number;
    successful_payments_count: number;
    average_check: number;
  } | null>(null);
  const [onlineOffline, setOnlineOffline] = useState<Record<
    string,
    { count: number; amount: number }
  > | null>(null);
  const [byGiveaway, setByGiveaway] = useState<RevenueByGiveawayRow[] | null>(null);
  const [byChannel, setByChannel] = useState<ChannelSalesRow[] | null>(null);

  const [dateFrom, setDateFrom] = useState(defaultDateFrom());
  const [dateTo, setDateTo] = useState(isoDate(new Date()));
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [metric, setMetric] = useState<"amount" | "count">("amount");
  const [salesByPeriod, setSalesByPeriod] = useState<SalesByPeriodRow[] | null>(null);
  const [brushOverview, setBrushOverview] = useState<SalesByPeriodRow[] | null>(null);

  useEffect(() => {
    void GiveawaysApi.list().then(setGiveaways);
    void ReportsApi.revenueByGiveaway().then(setByGiveaway);
  }, []);

  useEffect(() => {
    void ReportsApi.financialSummary(giveawayId).then(setSummary);
    void ReportsApi.onlineVsOffline(giveawayId).then(setOnlineOffline);
    void ReportsApi.salesByChannel(giveawayId).then(setByChannel);
  }, [giveawayId]);

  useEffect(() => {
    setSalesByPeriod(null);
    void ReportsApi.salesByPeriod({
      granularity,
      giveaway_id: giveawayId,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    }).then(setSalesByPeriod);
  }, [giveawayId, granularity, dateFrom, dateTo]);

  useEffect(() => {
    void ReportsApi.salesByPeriod({
      granularity: "day",
      giveaway_id: giveawayId,
      date_from: daysAgo(BRUSH_WINDOW_DAYS - 1),
      date_to: isoDate(new Date()),
    }).then(setBrushOverview);
  }, [giveawayId]);

  const trendData = useMemo(
    () =>
      (salesByPeriod ?? []).map((row) => ({
        x: row.period,
        y: metric === "amount" ? row.amount : row.count,
      })),
    [salesByPeriod, metric],
  );

  const brushData = useMemo(
    () =>
      (brushOverview ?? []).map((row) => ({
        x: row.period,
        y: metric === "amount" ? row.amount : row.count,
      })),
    [brushOverview, metric],
  );

  const revenueByGiveawayGroups: BarGroupDatum[] = useMemo(
    () =>
      bucketGroupTail(
        (byGiveaway ?? []).map((row) => ({
          label: row.giveaway_name,
          values: {
            revenue_online: row.revenue_online,
            revenue_offline_cash: row.revenue_offline_cash,
            revenue_offline_cashless: row.revenue_offline_cashless,
          },
        })),
        REVENUE_SERIES.map((s) => s.key),
        MAX_CHART_CATEGORIES,
      ),
    [byGiveaway],
  );

  const onlineOfflineGroups: BarGroupDatum[] = useMemo(
    () =>
      onlineOffline
        ? ONLINE_OFFLINE_CHART_KEYS.map((key) => ({
            label: ONLINE_OFFLINE_LABELS[key],
            values: { [key]: onlineOffline[key]?.amount ?? 0 },
          }))
        : [],
    [onlineOffline],
  );
  const onlineOfflineSeries: BarSeriesSpec[] = ONLINE_OFFLINE_CHART_KEYS.map((key, i) => ({
    key,
    label: ONLINE_OFFLINE_LABELS[key],
    colorVar: `--series-${i + 1}`,
  }));

  const channelGroups: BarGroupDatum[] = useMemo(
    () =>
      (byChannel ?? []).map((row) => ({
        label: CHANNEL_LABELS[row.channel] ?? row.channel,
        values: { [row.channel]: row.amount },
      })),
    [byChannel],
  );
  const channelSeries: BarSeriesSpec[] = useMemo(
    () =>
      (byChannel ?? []).map((row, i) => ({
        key: row.channel,
        label: CHANNEL_LABELS[row.channel] ?? row.channel,
        colorVar: `--series-${(i % 6) + 1}`,
      })),
    [byChannel],
  );

  const downloadReport = async (path: string, format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload(path, { export: format });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <h1>Отчёты</h1>

      <section>
        <label htmlFor="giveaway-filter">Коллекция: </label>
        <select
          id="giveaway-filter"
          value={giveawayId ?? ""}
          onChange={(e) => setGiveawayId(e.target.value ? Number(e.target.value) : undefined)}
        >
          <option value="">Все коллекции</option>
          {giveaways.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </section>

      <section>
        <h2>Динамика продаж</h2>
        <DateRangePicker
          from={dateFrom}
          to={dateTo}
          onChange={(f, t) => {
            setDateFrom(f);
            setDateTo(t);
          }}
        />
        <div className="viz-chart-header">
          <div className="segmented" role="group" aria-label="Метрика">
            <button
              type="button"
              className={metric === "amount" ? "active" : ""}
              onClick={() => setMetric("amount")}
            >
              Выручка
            </button>
            <button
              type="button"
              className={metric === "count" ? "active" : ""}
              onClick={() => setMetric("count")}
            >
              Кол-во билетов
            </button>
          </div>
          <div className="segmented" role="group" aria-label="Группировка">
            <button
              type="button"
              className={granularity === "hour" ? "active" : ""}
              onClick={() => setGranularity("hour")}
            >
              По часам
            </button>
            <button
              type="button"
              className={granularity === "day" ? "active" : ""}
              onClick={() => setGranularity("day")}
            >
              По дням
            </button>
            <button
              type="button"
              className={granularity === "month" ? "active" : ""}
              onClick={() => setGranularity("month")}
            >
              По месяцам
            </button>
          </div>
        </div>
        <div className="viz-chart-card">
          {salesByPeriod ? (
            <>
              <LineChart
                data={trendData}
                formatValue={metric === "amount" ? formatMoney : (v) => String(Math.round(v))}
                formatAxisValue={metric === "amount" ? formatMoneyCompact : undefined}
                formatX={(x) => formatPeriodLabel(x, granularity)}
                height={180}
                emptyMessage={
                  dateFrom || dateTo
                    ? "Нет продаж в выбранном диапазоне"
                    : "В этой коллекции ещё не было продаж"
                }
              />
              {trendData.length === 0 && (dateFrom || dateTo) && (
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => {
                    setDateFrom("");
                    setDateTo("");
                  }}
                >
                  Сбросить диапазон
                </button>
              )}
            </>
          ) : (
            <LoadingState />
          )}
          {brushData.length > 0 && (
            <TimelineBrush
              data={brushData}
              from={dateFrom}
              to={dateTo}
              onChange={(f, t) => {
                setDateFrom(f);
                setDateTo(t);
              }}
            />
          )}
        </div>
      </section>

      <section>
        <h2>Выручка по коллекциям</h2>
        <div className="viz-chart-card">
          {byGiveaway ? (
            <BarChart
              groups={revenueByGiveawayGroups}
              series={REVENUE_SERIES}
              stacked
              orientation="horizontal"
              formatValue={formatMoney}
              formatAxisValue={formatMoneyCompact}
            />
          ) : (
            <LoadingState />
          )}
        </div>
        {byGiveaway ? (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Коллекция</th>
                  <th>Эквайринг</th>
                  <th>Офлайн, наличные (касса)</th>
                  <th>Офлайн, безнал (QR оператора)</th>
                  <th>Итого</th>
                  <th>Экземпляров выдано</th>
                </tr>
              </thead>
              <tbody>
                {byGiveaway.length === 0 && <EmptyStateRow colSpan={6} />}
                {byGiveaway.map((row) => (
                  <tr key={row.giveaway_id}>
                    <td>{row.giveaway_name}</td>
                    <td>{formatMoney(row.revenue_online)}</td>
                    <td>{formatMoney(row.revenue_offline_cash)}</td>
                    <td>{formatMoney(row.revenue_offline_cashless)}</td>
                    <td>{formatMoney(row.revenue_total)}</td>
                    <td>{row.tickets_issued}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <LoadingState />
        )}
      </section>

      <section>
        <h2>Финансовая сводка {giveawayId ? "по коллекции" : "(все коллекции)"}</h2>
        {summary ? (
          <ul>
            <li>Эквайринг: {formatMoney(summary.revenue_online)}</li>
            <li>Офлайн, наличные (касса): {formatMoney(summary.revenue_offline_cash)}</li>
            <li>Офлайн, безнал (QR оператора): {formatMoney(summary.revenue_offline_cashless)}</li>
            <li>Итого выручка: {formatMoney(summary.revenue_total)}</li>
            <li>Успешных платежей: {summary.successful_payments_count}</li>
            <li>Средний чек (онлайн): {formatMoney(summary.average_check)}</li>
          </ul>
        ) : (
          <LoadingState />
        )}
      </section>

      <section>
        <h2>Онлайн vs офлайн</h2>
        <div className="viz-chart-card">
          {onlineOffline ? (
            <BarChart
              groups={onlineOfflineGroups}
              series={onlineOfflineSeries}
              stacked={false}
              showLegend={false}
              formatValue={formatMoney}
              formatAxisValue={formatMoneyCompact}
              height={160}
            />
          ) : (
            <LoadingState />
          )}
        </div>
        {onlineOffline ? (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Канал</th>
                  <th>Кол-во</th>
                  <th>Сумма</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(onlineOffline).map(([key, value]) => (
                  <tr key={key}>
                    <td>{ONLINE_OFFLINE_LABELS[key] ?? key}</td>
                    <td>{value.count}</td>
                    <td>{formatMoney(value.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <LoadingState />
        )}
      </section>

      <section>
        <h2>Продажи по каналу связи (Telegram/VK)</h2>
        <div className="viz-chart-card">
          {byChannel ? (
            <BarChart
              groups={channelGroups}
              series={channelSeries}
              stacked={false}
              showLegend={false}
              formatValue={formatMoney}
              formatAxisValue={formatMoneyCompact}
              height={160}
            />
          ) : (
            <LoadingState />
          )}
        </div>
        {byChannel ? (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Канал</th>
                  <th>Кол-во</th>
                  <th>Сумма</th>
                </tr>
              </thead>
              <tbody>
                {byChannel.length === 0 && <EmptyStateRow colSpan={3} />}
                {byChannel.map((row) => (
                  <tr key={row.channel}>
                    <td>{CHANNEL_LABELS[row.channel] ?? row.channel}</td>
                    <td>{row.count}</td>
                    <td>{formatMoney(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <LoadingState />
        )}
      </section>

      {hasPermission("reports_export") && (
        <section>
          <h2>Экспорт</h2>
          <button onClick={() => downloadReport("/api/reports/by-provider", "csv")}>
            По провайдерам — CSV
          </button>
          <button onClick={() => downloadReport("/api/reports/by-provider", "xlsx")}>
            По провайдерам — XLSX
          </button>
          <button onClick={() => downloadReport("/api/reports/by-channel", "csv")}>
            По каналу связи — CSV
          </button>
          <button onClick={() => downloadReport("/api/reports/by-channel", "xlsx")}>
            По каналу связи — XLSX
          </button>
          <button onClick={() => downloadReport("/api/reports/participants", "xlsx")}>
            По участникам — XLSX
          </button>
        </section>
      )}
    </div>
  );
}
