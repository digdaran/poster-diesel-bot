import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { BroadcastsApi } from "../api/resources";
import type { Broadcast } from "../api/types";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";

const STATUS_TONE: Record<string, "success" | "danger" | "info" | "muted"> = {
  SENT: "success",
  SENDING: "info",
  FAILED: "danger",
  DRAFT: "muted",
};

// Отправка уходит в фоновую задачу на backend (см. DECISIONS_LOG.md №79) —
// пока хотя бы одна рассылка в статусе SENDING, опрашиваем список, чтобы
// увидеть финальный SENT/FAILED без ручного обновления страницы.
const POLL_INTERVAL_MS = 3000;

export function BroadcastsPage() {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [broadcasts, setBroadcasts] = useState<Broadcast[]>([]);
  const [form, setForm] = useState({ title: "", message_text: "", segment: "all" });
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const broadcastsRef = useRef<Broadcast[]>([]);

  const load = useCallback(async () => {
    const data = await BroadcastsApi.list();
    broadcastsRef.current = data;
    setBroadcasts(data);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    const tick = async () => {
      if (broadcastsRef.current.some((b) => b.status === "SENDING")) {
        await load();
      }
      if (!cancelled) {
        timeoutId = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
      }
    };
    timeoutId = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [load]);

  const { run: onCreate, pending: creating } = useAsyncAction(async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await BroadcastsApi.create({
        title: form.title,
        message_text: form.message_text,
        audience_filter: { segment: form.segment },
      });
      setForm({ title: "", message_text: "", segment: "all" });
      showToast("Черновик рассылки создан");
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать рассылку");
    }
  });

  const onSend = async (b: Broadcast) => {
    const confirmed = await confirm(
      `Отправить рассылку «${b.title}»? Сообщение уйдёт всем получателям сегмента и отменить отправку будет нельзя.`,
    );
    if (!confirmed) return;
    setPendingId(b.id);
    try {
      await BroadcastsApi.send(b.id);
      showToast("Отправка запущена — статус обновится, когда рассылка завершится");
      void load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось отправить рассылку", "error");
    } finally {
      setPendingId(null);
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
        <button type="submit" disabled={creating}>
          {creating ? "Создаём…" : "Создать черновик"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      <div className="table-wrapper">
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
            {broadcasts.length === 0 && <EmptyStateRow colSpan={4} />}
            {broadcasts.map((b) => (
              <tr key={b.id}>
                <td>{b.title}</td>
                <td>
                  <Badge tone={STATUS_TONE[b.status] ?? "muted"}>{b.status}</Badge>
                </td>
                <td>
                  {b.stats.recipients !== undefined
                    ? `${b.stats.delivered}/${b.stats.recipients} доставлено` +
                      (b.stats.queued ? `, ещё ${b.stats.queued} в очереди` : "")
                    : "—"}
                </td>
                <td>
                  {b.status === "DRAFT" && (
                    <button disabled={pendingId === b.id} onClick={() => void onSend(b)}>
                      Отправить
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
