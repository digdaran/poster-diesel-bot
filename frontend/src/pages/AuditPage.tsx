import { useEffect, useState } from "react";
import { AuditApi } from "../api/resources";
import type { AuditLogEntry } from "../api/types";

export function AuditPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);

  useEffect(() => {
    void AuditApi.list().then(setEntries);
  }, []);

  return (
    <div>
      <h1>Журнал аудита</h1>
      <table>
        <thead>
          <tr>
            <th>Время</th>
            <th>Действие</th>
            <th>Инициатор</th>
            <th>Сущность</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString("ru-RU")}</td>
              <td>{e.action}</td>
              <td>
                {e.actor_label} ({e.actor_type})
              </td>
              <td>{e.entity_type ? `${e.entity_type}#${e.entity_id}` : "—"}</td>
              <td>{e.ip_address ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
