import { useCallback, useEffect, useRef, useState } from "react";
import { ManualRegistrationsApi, SalesApi, TicketsApi } from "../api/resources";
import { Badge } from "../components/Badge";
import { ChannelBadges } from "../components/ChannelBadges";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney, formatRelativeTime } from "../utils/format";

const POLL_INTERVAL_MS = 3000;
// CSS-класс "row-just-confirmed" запускает ДВЕ анимации подряд (см. index.css):
// вспышку (2.5s) и следующее за ней 10-секундное мигание (итого 12.5s) — этот
// таймаут держит класс навешенным ровно на всю их суммарную длительность,
// плюс небольшой запас на рассинхрон таймеров.
const HIGHLIGHT_DURATION_MS = 12800;
// Аналогично "row-just-appeared": вспышка 5s + мигание 10s = 15s.
const APPEAR_HIGHLIGHT_DURATION_MS = 15200;
const FEED_SIZE = 100;
const CONFIRMED_FEED_SIZE = 30;
// Длительность CSS-transition у летящей карточки — держим в одном месте с
// таймаутом её удаления (см. FLY_DURATION_MS + запас ниже).
const FLY_DURATION_MS = 700;
const GHOST_WIDTH = 200;

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
  // Заполняется только для строк, попадающих в правую панель (см.
  // fetchTicketCodes) — в основной ленте не используется.
  ticketCodes?: string[];
}

interface FlyingItem {
  id: string;
  row: MonitorRow;
  from: DOMRect;
  to: DOMRect;
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

// Номера билетов конкретного платежа/регистрации — у Ticket своя связь на
// каждый источник (payment_id для онлайна, manual_registration_id для ручных,
// см. backend/api/tickets.py), поэтому запрос зависит от row.kind.
async function fetchTicketCodes(row: MonitorRow): Promise<string[]> {
  try {
    const result =
      row.kind === "online"
        ? await TicketsApi.list({ payment_id: row.id, page_size: 500 })
        : await TicketsApi.list({ manual_registration_id: row.id, page_size: 500 });
    return result.items.map((t) => t.full_code).sort();
  } catch {
    return [];
  }
}

function FlyingGhost({
  item,
  onDone,
}: {
  item: FlyingItem;
  onDone: (item: FlyingItem) => void;
}) {
  const elRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // Стартовая позиция уже выставлена в inline-style при монтировании
    // (item.from) — на следующий кадр сдвигаем к цели, CSS-transition на
    // left/top анимирует перелёт (FLIP-приём без сторонних библиотек).
    const raf = requestAnimationFrame(() => {
      const el = elRef.current;
      if (!el) return;
      el.style.left = `${item.to.left + 10}px`;
      el.style.top = `${item.to.top + 10}px`;
      el.style.opacity = "0.35";
      el.style.transform = "scale(0.8)";
    });
    const timeoutId = window.setTimeout(() => onDone(item), FLY_DURATION_MS + 80);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timeoutId);
    };
  }, [item, onDone]);

  const startLeft = item.from.right - GHOST_WIDTH;
  const startTop = item.from.top + item.from.height / 2 - 16;

  return (
    <div ref={elRef} className="flying-payment-ghost" style={{ left: startLeft, top: startTop }}>
      <Badge tone={item.row.kind === "online" ? "info" : "muted"}>
        {item.row.kind === "online" ? "Онлайн" : "Ручная"}
      </Badge>
      <span className="flying-payment-ghost-name">{item.row.participantLabel}</span>
      <span className="flying-payment-ghost-amount">{formatMoney(item.row.amount)}</span>
    </div>
  );
}

