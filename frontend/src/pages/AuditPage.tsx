import { useEffect, useState } from "react";
import { AuditApi } from "../api/resources";
import type { AuditLogEntry } from "../api/types";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime } from "../utils/format";

const ENTITY_TYPES = [
  { value: "participant", label: "Участник" },
  { value: "giveaway", label: "Коллекция" },
  { value: "payment", label: "Платёж" },
  { value: "manual_registration", label: "Ручная регистрация" },
  { value: "panel_user", label: "Пользователь панели" },
  { value: "broadcast", label: "Рассылка" },
];

export function AuditPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, setPageSize } = usePagination();

  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [entityId, setEntityId] = useState("");
  const [actorQuery, setActorQuery] = useState("");
  const [ipAddress, setIpAddress] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");

  const debouncedAction = useDebouncedValue(action);
  const debouncedEntityId = useDebouncedValue(entityId);
  const debouncedActorQuery = useDebouncedValue(actorQuery);
  const debouncedIpAddress = useDebouncedValue(ipAddress);

  useEffect(() => {
    void AuditApi.list({
      action: debouncedAction || undefined,
      entity_type: entityType || undefined,
      entity_id: debouncedEntityId ? Number(debouncedEntityId) : undefined,
      actor_query: debouncedActorQuery || undefined,
      ip_address: debouncedIpAddress || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
      page,
      page_size: pageSize,
    }).then((result) => {
      setEntries(result.items);
      setTotal(result.total);
    });
  }, [
    debouncedAction,
    entityType,
    debouncedEntityId,
    debouncedActorQuery,
    debouncedIpAddress,
    createdFrom,
    createdTo,
    page,
    pageSize,
  ]);

  return (
    <div>
      <h1>Журнал аудита</h1>
      <div className="filters">
        <input
          placeholder="Действие (например, participant)"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
        />
        <input
          placeholder="Инициатор"
          value={actorQuery}
          onChange={(e) => {
            setActorQuery(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={entityType}
          onChange={(e) => {
            setEntityType(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все сущности</option>
          {ENTITY_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <input
          type="number"
          placeholder="ID сущности"
          style={{ width: "6em" }}
          value={entityId}
          onChange={(e) => {
            setEntityId(e.target.value);
            setPage(1);
          }}
        />
        <input
          placeholder="IP"
          value={ipAddress}
          onChange={(e) => {
            setIpAddress(e.target.value);
            setPage(1);
          }}
        />
        <label>
          Время с{" "}
          <input
            type="date"
            value={createdFrom}
            onChange={(e) => {
              setCreatedFrom(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          по{" "}
          <input
            type="date"
            value={createdTo}
            onChange={(e) => {
              setCreatedTo(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
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
      <PaginationControls
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />
    </div>
  );
}
