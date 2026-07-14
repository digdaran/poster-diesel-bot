import { useEffect, useState } from "react";
import { ParticipantsApi } from "../api/resources";
import type { Participant } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export function ParticipantsPage() {
  const { hasPermission } = useAuth();
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");

  const load = () => void ParticipantsApi.list(query || undefined).then(setParticipants);

  useEffect(load, [query]);

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
      <input
        placeholder="Поиск по телефону/имени"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
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
    </div>
  );
}
