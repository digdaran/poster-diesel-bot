import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { DashboardApi } from "../api/resources";
import type { Dashboard, DashboardAlert, DashboardGiveawayCard } from "../api/types";
import { LoadingState, EmptyStateRow } from "../components/EmptyState";
import { Badge } from "../components/Badge";
import { BankReconciliationStatusPanel } from "../components/BankReconciliationStatusPanel";
import { LineChart } from "../components/charts/LineChart";
import { BarChart } from "../components/charts/BarChart";
import type { BarGroupDatum, BarSeriesSpec } from "../components/charts/BarChart";
import { Sparkline } from "../components/charts/Sparkline";
import { CHANNEL_LABELS } from "../utils/channels";
import {
  formatMoney,
  formatMoneyCompact,
  formatMoneyRounded,
  formatPercentDelta,
} from "../utils/format";

// Как и «Мониторинг продаж» — сводка на Dashboard обновляется вживую, раз в
// несколько секунд, а не только при заходе на страницу (см. обсуждение с
// владельцем при внедрении раздела).
const POLL_INTERVAL_MS = 3000;

// Порог подсветки "тираж почти распродан" на карточке — визуальное зеркало
// LOW_STOCK_FREE_RATIO в app/services/dashboard_service.py (сам алерт уже
// приходит готовым с бэкенда, это только цвет прогресс-бара на карточке).
const LOW_STOCK_FREE_RATIO = 0.05;

function daysSince(iso: string): number {
  const opened = new Date(iso).getTime();
  return Math.max(0, Math.floor((Date.now() - opened) / (24 * 60 * 60 * 1000)));
}

function formatDayLabel(period: string): string {
  const parts = period.split("-");
  return parts.length === 3 ? `${parts[2]}.${parts[1]}` : period;
}

// Зеркало app/models/giveaway.py::Giveaway.is_open_for_sale — регистрация
// открыта, коллекция не приостановлена, есть свободные номерки. Это и есть
// "активная" коллекция для верхнего ряда (не путать с is_registration_open,
// которое остаётся true и для приостановленной/распроданной).
function isOpenForSale(g: DashboardGiveawayCard): boolean {
  return g.is_registration_open && !g.is_locked && g.free_tickets_count > 0;
}

function alertText(a: DashboardAlert): string {
  switch (a.type) {
    case "low_stock":
      return `«${a.giveaway_name}» — осталось ${a.free_tickets_count} из ${a.max_tickets} номерков`;
    case "sales_stalled":
      return `«${a.giveaway_name}» — нет продаж уже ${a.stalled_days} дн.`;
    case "manual_registration_expiring":
      return (
        `Ручная регистрация по «${a.giveaway_name}» истечёт через ` +
        `${a.minutes_until_expiry} мин без подтверждения`
      );
    case "bank_mismatch":
      return (
        `Счёт ${a.invoice_no} (${a.giveaway_name}) — расхождение суммы висит ` + `${a.hours_open} ч`
      );
  }
}

function alertLink(a: DashboardAlert): string {
  switch (a.type) {
    case "low_stock":
    case "sales_stalled":
      return a.giveaway_id ? `/giveaways/${a.giveaway_id}` : "/giveaways";
    case "manual_registration_expiring":
      return "/manual-registrations";
    case "bank_mismatch":
      return "/sales";
  }
}

const DANGER_ALERT_TYPES = new Set<DashboardAlert["type"]>(["bank_mismatch"]);

// Разная иконка на тип — чтобы список из нескольких алертов читался с одного
// взгляда, без необходимости вчитываться в текст каждой строки.
const ALERT_ICON: Record<DashboardAlert["type"], string> = {
  low_stock: "📦",
  sales_stalled: "📉",
  manual_registration_expiring: "⏳",
  bank_mismatch: "💳",
};

function DashboardAlertRow({ alert }: { alert: DashboardAlert }) {
  return (
    <Link
      to={alertLink(alert)}
      className={"dashboard-alert" + (DANGER_ALERT_TYPES.has(alert.type) ? " is-danger" : "")}
    >
      <span className="dashboard-alert-icon" aria-hidden="true">
        {ALERT_ICON[alert.type]}
      </span>
      <span>{alertText(alert)}</span>
    </Link>
  );
}

