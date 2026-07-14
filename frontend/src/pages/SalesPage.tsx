import { useEffect, useState } from "react";
import { SalesApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Payment } from "../api/types";

export function SalesPage() {
  const { hasPermission } = useAuth();
  const [payments, setPayments] = useState<Payment[]>([]);

  useEffect(() => {
    void SalesApi.list().then(setPayments);
  }, []);

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/payments", { export: format });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <h1>Продажи (онлайн-платежи)</h1>
      {hasPermission("sales_export") && (
        <div>
          <button onClick={() => downloadReport("csv")}>Экспорт CSV</button>
          <button onClick={() => downloadReport("xlsx")}>Экспорт XLSX</button>
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>ID заказа</th>
            <th>Розыгрыш</th>
            <th>Участник</th>
            <th>Провайдер</th>
            <th>Сумма</th>
            <th>Кол-во</th>
            <th>Статус</th>
            <th>Создан</th>
            <th>Подтверждён</th>
          </tr>
        </thead>
        <tbody>
          {payments.map((p) => (
            <tr key={p.id}>
              <td>{p.order_id}</td>
              <td>{p.giveaway_name}</td>
              <td>{p.participant_full_name ?? p.participant_phone}</td>
              <td>{p.provider}</td>
              <td>{(p.amount / 100).toFixed(2)} ₽</td>
              <td>{p.quantity}</td>
              <td>{p.status}</td>
              <td>{new Date(p.created_at).toLocaleString("ru-RU")}</td>
              <td>{p.confirmed_at ? new Date(p.confirmed_at).toLocaleString("ru-RU") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
