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
 * Восстановление доступно тем же правом, что и архивация (Super Admin). Отдельным правом
 * `giveaway_purge` (тоже Super Admin) — удаление чеков с диска и полное удаление коллекции
 * (DECISIONS_LOG.md №89). */
export function ArchivePage() {
  const { hasPermission } = useAuth();
  const canArchive = hasPermission("giveaway_archive");
  const canPurge = hasPermission("giveaway_purge");
  const showActions = canArchive || canPurge;
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

  // Перед необратимым действием показываем, что именно будет удалено.
  const runPurge = async (g: Giveaway, kind: "receipts" | "all"): Promise<void> => {
    setPendingId(g.id);
    try {
      const s = await GiveawaysApi.purgeSummary(g.id);
      const message =
        kind === "receipts"
          ? `Удалить с диска все чеки коллекции «${g.name}» (${s.receipts} шт.)? ` +
            "Файлы будут удалены безвозвратно; в «Продажах» у счетов останется пометка «чек удалён». " +
            "Покупки, номера и коллекция останутся."
          : `Удалить коллекцию «${g.name}» (${g.prefix}) полностью и безвозвратно? ` +
            `Будут удалены: онлайн-счета — ${s.payments}, ручные регистрации — ` +
            `${s.manual_registrations}, номера — ${s.tickets}, чеки — ${s.receipts}, ` +
            `постеры — ${s.posters}. Участники и журнал аудита останутся.`;
      if (kind === "receipts" && s.receipts === 0) {
        showToast("У этой коллекции нет чеков для удаления");
        return;
      }
      if (!(await confirm(message))) return;
      const result =
        kind === "receipts"
          ? await GiveawaysApi.deleteReceipts(g.id)
          : await GiveawaysApi.purge(g.id);
      const done =
        kind === "receipts"
          ? `Удалено чеков: ${result.summary.receipts}`
          : `Коллекция «${g.name}» удалена`;
      showToast(
        result.file_errors > 0
          ? `${done}. Не удалось удалить файлов: ${result.file_errors} (см. лог backend)`
          : done,
        result.file_errors > 0 ? "error" : undefined,
      );
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
              {showActions && <th>Действия</th>}
            </tr>
          </thead>
          <tbody>
            {giveaways.length === 0 && <EmptyStateRow colSpan={showActions ? 6 : 5} />}
            {giveaways.map((g) => (
              <tr key={g.id}>
                <td>
                  <Link to={`/giveaways/${g.id}`}>{g.name}</Link>
                </td>
                <td>{g.prefix}</td>
                <td>{formatMoney(g.ticket_price)}</td>
                <td>{g.tickets_issued}</td>
                <td>{g.archived_at ? formatDateTime(g.archived_at) : "—"}</td>
                {showActions && (
                  <td className="actions">
                    {canArchive && (
                      <button disabled={pendingId === g.id} onClick={() => void onUnarchive(g)}>
                        Восстановить
                      </button>
                    )}
                    {canPurge && (
                      <>
                        <button
                          className="button-secondary"
                          disabled={pendingId === g.id}
                          onClick={() => void runPurge(g, "receipts")}
                        >
                          Удалить чеки
                        </button>
                        <button
                          className="button-danger"
                          disabled={pendingId === g.id}
                          onClick={() => void runPurge(g, "all")}
                        >
                          Удалить коллекцию
                        </button>
                      </>
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
