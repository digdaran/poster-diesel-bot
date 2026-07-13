import { useEffect, useState } from "react";
import { ParticipantsApi } from "../api/resources";
import type { Participant } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export function ParticipantsPage() {
  const { hasPermission } = useAuth();
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [query, setQuery] = useState("");

  const load = () => void ParticipantsApi.list(query || undefined).then(setParticipants);

  useEffect(load, [query]);

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
            {hasPermission("participant_block") && <th>Действия</th>}
          </tr>
        </thead>
        <tbody>
          {participants.map((p) => (
            <tr key={p.id}>
              <td>{p.phone}</td>
              <td>{p.full_name ?? "—"}</td>
              <td>{p.phone_verified ? "Да" : "Нет"}</td>
              <td>{p.is_blocked ? "Да" : "Нет"}</td>
              <td>{new Date(p.created_at).toLocaleString("ru-RU")}</td>
              {hasPermission("participant_block") && (
                <td>
                  {p.is_blocked ? (
                    <button onClick={() => ParticipantsApi.unblock(p.id).then(load)}>
                      Разблокировать
                    </button>
                  ) : (
                    <button onClick={() => ParticipantsApi.block(p.id).then(load)}>
                      Заблокировать
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
