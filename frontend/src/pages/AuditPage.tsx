import { useEffect, useState } from "react";
import { AuditApi } from "../api/resources";
import type { AuditLogEntry } from "../api/types";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime } from "../utils/format";

export function AuditPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);

  useEffect(() => {
    void AuditApi.list().then(setEntries);
  }, []);

  return (
    <div>
      <h1>Журнал аудита</h1>
      <div className="table-wrapper">
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
            {entries.length === 0 && <EmptyStateRow colSpan={5} />}
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{formatDateTime(e.created_at)}</td>
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
    </div>
  );
}