// Строка активной коллекции в верхнем ряду Dashboard — по прямому запросу
// владельца заменяет прежние карточки "Коллекции": вместо одной цифры
// (выручка) на коллекцию теперь целый набор показателей одной строкой.
function GiveawayRow({ giveaway }: { giveaway: DashboardGiveawayCard }) {
  const soldRatio = giveaway.max_tickets > 0 ? giveaway.tickets_issued / giveaway.max_tickets : 0;
  const soldPercent = Math.min(100, Math.round(soldRatio * 100));
  const freeRatio =
    giveaway.max_tickets > 0 ? giveaway.free_tickets_count / giveaway.max_tickets : 1;
  const paidPercent =
    giveaway.online_payments_total > 0
      ? Math.round((giveaway.online_payments_succeeded / giveaway.online_payments_total) * 100)
      : null;

  return (
    <Link to={`/giveaways/${giveaway.id}`} className="giveaway-row">
      <div className="giveaway-row-header">
        <span className="giveaway-row-name">{giveaway.name}</span>
        {giveaway.opened_at && (
          <span className="giveaway-row-days">{daysSince(giveaway.opened_at)} дн. в продаже</span>
        )}
      </div>
      <div className="giveaway-row-tiles">
        <div className="giveaway-row-tile">
          <div className="giveaway-row-tile-value" title={formatMoney(giveaway.revenue_total)}>
            {formatMoneyCompact(giveaway.revenue_total)}
          </div>
          <div className="giveaway-row-tile-label">Выручка</div>
        </div>

        <div className="giveaway-row-tile">
          <div className="giveaway-card-progress-row">
            <div className="giveaway-card-progress-track">
              <div
                className={
                  "giveaway-card-progress-fill" +
                  (freeRatio <= LOW_STOCK_FREE_RATIO ? " is-low-stock" : "")
                }
                style={{ width: `${soldPercent}%` }}
              />
            </div>
            <span className="giveaway-card-progress-label">{soldPercent}%</span>
          </div>
          <div className="giveaway-row-tile-label">
            Выдано {giveaway.tickets_issued} из {giveaway.max_tickets} · осталось{" "}
            {giveaway.free_tickets_count}
          </div>
        </div>

        <div className="giveaway-row-tile">
          <div
            className="giveaway-row-tile-value"
            title={formatMoney(giveaway.average_check_total)}
          >
            {formatMoneyRounded(giveaway.average_check_total)}
          </div>
          <div className="giveaway-row-tile-label">
            Средний чек · TG {formatMoney(giveaway.average_check_telegram)} · VK{" "}
            {formatMoney(giveaway.average_check_vk)} · офлайн{" "}
            {formatMoney(giveaway.average_check_offline)}
          </div>
        </div>

        <div className="giveaway-row-tile">
          <div className="giveaway-row-tile-value">
            {paidPercent !== null ? `${paidPercent}%` : "—"}
          </div>
          <div className="giveaway-row-tile-label">
            Оплачено счетов
            {paidPercent !== null
              ? ` (${giveaway.online_payments_succeeded} из ${giveaway.online_payments_total})`
              : " (нет онлайн-счетов)"}
          </div>
        </div>

        <div className="giveaway-row-tile giveaway-row-tile-spark">
          <Sparkline data={giveaway.sparkline} width={100} height={32} />
          <div className="giveaway-row-tile-label">14 дней</div>
        </div>
      </div>
    </Link>
  );
}

interface FunnelSegmentSpec {
  key: string;
  label: string;
  value: number;
  colorVar: string;
}

// Одна строка воронки (Онлайн/Ручные) — один горизонтальный стек из 4
// сегментов по статусу. Общая легенда рисуется один раз снаружи (см. ниже).
function FunnelBar({ label, segments }: { label: string; segments: FunnelSegmentSpec[] }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  return (
    <div className="funnel-row">
      <div className="funnel-row-label">{label}</div>
      <div className="funnel-bar">
        {total === 0 ? (
          <div
            className="funnel-segment"
            style={{ width: "100%", background: "var(--color-border)" }}
          />
        ) : (
          segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <div
                key={s.key}
                className="funnel-segment"
                style={{ width: `${(s.value / total) * 100}%`, background: `var(${s.colorVar})` }}
                title={`${s.label}: ${s.value}`}
              />
            ))
        )}
      </div>
      <div className="funnel-row-total">{total}</div>
    </div>
  );
}

