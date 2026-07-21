import { useEffect, useState } from "react";
import { GiveawaysApi, ReportsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { LoadingState, EmptyStateRow } from "../components/EmptyState";
import { formatMoney } from "../utils/format";
import type { Giveaway, RevenueByGiveawayRow } from "../api/types";

export function ReportsPage() {
  const { hasPermission } = useAuth();
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [giveawayId, setGiveawayId] = useState<number | undefined>(undefined);
  const [summary, setSummary] = useState<{
    revenue_online: number;
    revenue_offline: number;
    revenue_total: number;
    successful_payments_count: number;
    average_check: number;
  } | null>(null);
  const [onlineOffline, setOnlineOffline] = useState<Record<
    string,
    { count: number; amount: number }
  > | null>(null);
  const [byGiveaway, setByGiveaway] = useState<RevenueByGiveawayRow[] | null>(null);

  useEffect(() => {
    void GiveawaysApi.list().then(setGiveaways);
    void ReportsApi.revenueByGiveaway().then(setByGiveaway);
  }, []);

  useEffect(() => {
    void ReportsApi.financialSummary(giveawayId).then(setSummary);
    void ReportsApi.onlineVsOffline(giveawayId).then(setOnlineOffline);
  }, [giveawayId]);

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
        <h2>Выручка по коллекциям</h2>
        {byGiveaway ? (
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Коллекция</th>
                  <th>Эквайринг</th>
                  <th>Наличные (оператор)</th>
                  <th>Итого</th>
                  <th>Экземпляров выдано</th>
                </tr>
              </thead>
              <tbody>
                {byGiveaway.length === 0 && <EmptyStateRow colSpan={5} />}
                {byGiveaway.map((row) => (
                  <tr key={row.giveaway_id}>
                    <td>{row.giveaway_name}</td>
                    <td>{formatMoney(row.revenue_online)}</td>
                    <td>{formatMoney(row.revenue_offline)}</td>
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
            <li>Наличные (оператор): {formatMoney(summary.revenue_offline)}</li>
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
                    <td>{key === "online" ? "Онлайн" : "Офлайн"}</td>
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

      {hasPermission("reports_export") && (
        <section>
          <h2>Экспорт</h2>
          <button onClick={() => downloadReport("/api/reports/by-provider", "csv")}>
            По провайдерам — CSV
          </button>
          <button onClick={() => downloadReport("/api/reports/by-provider", "xlsx")}>
            По провайдерам — XLSX
          </button>
          <button onClick={() => downloadReport("/api/reports/participants", "xlsx")}>
            По участникам — XLSX
          </button>
        </section>
      )}
    </div>
  );
}
