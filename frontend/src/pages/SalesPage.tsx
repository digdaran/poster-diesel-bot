import { useEffect, useState } from "react";
import { GiveawaysApi, SalesApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney, formatDateTime } from "../utils/format";
import type { Giveaway, Payment } from "../api/types";

const STATUS_TONE: Record<string, "success" | "danger" | "muted"> = {
  SUCCEEDED: "success",
  FAILED: "danger",
  PENDING: "muted",
};

export function SalesPage() {
  const { hasPermission } = useAuth();
  const [payments, setPayments] = useState<Payment[]>([]);
  const [total, setTotal] = useState(0);
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const { page, pageSize, setPage, setPageSize } = usePagination();

  const [giveawayId, setGiveawayId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [provider, setProvider] = useState("");
  const [orderId, setOrderId] = useState("");
  const [participantQuery, setParticipantQuery] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");

  const debouncedOrderId = useDebouncedValue(orderId);
  const debouncedParticipantQuery = useDebouncedValue(participantQuery);

  useEffect(() => {
    void GiveawaysApi.list().then(setGiveaways);
  }, []);

  useEffect(() => {
    void SalesApi.list({
      page,
      page_size: pageSize,
      giveaway_id: giveawayId ? Number(giveawayId) : undefined,
      status_filter: statusFilter || undefined,
      provider: provider || undefined,
      order_id: debouncedOrderId || undefined,
      participant_query: debouncedParticipantQuery || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
    }).then((result) => {
      setPayments(result.items);
      setTotal(result.total);
    });
  }, [
    page,
    pageSize,
    giveawayId,
    statusFilter,
    provider,
    debouncedOrderId,
    debouncedParticipantQuery,
    createdFrom,
    createdTo,
  ]);

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/payments", {
      export: format,
      giveaway_id: giveawayId ? Number(giveawayId) : undefined,
      status_filter: statusFilter || undefined,
      provider: provider || undefined,
      order_id: debouncedOrderId || undefined,
      participant_query: debouncedParticipantQuery || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
    });
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

      <div className="filters">
        <input
          placeholder="ID заказа"
          value={orderId}
          onChange={(e) => {
            setOrderId(e.target.value);
            setPage(1);
          }}
        />
        <input
          placeholder="Участник (телефон/имя)"
          value={participantQuery}
          onChange={(e) => {
            setParticipantQuery(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={giveawayId}
          onChange={(e) => {
            setGiveawayId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все коллекции</option>
          {giveaways.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <select
          value={provider}
          onChange={(e) => {
            setProvider(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все провайдеры</option>
          <option value="mock">mock</option>
          <option value="tbank">tbank</option>
          <option value="vtb">vtb</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все статусы</option>
          <option value="PENDING">PENDING</option>
          <option value="SUCCEEDED">SUCCEEDED</option>
          <option value="FAILED">FAILED</option>
        </select>
        <label>
          Создан с{" "}
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

      {hasPermission("sales_export") && (
        <div>
          <button onClick={() => downloadReport("csv")}>Экспорт CSV</button>
          <button onClick={() => downloadReport("xlsx")}>Экспорт XLSX</button>
        </div>
      )}
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>ID заказа</th>
              <th>Коллекция</th>
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
            {payments.length === 0 && <EmptyStateRow colSpan={9} />}
            {payments.map((p) => (
              <tr key={p.id}>
                <td>{p.order_id}</td>
                <td>{p.giveaway_name}</td>
                <td>{p.participant_full_name ?? p.participant_phone}</td>
                <td>{p.provider}</td>
                <td>{formatMoney(p.amount)}</td>
                <td>{p.quantity}</td>
                <td>
                  <Badge tone={STATUS_TONE[p.status] ?? "muted"}>{p.status}</Badge>
                </td>
                <td>{formatDateTime(p.created_at)}</td>
                <td>{p.confirmed_at ? formatDateTime(p.confirmed_at) : "—"}</td>
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
