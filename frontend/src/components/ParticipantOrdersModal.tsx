import { useEffect, useState } from "react";
import { ManualRegistrationsApi, SalesApi, TicketsApi } from "../api/resources";
import { useAuth } from "../auth/AuthContext";
import { formatMoney, formatDateTime } from "../utils/format";
import { Badge } from "./Badge";
import { ChannelBadges } from "./ChannelBadges";
import { EmptyStateRow, LoadingState } from "./EmptyState";
import type { ManualRegistration, Participant, Payment, Ticket } from "../api/types";

const PAYMENT_STATUS_TONE: Record<string, "success" | "danger" | "muted"> = {
  SUCCEEDED: "success",
  FAILED: "danger",
  PENDING: "muted",
};

const REGISTRATION_STATUS_TONE: Record<string, "success" | "danger" | "muted"> = {
  CONFIRMED: "success",
  CANCELLED: "danger",
  PENDING: "muted",
};

const PAYMENT_METHOD_LABEL: Record<string, string> = {
  CASH: "Наличные",
  CASHLESS: "Безнал (QR)",
};

const TICKET_SOURCE_LABEL: Record<string, string> = {
  online: "Онлайн-оплата",
  manual: "Ручная регистрация",
};

// Детальная выборка на одного участника — обычно не более нескольких
// десятков заказов, поэтому берём максимальный page_size вместо отдельной
// пагинации внутри модалки.
const DETAIL_PAGE_SIZE = 500;

export function ParticipantOrdersModal({
  participant,
  onClose,
}: {
  participant: Participant;
  onClose: () => void;
}) {
  const { hasPermission } = useAuth();
  const canViewSales = hasPermission("view_sales");
  const canViewManualRegistrations = hasPermission("manual_registration_create");
  const canViewTickets = hasPermission("view_tickets");

  const [loading, setLoading] = useState(true);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [registrations, setRegistrations] = useState<ManualRegistration[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void Promise.all([
      canViewSales
        ? SalesApi.list({ participant_id: participant.id, page_size: DETAIL_PAGE_SIZE })
        : Promise.resolve(null),
      canViewManualRegistrations
        ? ManualRegistrationsApi.list({
            participant_id: participant.id,
            page_size: DETAIL_PAGE_SIZE,
          })
        : Promise.resolve(null),
      canViewTickets
        ? TicketsApi.list({ participant_id: participant.id, page_size: DETAIL_PAGE_SIZE })
        : Promise.resolve(null),
    ]).then(([paymentsPage, registrationsPage, ticketsPage]) => {
      if (cancelled) return;
      setPayments(paymentsPage?.items ?? []);
      setRegistrations(registrationsPage?.items ?? []);
      setTickets(ticketsPage?.items ?? []);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [participant.id, canViewSales, canViewManualRegistrations, canViewTickets]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-details" onClick={(e) => e.stopPropagation()}>
        <p className="modal-message">
          Заказы участника: {participant.full_name ?? participant.phone} ({participant.phone})
        </p>

        {loading ? (
          <LoadingState />
        ) : (
          <>
            {canViewSales && (
              <>
                <h3>Онлайн-платежи</h3>
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Счёт №</th>
                        <th>Коллекция</th>
                        <th>Канал</th>
                        <th>Сумма</th>
                        <th>Кол-во</th>
                        <th>Статус</th>
                        <th>Создан</th>
                        <th>Подтверждён</th>
                      </tr>
                    </thead>
                    <tbody>
                      {payments.length === 0 && <EmptyStateRow colSpan={8} />}
                      {payments.map((p) => (
                        <tr key={p.id}>
                          <td>{p.invoice_no ?? "—"}</td>
                          <td>{p.giveaway_name}</td>
                          <td>
                            <ChannelBadges channel={p.channel} />
                          </td>
                          <td>{formatMoney(p.amount)}</td>
                          <td>{p.quantity}</td>
                          <td>
                            <Badge tone={PAYMENT_STATUS_TONE[p.status] ?? "muted"}>
                              {p.status}
                            </Badge>
                            {p.oversold && (
                              <>
                                {" "}
                                <Badge tone="danger">нет номерков</Badge>
                              </>
                            )}
                          </td>
                          <td>{formatDateTime(p.created_at)}</td>
                          <td>{p.confirmed_at ? formatDateTime(p.confirmed_at) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {canViewManualRegistrations && (
              <>
                <h3>Ручные регистрации</h3>
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Коллекция</th>
                        <th>Кол-во</th>
                        <th>Сумма</th>
                        <th>Оплата</th>
                        <th>Статус</th>
                        <th>Оператор</th>
                        <th>Создан</th>
                      </tr>
                    </thead>
                    <tbody>
                      {registrations.length === 0 && <EmptyStateRow colSpan={7} />}
                      {registrations.map((r) => (
                        <tr key={r.id}>
                          <td>{r.giveaway_name}</td>
                          <td>{r.quantity}</td>
                          <td>{formatMoney(r.revenue)}</td>
                          <td>
                            {PAYMENT_METHOD_LABEL[r.payment_method] ?? r.payment_method}
                            {r.invoice_no && <div className="muted-text">счёт {r.invoice_no}</div>}
                          </td>
                          <td>
                            <Badge tone={REGISTRATION_STATUS_TONE[r.status] ?? "muted"}>
                              {r.status}
                            </Badge>
                          </td>
                          <td>{r.operator_login}</td>
                          <td>{formatDateTime(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {canViewTickets && (
              <>
                <h3>Выданные номерки</h3>
                <div className="table-wrapper">
                  <table>
                    <thead>
                      <tr>
                        <th>Коллекция</th>
                        <th>Номер</th>
                        <th>Источник</th>
                        <th>Канал</th>
                        <th>Выдан</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tickets.length === 0 && <EmptyStateRow colSpan={5} />}
                      {tickets.map((t) => (
                        <tr key={t.id}>
                          <td>{t.giveaway_name}</td>
                          <td>{t.full_code}</td>
                          <td>{TICKET_SOURCE_LABEL[t.source] ?? t.source}</td>
                          <td>
                            <ChannelBadges channel={t.channel} />
                          </td>
                          <td>{formatDateTime(t.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}

        <div className="modal-actions">
          <button className="button-secondary" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
