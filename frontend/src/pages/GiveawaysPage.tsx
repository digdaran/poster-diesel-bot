import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { GiveawaysApi } from "../api/resources";
import type { Giveaway } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney } from "../utils/format";

export function GiveawaysPage() {
  const { hasPermission } = useAuth();
  const canEdit = hasPermission("giveaway_edit");
  const canLock = hasPermission("giveaway_lock");
  const canArchive = hasPermission("giveaway_archive");
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", prefix: "", ticket_price: "", max_tickets: "" });
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [registrationOpen, setRegistrationOpen] = useState("");
  const [isLocked, setIsLocked] = useState("");
  const debouncedQuery = useDebouncedValue(query);

  const load = () =>
    void GiveawaysApi.list({
      q: debouncedQuery || undefined,
      is_registration_open: registrationOpen ? registrationOpen === "true" : undefined,
      is_locked: isLocked ? isLocked === "true" : undefined,
      // Архивные коллекции здесь не показываются — см. раздел «Архив».
      is_archived: false,
    }).then(setGiveaways);
  useEffect(load, [debouncedQuery, registrationOpen, isLocked]);

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
      showToast("Коллекция создана");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка создания коллекции");
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
      `Приостановить продажи коллекции «${g.name}»? Новые покупки и ручные регистрации ` +
        "станут недоступны. Уже созданные счета всё равно завершатся, когда деньги поступят. " +
        "Возобновить можно в любой момент кнопкой «Возобновить продажи».",
    );
    if (!confirmed) return;
    void runRowAction(g.id, () => GiveawaysApi.lock(g.id), "Продажи приостановлены");
  };

  const onCloseRegistration = async (g: Giveaway) => {
    const confirmed = await confirm(
      `Закрыть регистрацию для «${g.name}» НАВСЕГДА? Новые покупки станут недоступны, и ` +
        "открыть регистрацию заново через панель будет нельзя — действие необратимо.",
    );
    if (!confirmed) return;
    void runRowAction(
      g.id,
      () => GiveawaysApi.closeRegistration(g.id),
      "Регистрация закрыта навсегда",
    );
  };

  const onArchive = async (g: Giveaway) => {
    const confirmed = await confirm(
      `Архивировать коллекцию «${g.name}»? Она пропадёт из списка «Коллекции» и переедет в ` +
        "«Архив». Ничего не удаляется — билеты, платежи и история остаются доступны; при " +
        "необходимости коллекцию можно вернуть обратно из архива.",
    );
    if (!confirmed) return;
    void runRowAction(g.id, () => GiveawaysApi.archive(g.id), "Коллекция перемещена в архив");
  };

  const columnCount = canEdit || canLock || canArchive ? 9 : 8;

  return (
    <div>
      <h1>Коллекции</h1>
      {canEdit && (
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Отмена" : "Новая коллекция"}
        </button>
      )}
      <Link to="/archive" className="button-secondary">
        Архив
      </Link>
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
            placeholder="Цена экземпляра, ₽"
            type="number"
            step="0.01"
            value={form.ticket_price}
            onChange={(e) => setForm({ ...form, ticket_price: e.target.value })}
            required
          />
          <input
            placeholder="Лимит количества"
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
      <div className="filters">
        <input
          placeholder="Поиск по названию/префиксу"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={registrationOpen} onChange={(e) => setRegistrationOpen(e.target.value)}>
          <option value="">Регистрация: любая</option>
          <option value="true">Открыта</option>
          <option value="false">Закрыта</option>
        </select>
        <select value={isLocked} onChange={(e) => setIsLocked(e.target.value)}>
          <option value="">Продажи: любые</option>
          <option value="true">Приостановлены</option>
          <option value="false">Активны</option>
        </select>
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Название</th>
              <th>Префикс</th>
              <th>Цена</th>
              <th>Лимит</th>
              <th>Выдано</th>
              <th>Резерв</th>
              <th>Регистрация</th>
              <th>Продажи</th>
              {(canEdit || canLock || canArchive) && <th>Действия</th>}
            </tr>
          </thead>
          <tbody>
            {giveaways.length === 0 && <EmptyStateRow colSpan={columnCount} />}
            {giveaways.map((g) => (
              <tr key={g.id}>
                <td>
                  <Link to={`/giveaways/${g.id}`}>{g.name}</Link>
                </td>
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
                  <Badge tone={g.is_locked ? "danger" : "success"}>
                    {g.is_locked ? "Приостановлены" : "Активны"}
                  </Badge>
                </td>
                {(canEdit || canLock || canArchive) && (
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
                              "Продажи возобновлены",
                            )
                          }
                        >
                          Возобновить продажи
                        </button>
                      ) : (
                        <button
                          className="button-danger"
                          disabled={pendingId === g.id}
                          onClick={() => void onLock(g)}
                        >
                          Приостановить продажи
                        </button>
                      ))}
                    {canEdit && g.is_registration_open && (
                      <button
                        className="button-danger"
                        disabled={pendingId === g.id}
                        onClick={() => void onCloseRegistration(g)}
                      >
                        Закрыть регистрацию навсегда
                      </button>
                    )}
                    {canArchive && !g.is_registration_open && (
                      <button disabled={pendingId === g.id} onClick={() => void onArchive(g)}>
                        Архивировать
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
