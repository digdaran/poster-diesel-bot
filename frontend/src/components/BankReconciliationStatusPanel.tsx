import { useEffect, useState } from "react";
import { BankReconciliationApi } from "../api/resources";
import { Badge } from "./Badge";
import { EmptyStateRow, LoadingState } from "./EmptyState";
import { formatDateTime } from "../utils/format";
import type { BankReconciliationRun, BankReconciliationStatus } from "../api/types";

// Собственный интервал обновления (не паттерн "только по монтированию/фильтрам",
// принятый в остальной панели) — это честный live-статус фонового процесса,
// а не список с фильтрами.
const REFRESH_INTERVAL_MS = 30_000;

const RUN_STATUS_LABEL: Record<BankReconciliationRun["status"], string> = {
  SUCCESS: "OK",
  FETCH_FAILED: "Ошибка запроса к банку",
};

export function BankReconciliationStatusPanel() {
  const [status, setStatus] = useState<BankReconciliationStatus | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void BankReconciliationApi.getStatus()
        .then((result) => {
          if (cancelled) return;
          setStatus(result);
          setLoadError(false);
        })
        .catch(() => {
          if (!cancelled) setLoadError(true);
        });
    };
    load();
    const interval = setInterval(load, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loadError) {
    return (
      <div className="card reconciliation-status">
        <Badge tone="danger">Не удалось загрузить статус сверки выписок</Badge>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="card reconciliation-status">
        <LoadingState message="Загрузка статуса сверки выписок…" />
      </div>
    );
  }

  const lastRun = status.runs[0] ?? null;
  const health: { tone: "success" | "danger" | "muted"; label: string } = status.is_stale
    ? { tone: "muted", label: "Фоновая сверка не запускалась — проверьте фоновый процесс" }
    : lastRun?.status === "FETCH_FAILED"
      ? { tone: "danger", label: "Последний запрос к банку не удался" }
      : status.failed_runs_24h > 0
        ? { tone: "danger", label: `Ошибки за 24ч: ${status.failed_runs_24h}` }
        : { tone: "success", label: "Работает" };

  return (
    <details className="reconciliation-status" open>
      <summary>
        Сверка банковской выписки <Badge tone={health.tone}>{health.label}</Badge>
      </summary>

      <div className="cards">
        <div className="card">
          <div className="card-value">{lastRun ? formatDateTime(lastRun.started_at) : "—"}</div>
          <div className="card-label">Последний тик</div>
        </div>
        <div className="card">
          <div className="card-value">{lastRun?.candidates_checked ?? "—"}</div>
          <div className="card-label">Счетов проверено</div>
        </div>
        <div className="card">
          <div className="card-value">{lastRun?.entries_fetched ?? "—"}</div>
          <div className="card-label">Операций в выписке</div>
        </div>
        <div className="card">
          <div className="card-value">{lastRun?.matched_count ?? "—"}</div>
          <div className="card-label">Совпадений</div>
        </div>
        <div className="card">
          <div className="card-value">{lastRun?.mismatch_count ?? "—"}</div>
          <div className="card-label">Расхождений суммы</div>
        </div>
        <div className="card">
          <div className="card-value">{lastRun?.ttl_expired_count ?? "—"}</div>
          <div className="card-label">Просрочено по TTL</div>
        </div>
        <div className="card">
          <div className="card-value">
            {status.total_runs_24h - status.failed_runs_24h}/{status.total_runs_24h}
          </div>
          <div className="card-label">Успешных тиков за 24ч</div>
        </div>
        <div className="card">
          <div className="card-value">
            {status.last_success_at ? formatDateTime(status.last_success_at) : "—"}
          </div>
          <div className="card-label">Последний успешный запрос к банку</div>
        </div>
      </div>

      {lastRun && lastRun.status === "FETCH_FAILED" && lastRun.error_message && (
        <p className="reconciliation-error-message">{lastRun.error_message}</p>
      )}
      {lastRun && lastRun.finalize_error_count > 0 && (
        <p className="reconciliation-error-message">
          В последнем тике не удалось обработать {lastRun.finalize_error_count}{" "}
          платеж(ей) — подробности в логах backend.
        </p>
      )}

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Статус</th>
              <th>Счетов</th>
              <th>Операций</th>
              <th>Совпадений</th>
              <th>Расхождений</th>
              <th>TTL</th>
              <th>Ошибок</th>
            </tr>
          </thead>
          <tbody>
            {status.runs.length === 0 && <EmptyStateRow colSpan={8} />}
            {status.runs.map((run) => (
              <tr
                key={run.id}
                className={run.status === "FETCH_FAILED" ? "row-amount-mismatch" : undefined}
              >
                <td>{formatDateTime(run.started_at)}</td>
                <td>
                  <Badge tone={run.status === "FETCH_FAILED" ? "danger" : "success"}>
                    {RUN_STATUS_LABEL[run.status]}
                  </Badge>
                </td>
                <td>{run.candidates_checked}</td>
                <td>{run.entries_fetched ?? "—"}</td>
                <td>{run.matched_count}</td>
                <td>{run.mismatch_count}</td>
                <td>{run.ttl_expired_count}</td>
                <td>{run.finalize_error_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
