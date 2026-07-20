import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GiveawaysApi } from "../api/resources";
import type { Giveaway } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney } from "../utils/format";

export function GiveawaysPage() {
  const { hasPermission } = useAuth();
  const canEdit = hasPermission("giveaway_edit");
  const canLock = hasPermission("giveaway_lock");
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", prefix: "", ticket_price: "", max_tickets: "" });
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);

  const load = () => void GiveawaysApi.list().then(setGiveaways);
  useEffect(load, []);

  const { run: onCreate, pending: creating } = useAsyncAction(async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await GiveawaysApi.create({
        name: form.name,
        prefix: form.prefix,
        ticket_price: Math.round(Number(form.ticket_price) * 100),
        max_tickets: Number(form.max_tickets),
      });
      setForm({ name: "", prefix: "", ticket_price: "", max_tickets: "" });
      setShowForm(false);
      showToast("Розыгрыш создан");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка создания розыгрыша");
    }
  });

  const runRowAction = async (
    id: number,
    action: () => Promise<unknown>,
    successMessage: string,
  ) => {
    setPendingId(id);
    try {
      await action();
      showToast(successMessage);
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось выполнить действие", "error");
    } finally {
      setPendingId(null);
    }
  };

  const onLock = async (g: Giveaway) => {
    const confirmed = await confirm(
      `Заблокировать розыгрыш «${g.name}»? Продажи и выдача номерков остановятся.`,
    );
    if (!confirmed) return;
    void runRowAction(g.id, () => GiveawaysApi.lock(g.id), "Розыгрыш заблокирован");
  };

  const onCloseRegistration = async (g: Giveaway) => {
    const confirmed = await confirm(
      `Закрыть регистрацию для «${g.name}»? Действие нельзя отменить из панели.`,
    );
    if (!confirmed) return;
    void runRowAction(g.id, () => GiveawaysApi.closeRegistration(g.id), "Регистрация закрыта");
  };

  const columnCount = canEdit || canLock ? 9 : 8;

  return (
    <div>
      <h1>Розыгрыши</h1>
      {canEdit && (
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Отмена" : "Новый розыгрыш"}
        </button>
      )}
      {showForm && (
        <form onSubmit={onCreate} className="inline-form">
          <input
            placeholder="Название"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
          />
          <input
            placeholder="Префикс (напр. AUG)"
            value={form.prefix}
            onChange={(e) => setForm({ ...form, prefix: e.target.value })}
            required
          />
          <input
            placeholder="Цена номерка, ₽"
            type="number"
            step="0.01"
            value={form.ticket_price}
            onChange={(e) => setForm({ ...form, ticket_price: e.target.value })}
            required
          />
          <input
            placeholder="Тираж"
            type="number"
            value={form.max_tickets}
            onChange={(e) => setForm({ ...form, max_tickets: e.target.value })}
            required
          />
          <button type="submit" disabled={creating}>
            {creating ? "Создаём…" : "Создать"}
          </button>
        </form>
      )}
      {error && <div className="error">{error}</div>}
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Название</th>
              <th>Префикс</th>
              <th>Цена</th>
              <th>Тираж</th>
              <th>Выдано</th>
              <th>Резерв</th>
              <th>Регистрация</th>
              <th>Блок</th>
              {(canEdit || canLock) && <th>Действия</th>}
            </tr>
          </thead>
          <tbody>
            {giveaways.length === 0 && <EmptyStateRow colSpan={columnCount} />}
            {giveaways.map((g) => (
              <tr key={g.id}>
                <td>{g.name}</td>
                <td>{g.prefix}</td>
                <td>{formatMoney(g.ticket_price)}</td>
                <td>{g.max_tickets}</td>
                <td>{g.tickets_issued}</td>
                <td>{g.tickets_reserved}</td>
                <td>
                  <Badge tone={g.is_registration_open ? "success" : "muted"}>
                    {g.is_registration_open ? "Открыта" : "Закрыта"}
                  </Badge>
                </td>
                <td>
                  <Badge tone={g.is_locked ? "danger" : "muted"}>
                    {g.is_locked ? "Да" : "Нет"}
                  </Badge>
                </td>
                {(canEdit || canLock) && (
                  <td className="actions">
                    {canEdit && !g.opened_at && (
                      <button
                        disabled={pendingId === g.id}
                        onClick={() =>
                          void runRowAction(
                            g.id,
                            () => GiveawaysApi.open(g.id),
                            "Регистрация открыта",
                          )
                        }
                      >
                        Открыть регистрацию
                      </button>
                    )}
                    {canLock &&
                      (g.is_locked ? (
                        <button
                          disabled={pendingId === g.id}
                          onClick={() =>
                            void runRowAction(
                              g.id,
                              () => GiveawaysApi.unlock(g.id),
                              "Розыгрыш разблокирован",
                            )
                          }
                        >
                          Разблокировать
                        </button>
                      ) : (
                        <button
                          className="button-danger"
                          disabled={pendingId === g.id}
                          onClick={() => void onLock(g)}
                        >
                          Заблокировать
                        </button>
                      ))}
                    {canEdit && g.is_registration_open && (
                      <button
                        className="button-danger"
                        disabled={pendingId === g.id}
                        onClick={() => void onCloseRegistration(g)}
                      >
                        Закрыть регистрацию
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
