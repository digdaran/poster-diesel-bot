import { useCallback, useEffect, useRef, useState } from "react";
import { ManualRegistrationsApi, SalesApi } from "../api/resources";
import { Badge } from "../components/Badge";
import { ChannelBadges } from "../components/ChannelBadges";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney, formatRelativeTime } from "../utils/format";

const POLL_INTERVAL_MS = 3000;
// Держим подсветку на экране чуть дольше, чем длится CSS-анимация (2.5s) —
// запас на случай рассинхрона таймеров, саму анимацию это не продлевает.
const HIGHLIGHT_DURATION_MS = 3000;
const FEED_SIZE = 100;

type MonitorKind = "online" | "manual";

interface MonitorRow {
  key: string;
  kind: MonitorKind;
  id: number;
  giveawayName: string;
  participantLabel: string;
  channels: string[];
  paymentLabel: string;
  amount: number;
  quantity: number;
  status: string;
  isConfirmed: boolean;
  createdAt: string;
  confirmedAt: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Ожидание",
  SUCCEEDED: "Оплачено",
  CONFIRMED: "Подтверждено",
  FAILED: "Ошибка",
  CANCELLED: "Отменено",
};

const STATUS_TONE: Record<string, "success" | "danger" | "muted"> = {
  PENDING: "muted",
  SUCCEEDED: "success",
  CONFIRMED: "success",
  FAILED: "danger",
  CANCELLED: "danger",
};

