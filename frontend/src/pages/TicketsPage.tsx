import { useEffect, useState } from "react";
import { TicketsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Ticket } from "../api/types";

export function TicketsPage() {
  const { hasPermission } = useAuth();
  const [tickets, setTickets] = useState<Ticket[]>([]);

  useEffect(() => {
    void TicketsApi.list().then(setTickets);
  }, []);

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/tickets", { export: format });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <h1>Номерки</h1>
      {hasPermission("sales_export") && (
        <div>
          <button onClick={() => downloadReport("csv")}>Экспорт CSV</button>
          <button onClick={() => downloadReport("xlsx")}>Экспорт XLSX</button>
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>Код</th>
            <th>Розыгрыш</th>
            <th>Участник</th>
            <th>Источник</th>
            <th>Выдан</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <tr key={t.id}>
              <td>{t.full_code}</td>
              <td>{t.giveaway_name}</td>
              <td>{t.participant_full_name ?? t.participant_phone}</td>
              <td>{t.source === "online" ? "Онлайн" : "Ручная"}</td>
              <td>{new Date(t.created_at).toLocaleString("ru-RU")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
