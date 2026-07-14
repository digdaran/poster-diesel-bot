import { useEffect, useState } from "react";
import { ParticipantsApi } from "../api/resources";
import type { Participant } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { PaginationControls } from "../components/PaginationControls";

export function ParticipantsPage() {
  const { hasPermission } = useAuth();
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [phoneVerified, setPhoneVerified] = useState("");
  const [isBlocked, setIsBlocked] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");
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
    await ParticipantsApi.update(id, editingName);
    setEditingId(null);
    load();
  };

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
      <table>
        <thead>
          <tr>
            <th>Телефон</th>
            <th>Имя</th>
            <th>Подтверждён</th>
            <th>Заблокирован</th>
            <th>Регистрация</th>
            {(hasPermission("participant_block") || hasPermission("participant_edit")) && (
              <th>Действия</th>
            )}
          </tr>
        </thead>
        <tbody>
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
              <td>{p.phone_verified ? "Да" : "Нет"}</td>
              <td>{p.is_blocked ? "Да" : "Нет"}</td>
              <td>{new Date(p.created_at).toLocaleString("ru-RU")}</td>
              {(hasPermission("participant_block") || hasPermission("participant_edit")) && (
                <td className="actions">
                  {hasPermission("participant_edit") &&
                    (editingId === p.id ? (
                      <>
                        <button onClick={() => saveEdit(p.id)}>Сохранить</button>
                        <button onClick={() => setEditingId(null)}>Отмена</button>
                      </>
                    ) : (
                      <button onClick={() => startEdit(p)}>Изменить имя</button>
                    ))}
                  {hasPermission("participant_block") &&
                    (p.is_blocked ? (
                      <button onClick={() => ParticipantsApi.unblock(p.id).then(load)}>
                        Разблокировать
                      </button>
                    ) : (
                      <button onClick={() => ParticipantsApi.block(p.id).then(load)}>
                        Заблокировать
                      </button>
                    ))}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
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
