import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { BroadcastsApi } from "../api/resources";
import type { Broadcast } from "../api/types";

export function BroadcastsPage() {
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([]);
  const [form, setForm] = useState({ title: "", message_text: "", segment: "all" });
  const [error, setError] = useState<string | null>(null);

  const load = () => void BroadcastsApi.list().then(setBroadcasts);
  useEffect(load, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await BroadcastsApi.create({
        title: form.title,
        message_text: form.message_text,
        audience_filter: { segment: form.segment },
      });
      setForm({ title: "", message_text: "", segment: "all" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать рассылку");
    }
  };

  return (
    <div>
      <h1>Рассылки (только Telegram)</h1>
      <form onSubmit={onCreate} className="inline-form-column">
        <input
          placeholder="Заголовок"
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          required
        />
        <textarea
          placeholder="Текст сообщения"
          rows={3}
          value={form.message_text}
          onChange={(e) => setForm({ ...form, message_text: e.target.value })}
          required
        />
        <select
          value={form.segment}
          onChange={(e) => setForm({ ...form, segment: e.target.value })}
        >
          <option value="all">Все</option>
          <option value="paid">Оплатившие</option>
          <option value="unpaid">Неоплатившие</option>
          <option value="online">Онлайн-покупатели</option>
          <option value="offline">Офлайн-покупатели</option>
        </select>
        <button type="submit">Создать черновик</button>
      </form>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>Заголовок</th>
            <th>Статус</th>
            <th>Статистика</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {broadcasts.map((b) => (
            <tr key={b.id}>
              <td>{b.title}</td>
              <td>{b.status}</td>
              <td>
                {b.stats.recipients !== undefined
                  ? `${b.stats.delivered}/${b.stats.recipients} доставлено`
                  : "—"}
              </td>
              <td>
                {b.status === "DRAFT" && (
                  <button onClick={() => BroadcastsApi.send(b.id).then(load)}>Отправить</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
