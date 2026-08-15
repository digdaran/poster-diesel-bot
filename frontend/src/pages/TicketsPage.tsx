import { useEffect, useState } from "react";
import { GiveawaysApi, TicketsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";
import { ChannelBadges } from "../components/ChannelBadges";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime } from "../utils/format";
import type { Giveaway, Ticket } from "../api/types";

export function TicketsPage() {
  const { hasPermission } = useAuth();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [total, setTotal] = useState(0);
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const { page, pageSize, setPage, setPageSize } = usePagination();

  const [giveawayId, setGiveawayId] = useState("");
  const [fullCode, setFullCode] = useState("");
  const [source, setSource] = useState("");
  const [channel, setChannel] = useState("");
  const [participantQuery, setParticipantQuery] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");

  const debouncedFullCode = useDebouncedValue(fullCode);
  const debouncedParticipantQuery = useDebouncedValue(participantQuery);

  useEffect(() => {
    void GiveawaysApi.list().then(setGiveaways);
  }, []);

  useEffect(() => {
    void TicketsApi.list({
      page,
      page_size: pageSize,
      giveaway_id: giveawayId ? Number(giveawayId) : undefined,
      full_code: debouncedFullCode || undefined,
      source: source || undefined,
      channel: channel || undefined,
      participant_query: debouncedParticipantQuery || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
    }).then((result) => {
      setTickets(result.items);
      setTotal(result.total);
    });
  }, [
    page,
    pageSize,
    giveawayId,
    debouncedFullCode,
    source,
    channel,
    debouncedParticipantQuery,
    createdFrom,
    createdTo,
  ]);

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/tickets", {
      export: format,
      giveaway_id: giveawayId ? Number(giveawayId) : undefined,
      full_code: debouncedFullCode || undefined,
      source: source || undefined,
      channel: channel || undefined,
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
      <h1>Номера</h1>

      <div className="filters">
        <input
          placeholder="Код номера"
          value={fullCode}
          onChange={(e) => {
            setFullCode(e.target.value);
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
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Любой источник</option>
          <option value="online">Онлайн</option>
          <option value="manual">Ручная</option>
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
        <label>
          Выдан с{" "}
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
              <th>Код</th>
              <th>Коллекция</th>
              <th>Участник</th>
              <th>Телефон</th>
              <th>Источник</th>
              <th>Канал</th>
              <th>Выдан</th>
            </tr>
          </thead>
          <tbody>
            {tickets.length === 0 && <EmptyStateRow colSpan={7} />}
            {tickets.map((t) => (
              <tr key={t.id}>
                <td>{t.full_code}</td>
                <td>{t.giveaway_name}</td>
                <td>{t.participant_full_name ?? "—"}</td>
                <td>{t.participant_phone}</td>
                <td>{t.source === "online" ? "Онлайн" : "Ручная"}</td>
                <td>
                  <ChannelBadges channel={t.channel} />
                </td>
                <td>{formatDateTime(t.created_at)}</td>
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
