import { useEffect, useState } from "react";
import { ReportsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export function ReportsPage() {
  const { hasPermission } = useAuth();
  const [summary, setSummary] = useState<{
    revenue_total: number;
    successful_payments_count: number;
    average_check: number;
  } | null>(null);
  const [onlineOffline, setOnlineOffline] = useState<Record<
    string,
    { count: number; amount: number }
  > | null>(null);

  useEffect(() => {
    void ReportsApi.financialSummary().then(setSummary);
    void ReportsApi.onlineVsOffline().then(setOnlineOffline);
  }, []);

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
        <h2>Финансовая сводка</h2>
        {summary && (
          <ul>
            <li>Выручка: {(summary.revenue_total / 100).toFixed(2)} ₽</li>
            <li>Успешных платежей: {summary.successful_payments_count}</li>
            <li>Средний чек: {(summary.average_check / 100).toFixed(2)} ₽</li>
          </ul>
        )}
      </section>

      <section>
        <h2>Онлайн vs офлайн</h2>
        {onlineOffline && (
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
                  <td>{(value.amount / 100).toFixed(2)} ₽</td>
                </tr>
              ))}
            </tbody>
          </table>
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
