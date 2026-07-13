import { useEffect, useState } from "react";
import { SalesApi } from "../api/resources";
import type { Payment } from "../api/types";

export function SalesPage() {
  const [payments, setPayments] = useState<Payment[]>([]);

  useEffect(() => {
    void SalesApi.list().then(setPayments);
  }, []);

  return (
    <div>
      <h1>Продажи (онлайн-платежи)</h1>
      <table>
        <thead>
          <tr>
            <th>ID заказа</th>
            <th>Провайдер</th>
            <th>Сумма</th>
            <th>Кол-во</th>
            <th>Статус</th>
            <th>Создан</th>
          </tr>
        </thead>
        <tbody>
          {payments.map((p) => (
            <tr key={p.id}>
              <td>{p.order_id}</td>
              <td>{p.provider}</td>
              <td>{(p.amount / 100).toFixed(2)} ₽</td>
              <td>{p.quantity}</td>
              <td>{p.status}</td>
              <td>{new Date(p.created_at).toLocaleString("ru-RU")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
