import { useEffect, useState } from "react";
import { GiveawaysApi, SalesApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";
import { Badge } from "../components/Badge";
import { ChannelBadges } from "../components/ChannelBadges";
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
  const [channel, setChannel] = useState("");
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
      channel: channel || undefined,
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
    channel,
    debouncedOrderId,
    debouncedParticipantQuery,
    createdFrom,
    createdTo,
  ]);

  const viewReceipt = async (paymentId: number) => {
    const receipts = await SalesApi.listReceipts(paymentId);
    const latest = receipts[0];
    if (!latest) return;
    const { blob } = await SalesApi.downloadReceipt(paymentId, latest.id);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/payments", {
      export: format,
      giveaway_id: giveawayId ? Number(giveawayId) : undefined,
      status_filter: statusFilter || undefined,
      provider: provider || undefined,
      channel: channel || undefined,
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
          <option value="requisites_qr">requisites_qr</option>
          <option value="mock">mock</option>
          <option value="tbank">tbank</option>
          <option value="vtb">vtb</option>
        </select>
        <select
          value={channel}
          onChange={(e) => {
            setChannel(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все каналы</option>
          <option value="telegram">Telegram</option>
          <option value="vk">VK</option>
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
              <th>Счёт №</th>
              <th>Коллекция</th>
              <th>Участник</th>
              <th>Провайдер</th>
              <th>Канал</th>
              <th>Сумма</th>
              <th>Кол-во</th>
              <th>Статус</th>
              <th>Квитанция</th>
              <th>Создан</th>
              <th>Подтверждён</th>
            </tr>
          </thead>
          <tbody>
            {payments.length === 0 && <EmptyStateRow colSpan={12} />}
            {payments.map((p) => (
              <tr key={p.id}>
                <td>{p.order_id}</td>
                <td>{p.invoice_no ?? "—"}</td>
                <td>{p.giveaway_name}</td>
                <td>{p.participant_full_name ?? p.participant_phone}</td>
                <td>{p.provider}</td>
                <td>
                  <ChannelBadges channel={p.channel} />
                </td>
                <td>{formatMoney(p.amount)}</td>
                <td>{p.quantity}</td>
                <td>
                  <Badge tone={STATUS_TONE[p.status] ?? "muted"}>{p.status}</Badge>
                  {p.oversold && (
                    <>
                      {" "}
                      <Badge tone="danger">нет номерков</Badge>
                    </>
                  )}
                </td>
                <td>
                  {p.receipt_count > 0 ? (
                    <button onClick={() => void viewReceipt(p.id)}>
                      Посмотреть{p.receipt_count > 1 ? ` (${p.receipt_count})` : ""}
                    </button>
                  ) : (
                    "—"
                  )}
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
