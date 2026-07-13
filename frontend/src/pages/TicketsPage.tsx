import { useEffect, useState } from "react";
import { TicketsApi } from "../api/resources";
import type { Ticket } from "../api/types";

export function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);

  useEffect(() => {
    void TicketsApi.list().then(setTickets);
  }, []);

  return (
    <div>
      <h1>Номерки</h1>
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
              <td>{t.giveaway_id}</td>
              <td>{t.participant_id}</td>
              <td>{t.source === "online" ? "Онлайн" : "Ручная"}</td>
              <td>{new Date(t.created_at).toLocaleString("ru-RU")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