function toRows(
  payments: Awaited<ReturnType<typeof SalesApi.list>>["items"],
  registrations: Awaited<ReturnType<typeof ManualRegistrationsApi.list>>["items"],
): MonitorRow[] {
  const online: MonitorRow[] = payments.map((p) => ({
    key: `online-${p.id}`,
    kind: "online" as const,
    id: p.id,
    giveawayName: p.giveaway_name,
    participantLabel: p.participant_full_name ?? p.participant_phone,
    channels: p.channel ? [p.channel] : [],
    paymentLabel: p.provider,
    amount: p.amount,
    quantity: p.quantity,
    status: p.status,
    isConfirmed: p.status === "SUCCEEDED",
    createdAt: p.created_at,
    confirmedAt: p.confirmed_at,
  }));
  const manual: MonitorRow[] = registrations.map((r) => ({
    key: `manual-${r.id}`,
    kind: "manual" as const,
    id: r.id,
    giveawayName: r.giveaway_name,
    participantLabel: r.participant_full_name ?? r.participant_phone,
    channels: r.participant_channels,
    paymentLabel: r.payment_method === "CASH" ? "Наличные" : "Безнал (QR)",
    amount: r.revenue,
    quantity: r.quantity,
    status: r.status,
    isConfirmed: r.status === "CONFIRMED",
    createdAt: r.created_at,
    confirmedAt: r.confirmed_at,
  }));
  return [...online, ...manual].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function MonitoringPage() {
  const [rows, setRows] = useState<MonitorRow[]>([]);
  const [justConfirmed, setJustConfirmed] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  // Состояние подтверждения по каждой строке на МОМЕНТ предыдущего опроса —
  // не в useState, т.к. не должно вызывать перерисовку само по себе.
  const prevConfirmedRef = useRef<Map<string, boolean>>(new Map());
  const isFirstLoadRef = useRef(true);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  const highlightTimeoutsRef = useRef<Set<number>>(new Set());

  const setRowRef = useCallback((key: string) => {
    return (el: HTMLTableRowElement | null) => {
      if (el) rowRefs.current.set(key, el);
      else rowRefs.current.delete(key);
    };
  }, []);

  const fetchAndMerge = useCallback(async () => {
    try {
      const [sales, registrations] = await Promise.all([
        SalesApi.list({ page: 1, page_size: FEED_SIZE }),
        ManualRegistrationsApi.list({ page: 1, page_size: FEED_SIZE }),
      ]);
      const merged = toRows(sales.items, registrations.items);

      // Новое подтверждение = строка, которая на прошлом опросе ещё не была
      // подтверждена, а сейчас — подтверждена. На самом первом опросе не
      // считаем ничего "новым" (иначе вся уже оплаченная история подсветится
      // разом при открытии страницы).
      const newlyConfirmed: string[] = [];
      if (!isFirstLoadRef.current) {
        for (const row of merged) {
          const wasConfirmed = prevConfirmedRef.current.get(row.key);
          if (wasConfirmed === false && row.isConfirmed) {
            newlyConfirmed.push(row.key);
          }
        }
      }

      // Перестраиваем карту статусов только из текущей выдачи — строка,
      // ушедшая с топ-100 (вытеснена более новыми), больше никогда не
      // вернётся (сортировка по убыванию), так что копить её статус вечно
      // незачем — это просто утечка памяти на долго открытой вкладке.
      const nextConfirmed = new Map<string, boolean>();
      for (const row of merged) nextConfirmed.set(row.key, row.isConfirmed);
      prevConfirmedRef.current = nextConfirmed;
      isFirstLoadRef.current = false;

      setRows(merged);
      setError(null);

      if (newlyConfirmed.length > 0) {
        setJustConfirmed((prev) => {
          const next = new Set(prev);
          newlyConfirmed.forEach((k) => next.add(k));
          return next;
        });
        // Фокус — только на первую (самую свежую) из подтвердившихся за этот
        // тик, чтобы экран не прыгал между несколькими строками разом.
        rowRefs.current.get(newlyConfirmed[0])?.scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
        newlyConfirmed.forEach((key) => {
          const timeoutId = window.setTimeout(() => {
            setJustConfirmed((prev) => {
              const next = new Set(prev);
              next.delete(key);
              return next;
            });
            highlightTimeoutsRef.current.delete(timeoutId);
          }, HIGHLIGHT_DURATION_MS);
          highlightTimeoutsRef.current.add(timeoutId);
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить данные мониторинга");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    const tick = async () => {
      await fetchAndMerge();
      if (!cancelled) {
        timeoutId = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
      }
    };
    void tick();

    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      highlightTimeoutsRef.current.forEach((id) => window.clearTimeout(id));
      highlightTimeoutsRef.current.clear();
    };
  }, [fetchAndMerge]);

  return (
    <div>
      <h1>
        Мониторинг продаж <span className="live-dot" title="Обновляется каждые 3 сек" />
      </h1>
      <p className="muted-text">
        Живая лента онлайн-платежей и ручных регистраций (последние {FEED_SIZE}). При подтверждении
        оплаты строка подсвечивается и попадает в фокус.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Тип</th>
              <th>Коллекция</th>
              <th>Участник</th>
              <th>Канал</th>
              <th>Оплата</th>
              <th>Сумма</th>
              <th>Кол-во</th>
              <th>Статус</th>
              <th>Создан</th>
              <th>Подтверждён</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <EmptyStateRow colSpan={10} />}
            {rows.map((row) => (
              <tr
                key={row.key}
                ref={setRowRef(row.key)}
                className={justConfirmed.has(row.key) ? "row-just-confirmed" : undefined}
              >
                <td>
                  <Badge tone={row.kind === "online" ? "info" : "muted"}>
                    {row.kind === "online" ? "Онлайн" : "Ручная"}
                  </Badge>
                </td>
                <td>{row.giveawayName}</td>
                <td>{row.participantLabel}</td>
                <td>
                  <ChannelBadges channels={row.channels} />
                </td>
                <td>{row.paymentLabel}</td>
                <td>{formatMoney(row.amount)}</td>
                <td>{row.quantity}</td>
                <td>
                  <Badge tone={STATUS_TONE[row.status] ?? "muted"}>
                    {STATUS_LABEL[row.status] ?? row.status}
                  </Badge>
                </td>
                <td>{formatRelativeTime(row.createdAt)}</td>
                <td>{row.confirmedAt ? formatRelativeTime(row.confirmedAt) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