export function DashboardPage() {
  const { hasPermission } = useAuth();
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    const tick = async () => {
      try {
        const next = await DashboardApi.get();
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Не удалось обновить Dashboard");
        }
      } finally {
        if (!cancelled) {
          timeoutId = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
        }
      }
    };
    void tick();

    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, []);

  const activeGiveaways = useMemo(() => (data?.giveaways ?? []).filter(isOpenForSale), [data]);

  const trendData = useMemo(
    () => (data?.sales_trend ?? []).map((row) => ({ x: row.period, y: row.amount })),
    [data],
  );

  // Дельта к предыдущим 30 дням у заголовка графика — сумма самого графика
  // (trendData) против sales_trend_prev_total, который приходит уже готовым.
  const trendDelta = useMemo(() => {
    if (!data) return null;
    const trendTotal = trendData.reduce((sum, p) => sum + p.y, 0);
    return formatPercentDelta(trendTotal, data.sales_trend_prev_total);
  }, [data, trendData]);

  const todayDelta = data ? formatPercentDelta(data.revenue_today, data.revenue_yesterday) : null;

  const channelGroups: BarGroupDatum[] = useMemo(
    () =>
      (data?.revenue_by_channel ?? []).map((row) => ({
        label: CHANNEL_LABELS[row.channel] ?? row.channel,
        values: { [row.channel]: row.amount },
      })),
    [data],
  );
  const channelSeries: BarSeriesSpec[] = useMemo(
    () =>
      (data?.revenue_by_channel ?? []).map((row, i) => ({
        key: row.channel,
        label: CHANNEL_LABELS[row.channel] ?? row.channel,
        colorVar: `--series-${(i % 6) + 1}`,
      })),
    [data],
  );

  if (!data) return <LoadingState />;

  const onlineFunnelSegments: FunnelSegmentSpec[] = [
    {
      key: "pending",
      label: "Ожидание",
      value: data.funnel_online.pending,
      colorVar: "--color-warning",
    },
    {
      key: "succeeded",
      label: "Оплачено",
      value: data.funnel_online.succeeded,
      colorVar: "--color-success",
    },
    {
      key: "failed",
      label: "Не оплачено",
      value: data.funnel_online.failed + data.funnel_online.cancelled,
      colorVar: "--color-danger",
    },
    {
      key: "refunded",
      label: "Возврат",
      value: data.funnel_online.refunded,
      colorVar: "--color-info",
    },
  ];
  const manualFunnelSegments: FunnelSegmentSpec[] = [
    {
      key: "pending",
      label: "Ожидание",
      value: data.funnel_manual.pending,
      colorVar: "--color-warning",
    },
    {
      key: "confirmed",
      label: "Подтверждено",
      value: data.funnel_manual.confirmed,
      colorVar: "--color-success",
    },
    {
      key: "cancelled",
      label: "Отменено",
      value: data.funnel_manual.cancelled,
      colorVar: "--color-danger",
    },
    {
      key: "refunded",
      label: "Возврат",
      value: data.funnel_manual.refunded,
      colorVar: "--color-info",
    },
  ];

  return (
    <div>
      <h1>
        Dashboard <span className="live-dot" title="Обновляется каждые 3 сек" />
      </h1>
      {error && <div className="error">{error}</div>}

      <section>
        <h2>Активные коллекции</h2>
        {activeGiveaways.length === 0 ? (
          <p className="card-label">Сейчас нет коллекций, открытых для продаж</p>
        ) : (
          <div className="giveaway-rows">
            {activeGiveaways.map((g) => (
              <GiveawayRow key={g.id} giveaway={g} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2>Итого по всем коллекциям</h2>
        <div className="cards">
          <div className="card">
            <div className="card-value">{data.participants_count}</div>
            <div className="card-label">Участников</div>
          </div>
          <div className="card">
            <div className="card-value">{data.tickets_issued_count}</div>
            <div className="card-label">Экземпляров выдано</div>
          </div>
          <div className="card">
            <div className="card-value" title={formatMoney(data.revenue_online)}>
              {formatMoneyCompact(data.revenue_online)}
            </div>
            <div className="card-label">Эквайринг</div>
          </div>
          <div className="card">
            <div className="card-value" title={formatMoney(data.revenue_offline)}>
              {formatMoneyCompact(data.revenue_offline)}
            </div>
            <div className="card-label">Наличные (оператор)</div>
          </div>
          <div className="card is-hero">
            <div className="card-value" title={formatMoney(data.revenue_total)}>
              {formatMoneyCompact(data.revenue_total)}
            </div>
            <div className="card-label">Итого выручка</div>
          </div>
          <div className="card">
            <div className="card-value">{data.giveaways_count}</div>
            <div className="card-label">Коллекций</div>
          </div>
          <div className="card">
            <div className="card-value" title={formatMoney(data.average_check)}>
              {formatMoneyRounded(data.average_check)}
            </div>
            <div className="card-label">Средний чек (онлайн)</div>
          </div>
          <div className="card">
            <div className="card-value" title={formatMoney(data.revenue_today)}>
              {formatMoneyCompact(data.revenue_today)}
            </div>
            <div className="card-label dashboard-kpi-delta-row">
              <span>Сегодня · вчера {formatMoney(data.revenue_yesterday)}</span>
              {todayDelta && (
                <Badge tone={todayDelta.startsWith("-") ? "danger" : "success"}>{todayDelta}</Badge>
              )}
            </div>
          </div>
          <div className="card">
            <div className="card-value">{data.sales_velocity_last_hour.tickets_count}</div>
            <div className="card-label">
              За последний час · {formatMoney(data.sales_velocity_last_hour.revenue)}
            </div>
          </div>
        </div>
      </section>

      {data.alerts.length > 0 && (
        <section>
          <h2>Требует внимания</h2>
          <div className="dashboard-alerts">
            {data.alerts.map((alert, i) => {
              const key = [
                alert.type,
                alert.giveaway_id,
                alert.manual_registration_id,
                alert.payment_id,
                i,
              ].join("-");
              return <DashboardAlertRow key={key} alert={alert} />;
            })}
          </div>
        </section>
      )}

      <section>
        <div className="viz-chart-header">
          <h2>Динамика продаж за 30 дней</h2>
          {trendDelta && (
            <Badge tone={trendDelta.startsWith("-") ? "danger" : "success"}>
              {trendDelta} к прошлым 30 дням
            </Badge>
          )}
        </div>
        <div className="viz-chart-card is-full-width">
          <LineChart
            data={trendData}
            formatValue={formatMoney}
            formatAxisValue={formatMoneyCompact}
            formatX={formatDayLabel}
            height={220}
          />
        </div>
      </section>

      {channelGroups.length > 0 && (
        <section>
          <h2>Продажи по каналу связи</h2>
          <div className="viz-chart-card">
            <BarChart
              groups={channelGroups}
              series={channelSeries}
              stacked={false}
              showLegend={false}
              formatValue={formatMoney}
              formatAxisValue={formatMoneyCompact}
              height={140}
            />
          </div>
        </section>
      )}

      <section>
        <h2>Топ участников по сумме покупок</h2>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Участник</th>
                <th>Телефон</th>
                <th>Экземпляров</th>
                <th>Сумма покупок</th>
              </tr>
            </thead>
            <tbody>
              {data.top_participants.length === 0 && <EmptyStateRow colSpan={4} />}
              {data.top_participants.map((p) => (
                <tr key={p.participant_id}>
                  <td>{p.full_name ?? "—"}</td>
                  <td>{p.phone}</td>
                  <td>{p.tickets_count}</td>
                  <td>{formatMoney(p.revenue_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Воронка продаж</h2>
        <div className="viz-chart-card">
          <FunnelBar label="Онлайн" segments={onlineFunnelSegments} />
          <FunnelBar label="Ручные" segments={manualFunnelSegments} />
          <div className="viz-legend">
            <span className="viz-legend-item">
              <span className="viz-legend-swatch" style={{ background: "var(--color-warning)" }} />
              Ожидание
            </span>
            <span className="viz-legend-item">
              <span className="viz-legend-swatch" style={{ background: "var(--color-success)" }} />
              Оплачено / подтверждено
            </span>
            <span className="viz-legend-item">
              <span className="viz-legend-swatch" style={{ background: "var(--color-danger)" }} />
              Не оплачено / отменено
            </span>
            <span className="viz-legend-item">
              <span className="viz-legend-swatch" style={{ background: "var(--color-info)" }} />
              Возврат
            </span>
          </div>
        </div>
      </section>

      {hasPermission("view_bank_reconciliation") && (
        <section>
          <h2>Сверка банковской выписки</h2>
          <BankReconciliationStatusPanel />
        </section>
      )}
    </div>
  );
}
