import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { GiveawaysApi } from "../api/resources";
import type { Giveaway } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime, formatMoney } from "../utils/format";

/** Заархивированные коллекции (см. GiveawaysPage.tsx) — данные (билеты, платежи,
 * история) не удаляются, коллекция просто скрыта из основного раздела «Коллекции».
 * Восстановление доступно тем же правом, что и архивация (Super Admin). */
export function ArchivePage() {
  const { hasPermission } = useAuth();
  const canArchive = hasPermission("giveaway_archive");
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query);

  const load = () =>
    void GiveawaysApi.list({ q: debouncedQuery || undefined, is_archived: true }).then(
      setGiveaways,
    );
  useEffect(load, [debouncedQuery]);

  const onUnarchive = async (g: Giveaway) => {
    const confirmed = await confirm(`Вернуть коллекцию «${g.name}» из архива в «Коллекции»?`);
    if (!confirmed) return;
    setPendingId(g.id);
    try {
      await GiveawaysApi.unarchive(g.id);
      showToast("Коллекция возвращена из архива");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось выполнить действие", "error");
    } finally {
      setPendingId(null);
    }
  };

  return (
    <div>
      <h1>Архив</h1>
      <p>
        Коллекции, регистрация на которые закрыта навсегда и которые убрали из основного списка.
        Билеты, платежи и история покупок по ним никуда не делись — их по-прежнему видно в
        «Продажи»/«Номера»/«Отчёты».
      </p>
      <div className="filters">
        <input
          placeholder="Поиск по названию/префиксу"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Название</th>
              <th>Префикс</th>
              <th>Цена</th>
              <th>Выдано</th>
              <th>Заархивирована</th>
              {canArchive && <th>Действия</th>}
            </tr>
          </thead>
          <tbody>
            {giveaways.length === 0 && <EmptyStateRow colSpan={canArchive ? 6 : 5} />}
            {giveaways.map((g) => (
              <tr key={g.id}>
                <td>
                  <Link to={`/giveaways/${g.id}`}>{g.name}</Link>
                </td>
                <td>{g.prefix}</td>
                <td>{formatMoney(g.ticket_price)}</td>
                <td>{g.tickets_issued}</td>
                <td>{g.archived_at ? formatDateTime(g.archived_at) : "—"}</td>
                {canArchive && (
                  <td className="actions">
                    <button disabled={pendingId === g.id} onClick={() => void onUnarchive(g)}>
                      Восстановить
                    </button>
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