export function MonitoringPage() {
  const [rows, setRows] = useState<MonitorRow[]>([]);
  const [confirmedFeed, setConfirmedFeed] = useState<MonitorRow[]>([]);
  const [flyingItems, setFlyingItems] = useState<FlyingItem[]>([]);
  const [justConfirmed, setJustConfirmed] = useState<Set<string>>(new Set());
  const [justAppeared, setJustAppeared] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  // Состояние подтверждения по каждой строке на МОМЕНТ предыдущего опроса —
  // не в useState, т.к. не должно вызывать перерисовку само по себе. Заодно
  // это единственный способ отличить "видели уже" от "появилась впервые".
  const prevConfirmedRef = useRef<Map<string, boolean>>(new Map());
  const isFirstLoadRef = useRef(true);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());
  const highlightTimeoutsRef = useRef<Set<number>>(new Set());
  const confirmedListRef = useRef<HTMLDivElement>(null);

  const setRowRef = useCallback((key: string) => {
    return (el: HTMLTableRowElement | null) => {
      if (el) rowRefs.current.set(key, el);
      else rowRefs.current.delete(key);
    };
  }, []);

  const handleFlightDone = useCallback((item: FlyingItem) => {
    setFlyingItems((prev) => prev.filter((f) => f.id !== item.id));
    setConfirmedFeed((feed) => [item.row, ...feed].slice(0, CONFIRMED_FEED_SIZE));
  }, []);

  const fetchAndMerge = useCallback(async () => {
    try {
      const [sales, registrations] = await Promise.all([
        SalesApi.list({ page: 1, page_size: FEED_SIZE }),
        ManualRegistrationsApi.list({ page: 1, page_size: FEED_SIZE }),
      ]);
      const merged = toRows(sales.items, registrations.items);

      // "Появилась впервые" = ключа вообще не было на прошлом опросе.
      // "Подтверждена только что" = на прошлом опросе НЕ была подтверждена
      // (включая случай, когда её вообще не было — платёж/регистрация могли
      // создать и тут же подтвердить в рамках одного 3-секундного цикла
      // опроса, тогда wasConfirmed === undefined, а не false; раньше это был
      // отдельный "else if"-ветвь, из-за чего такая строка никогда не
      // проверялась на подтверждение и не улетала в правую панель).
      // На самом первом опросе не считаем ничего новым/подтверждённым —
      // иначе вся история подсветится и улетит в правую панель разом при
      // открытии страницы.
      const newlyAppeared: string[] = [];
      const newlyConfirmed: string[] = [];
      if (!isFirstLoadRef.current) {
        for (const row of merged) {
          const wasConfirmed = prevConfirmedRef.current.get(row.key);
          if (wasConfirmed === undefined) {
            newlyAppeared.push(row.key);
          }
          if (wasConfirmed !== true && row.isConfirmed) {
            newlyConfirmed.push(row.key);
          }
        }
      }

      // Номера билетов тянем ДО того, как строка попадёт в правую панель —
      // запросы идут параллельно, так что старт анимации задерживается на
      // доли секунды, не более.
      const confirmedEntries = await Promise.all(
        newlyConfirmed.map(async (key) => {
          const row = merged.find((r) => r.key === key);
          if (!row) return null;
          const ticketCodes = await fetchTicketCodes(row);
          return { key, row: { ...row, ticketCodes } };
        }),
      );

      // Координаты снимаем СЕЙЧАС, пока DOM ещё отражает предыдущий рендер
      // (со старым статусом строки) — именно эта позиция и есть "откуда
      // лететь". Сама раскладка (setRows) произойдёт чуть ниже.
      const newGhosts: FlyingItem[] = [];
      const directInserts: MonitorRow[] = [];
      for (const entry of confirmedEntries) {
        if (!entry) continue;
        const { key, row } = entry;
        const fromEl = rowRefs.current.get(key);
        const toEl = confirmedListRef.current;
        if (fromEl && toEl) {
          newGhosts.push({
            id: `${key}-${Date.now()}`,
            row,
            from: fromEl.getBoundingClientRect(),
            to: toEl.getBoundingClientRect(),
          });
        } else {
          // Не удалось получить координаты (строка вне экрана и т.п.) —
          // добавляем сразу без анимации, чтобы платёж не потерялся.
          directInserts.push(row);
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

      if (newlyAppeared.length > 0) {
        setJustAppeared((prev) => {
          const next = new Set(prev);
          newlyAppeared.forEach((k) => next.add(k));
          return next;
        });
        newlyAppeared.forEach((key) => {
          const timeoutId = window.setTimeout(() => {
            setJustAppeared((prev) => {
              const next = new Set(prev);
              next.delete(key);
              return next;
            });
            highlightTimeoutsRef.current.delete(timeoutId);
          }, APPEAR_HIGHLIGHT_DURATION_MS);
          highlightTimeoutsRef.current.add(timeoutId);
        });
      }

      if (directInserts.length > 0) {
        setConfirmedFeed((feed) => [...directInserts, ...feed].slice(0, CONFIRMED_FEED_SIZE));
      }

      if (newGhosts.length > 0) {
        setFlyingItems((prev) => [...prev, ...newGhosts]);
        const keys = new Set(newGhosts.map((g) => g.row.key));
        setJustConfirmed((prev) => {
          const next = new Set(prev);
          keys.forEach((k) => next.add(k));
          return next;
        });
        // Фокус — только на первую (самую свежую) из подтвердившихся за этот
        // тик, чтобы экран не прыгал между несколькими строками разом.
        rowRefs.current.get(newGhosts[0].row.key)?.scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
        keys.forEach((key) => {
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

  const rowClassName = (key: string) => {
    if (justConfirmed.has(key)) return "row-just-confirmed";
    if (justAppeared.has(key)) return "row-just-appeared";
    return undefined;
  };

  return (
    <div className="monitoring-standalone">
      <h1>
        Мониторинг продаж <span className="live-dot" title="Обновляется каждые 3 сек" />
      </h1>
      {error && <div className="error">{error}</div>}
      <div className="monitoring-layout">
        <div className="monitoring-main">
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
                  <tr key={row.key} ref={setRowRef(row.key)} className={rowClassName(row.key)}>
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
        <aside className="monitoring-confirmed-panel">
          <h2>Подтверждённые платежи</h2>
          <div className="monitoring-confirmed-list" ref={confirmedListRef}>
            <table>
              <thead>
                <tr>
                  <th>Тип</th>
                  <th>Участник</th>
                  <th>Подтверждён</th>
                  <th>Номера</th>
                </tr>
              </thead>
              <tbody>
                {confirmedFeed.length === 0 && (
                  <EmptyStateRow colSpan={4} message="Пока нет подтверждений" />
                )}
                {confirmedFeed.map((row) => (
                  <tr key={row.key} className="confirmed-feed-row">
                    <td>
                      <Badge tone={row.kind === "online" ? "info" : "muted"}>
                        {row.kind === "online" ? "Онлайн" : "Ручная"}
                      </Badge>
                    </td>
                    <td>{row.participantLabel}</td>
                    <td>{row.confirmedAt ? formatRelativeTime(row.confirmedAt) : "—"}</td>
                    <td className="confirmed-feed-tickets">
                      {row.ticketCodes && row.ticketCodes.length > 0
                        ? row.ticketCodes.join(", ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </aside>
      </div>
      {flyingItems.map((item) => (
        <FlyingGhost key={item.id} item={item} onDone={handleFlightDone} />
      ))}
    </div>
  );
}
