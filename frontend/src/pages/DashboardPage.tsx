import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DashboardApi } from "../api/resources";
import type { Dashboard, DashboardAlert, DashboardGiveawayCard } from "../api/types";
import { LoadingState } from "../components/EmptyState";
import { Badge } from "../components/Badge";
import { LineChart } from "../components/charts/LineChart";
import { formatMoney } from "../utils/format";

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

function giveawayStatusBadge(g: DashboardGiveawayCard) {
  if (g.is_archived) {
    return <Badge tone="muted">В архиве</Badge>;
  }
  if (g.is_closed_forever) {
    return <Badge tone="muted">Закрыта навсегда</Badge>;
  }
  if (g.is_locked) {
    return <Badge tone="danger">Приостановлена</Badge>;
  }
  if (g.is_registration_open) {
    return <Badge tone="success">Открыта</Badge>;
  }
  return <Badge tone="muted">Не открыта</Badge>;
}

function GiveawayCard({ giveaway }: { giveaway: DashboardGiveawayCard }) {
  const soldRatio = giveaway.max_tickets > 0 ? giveaway.tickets_issued / giveaway.max_tickets : 0;
  const soldPercent = Math.min(100, Math.round(soldRatio * 100));
  const freeRatio =
    giveaway.max_tickets > 0 ? giveaway.free_tickets_count / giveaway.max_tickets : 1;
  return (
    <Link
      to={`/giveaways/${giveaway.id}`}
      className={"giveaway-card" + (giveaway.is_archived ? " is-archived" : "")}
    >
      <div className="giveaway-card-header">
        <span className="giveaway-card-name">{giveaway.name}</span>
        {giveawayStatusBadge(giveaway)}
      </div>
      <div className="giveaway-card-revenue">{formatMoney(giveaway.revenue_total)}</div>
      <div className="giveaway-card-progress-row">
        <div className="giveaway-card-progress-track">
          <div
            className={
              "giveaway-card-progress-fill" +
              (giveaway.is_registration_open && freeRatio <= LOW_STOCK_FREE_RATIO
                ? " is-low-stock"
                : "")
            }
            style={{ width: `${soldPercent}%` }}
          />
        </div>
        <span className="giveaway-card-progress-label">{soldPercent}%</span>
      </div>
      <div className="giveaway-card-meta">
        Выдано {giveaway.tickets_issued} из {giveaway.max_tickets} · осталось{" "}
        {giveaway.free_tickets_count}
      </div>
      {giveaway.opened_at && (
        <div className="giveaway-card-meta">{daysSince(giveaway.opened_at)} дн. в продаже</div>
      )}
    </Link>
  );
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

export function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [showAllGiveaways, setShowAllGiveaways] = useState(false);
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

  const visibleGiveaways = useMemo(() => {
    if (!data) return [];
    return showAllGiveaways ? data.giveaways : data.giveaways.filter((g) => g.is_registration_open);
  }, [data, showAllGiveaways]);

  const trendData = useMemo(
    () => (data?.sales_trend ?? []).map((row) => ({ x: row.period, y: row.amount })),
    [data],
  );

  if (!data) return <LoadingState />;

  return (
    <div>
      <h1>
        Dashboard <span className="live-dot" title="Обновляется каждые 3 сек" />
      </h1>
      {error && <div className="error">{error}</div>}

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
          <div className="card-value">{formatMoney(data.revenue_online)}</div>
          <div className="card-label">Эквайринг</div>
        </div>
        <div className="card">
          <div className="card-value">{formatMoney(data.revenue_offline)}</div>
          <div className="card-label">Наличные (оператор)</div>
        </div>
        <div className="card is-hero">
          <div className="card-value">{formatMoney(data.revenue_total)}</div>
          <div className="card-label">Итого выручка</div>
        </div>
        <div className="card">
          <div className="card-value">{data.giveaways_count}</div>
          <div className="card-label">Коллекций</div>
        </div>
      </div>

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
        <h2>Динамика продаж за 30 дней</h2>
        <div className="viz-chart-card">
          <LineChart
            data={trendData}
            formatValue={formatMoney}
            formatX={formatDayLabel}
            height={160}
          />
        </div>
      </section>

      <section>
        <div className="viz-chart-header">
          <h2>Коллекции</h2>
          <div className="segmented" role="group" aria-label="Какие коллекции показывать">
            <button
              type="button"
              className={!showAllGiveaways ? "active" : ""}
              onClick={() => setShowAllGiveaways(false)}
            >
              Только открытые
            </button>
            <button
              type="button"
              className={showAllGiveaways ? "active" : ""}
              onClick={() => setShowAllGiveaways(true)}
            >
              Показать все
            </button>
          </div>
        </div>
        <div className="giveaway-cards">
          {visibleGiveaways.length === 0 && (
            <p className="card-label">
              {showAllGiveaways ? "Нет коллекций" : "Нет коллекций, открытых для продаж"}
            </p>
          )}
          {visibleGiveaways.map((g) => (
            <GiveawayCard key={g.id} giveaway={g} />
          ))}
        </div>
      </section>
    </div>
  );
}
