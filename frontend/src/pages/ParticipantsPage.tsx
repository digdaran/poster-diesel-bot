import { useEffect, useState } from "react";
import { ParticipantsApi } from "../api/resources";
import type { Participant } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";
import { useToast } from "../components/Toast";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime } from "../utils/format";

export function ParticipantsPage() {
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [phoneVerified, setPhoneVerified] = useState("");
  const [isBlocked, setIsBlocked] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");
  const [pendingId, setPendingId] = useState<number | null>(null);
  const { page, pageSize, setPage, setPageSize } = usePagination();

  const debouncedQuery = useDebouncedValue(query);

  const load = () =>
    void ParticipantsApi.list({
      q: debouncedQuery || undefined,
      phone_verified: phoneVerified ? phoneVerified === "true" : undefined,
      is_blocked: isBlocked ? isBlocked === "true" : undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
      page,
      page_size: pageSize,
    }).then((result) => {
      setParticipants(result.items);
      setTotal(result.total);
    });

  useEffect(load, [
    debouncedQuery,
    phoneVerified,
    isBlocked,
    createdFrom,
    createdTo,
    page,
    pageSize,
  ]);

  const startEdit = (p: Participant) => {
    setEditingId(p.id);
    setEditingName(p.full_name ?? "");
  };

  const saveEdit = async (id: number) => {
    setPendingId(id);
    try {
      await ParticipantsApi.update(id, editingName);
      setEditingId(null);
      showToast("Имя обновлено");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось сохранить имя", "error");
    } finally {
      setPendingId(null);
    }
  };

  const toggleBlock = async (p: Participant) => {
    setPendingId(p.id);
    try {
      if (p.is_blocked) {
        await ParticipantsApi.unblock(p.id);
        showToast("Участник разблокирован");
      } else {
        await ParticipantsApi.block(p.id);
        showToast("Участник заблокирован");
      }
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось выполнить действие", "error");
    } finally {
      setPendingId(null);
    }
  };

  const showActions = hasPermission("participant_block") || hasPermission("participant_edit");

  return (
    <div>
      <h1>Участники</h1>
      <div className="filters">
        <input
          placeholder="Поиск по телефону/имени"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={phoneVerified}
          onChange={(e) => {
            setPhoneVerified(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Подтверждён: любой</option>
          <option value="true">Подтверждён</option>
          <option value="false">Не подтверждён</option>
        </select>
        <select
          value={isBlocked}
          onChange={(e) => {
            setIsBlocked(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Заблокирован: любой</option>
          <option value="true">Заблокирован</option>
          <option value="false">Не заблокирован</option>
        </select>
        <label>
          Регистрация с{" "}
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
              <th>Телефон</th>
              <th>Имя</th>
              <th>Подтверждён</th>
              <th>Заблокирован</th>
              <th>Регистрация</th>
              {showActions && <th>Действия</th>}
            </tr>
          </thead>
          <tbody>
            {participants.length === 0 && <EmptyStateRow colSpan={showActions ? 6 : 5} />}
            {participants.map((p) => (
              <tr key={p.id}>
                <td>{p.phone}</td>
                <td>
                  {editingId === p.id ? (
                    <input value={editingName} onChange={(e) => setEditingName(e.target.value)} />
                  ) : (
                    (p.full_name ?? "—")
                  )}
                </td>
                <td>
                  <Badge tone={p.phone_verified ? "success" : "muted"}>
                    {p.phone_verified ? "Да" : "Нет"}
                  </Badge>
                </td>
                <td>
                  <Badge tone={p.is_blocked ? "danger" : "muted"}>
                    {p.is_blocked ? "Да" : "Нет"}
                  </Badge>
                </td>
                <td>{formatDateTime(p.created_at)}</td>
                {showActions && (
                  <td className="actions">
                    {hasPermission("participant_edit") &&
                      (editingId === p.id ? (
                        <>
                          <button disabled={pendingId === p.id} onClick={() => saveEdit(p.id)}>
                            Сохранить
                          </button>
                          <button className="button-secondary" onClick={() => setEditingId(null)}>
                            Отмена
                          </button>
                        </>
                      ) : (
                        <button onClick={() => startEdit(p)}>Изменить имя</button>
                      ))}
                    {hasPermission("participant_block") && (
                      <button
                        className={p.is_blocked ? undefined : "button-danger"}
                        disabled={pendingId === p.id}
                        onClick={() => void toggleBlock(p)}
                      >
                        {p.is_blocked ? "Разблокировать" : "Заблокировать"}
                      </button>
                    )}
                  </td>
                )}
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
