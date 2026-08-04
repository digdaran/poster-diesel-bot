import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  GiveawaysApi,
  ManualRegistrationsApi,
  ParticipantsApi,
  TicketsApi,
} from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { PaginationControls } from "../components/PaginationControls";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { RefundDialog } from "../components/RefundDialog";
import { Badge } from "../components/Badge";
import { ChannelBadges } from "../components/ChannelBadges";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney, formatDateTime } from "../utils/format";
import { PAGE_SIZES } from "../api/types";
import type { Giveaway, ManualRegistration, Ticket } from "../api/types";

const STATUS_TONE: Record<string, "success" | "danger" | "info" | "muted"> = {
  CONFIRMED: "success",
  CANCELLED: "danger",
  PENDING: "muted",
  REFUNDED: "info",
};

const PAYMENT_METHOD_LABEL: Record<string, string> = {
  CASH: "Наличные",
  CASHLESS: "Безнал (QR)",
};

export function ManualRegistrationsPage() {
  const { hasPermission, user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [registrations, setRegistrations] = useState<ManualRegistration[]>([]);
  const [total, setTotal] = useState(0);
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [allGiveaways, setAllGiveaways] = useState<Giveaway[]>([]);
  const { page, pageSize, setPage, setPageSize } = usePagination();
  const [form, setForm] = useState({
    giveaway_id: "",
    participant_phone: "",
    participant_full_name: "",
    quantity: "1",
    comment: "",
  });
  const [nameLocked, setNameLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [qrModal, setQrModal] = useState<{
    registration: ManualRegistration;
    imageUrl: string;
  } | null>(null);
  const qrObjectUrlRef = useRef<string | null>(null);
  const [ticketsModal, setTicketsModal] = useState<{
    registration: ManualRegistration;
    tickets: Ticket[];
  } | null>(null);
  const [refundTarget, setRefundTarget] = useState<ManualRegistration | null>(null);
  const [refundPending, setRefundPending] = useState(false);

  const [filterGiveawayId, setFilterGiveawayId] = useState("");
  const [participantQuery, setParticipantQuery] = useState("");
  const [operatorQuery, setOperatorQuery] = useState("");
  const [paymentMethodFilter, setPaymentMethodFilter] = useState("");
  const [invoiceNo, setInvoiceNo] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const debouncedParticipantQuery = useDebouncedValue(participantQuery);
  const debouncedOperatorQuery = useDebouncedValue(operatorQuery);
  const debouncedInvoiceNo = useDebouncedValue(invoiceNo);

  // Operator и так видит только свои регистрации (сервер сам это применяет) —
  // фильтр по оператору имеет смысл только для ролей, которые видят всех.
  const canFilterByOperator = user?.role !== "operator";

  const load = () =>
    void ManualRegistrationsApi.list({
      page,
      page_size: pageSize,
      giveaway_id: filterGiveawayId ? Number(filterGiveawayId) : undefined,
      participant_query: debouncedParticipantQuery || undefined,
      operator_query: debouncedOperatorQuery || undefined,
      payment_method: paymentMethodFilter || undefined,
      invoice_no: debouncedInvoiceNo || undefined,
      status_filter: statusFilter || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
    }).then((result) => {
      setRegistrations(result.items);
      setTotal(result.total);
    });
  useEffect(load, [
    page,
    pageSize,
    filterGiveawayId,
    debouncedParticipantQuery,
    debouncedOperatorQuery,
    paymentMethodFilter,
    debouncedInvoiceNo,
    statusFilter,
    createdFrom,
    createdTo,
  ]);

  useEffect(() => {
    void GiveawaysApi.list().then((all) => {
      setAllGiveaways(all);
      setGiveaways(all.filter((g) => g.is_registration_open && !g.is_locked));
    });
  }, []);

  const onPhoneChange = (phone: string) => {
    // Телефон правится после найденного совпадения — сбрасываем автоподстановку,
    // пока не подтвердится совпадение по новому номеру.
    setForm((f) => ({
      ...f,
      participant_phone: phone,
      participant_full_name: nameLocked ? "" : f.participant_full_name,
    }));
    if (nameLocked) setNameLocked(false);
  };

  const onPhoneBlur = async () => {
    if (!form.participant_phone) return;
    const found = await ParticipantsApi.findByPhone(form.participant_phone).catch(() => null);
    if (found) {
      setForm((f) => ({ ...f, participant_full_name: found.full_name ?? "" }));
      setNameLocked(!isSuperAdmin);
    } else {
      setNameLocked(false);
    }
  };

  const downloadReport = async (format: "csv" | "xlsx") => {
    const { blob, filename } = await apiDownload("/api/manual-registrations", {
      export: format,
      giveaway_id: filterGiveawayId ? Number(filterGiveawayId) : undefined,
      participant_query: debouncedParticipantQuery || undefined,
      operator_query: debouncedOperatorQuery || undefined,
      payment_method: paymentMethodFilter || undefined,
      invoice_no: debouncedInvoiceNo || undefined,
      status_filter: statusFilter || undefined,
      created_from: createdFrom || undefined,
      created_to: createdTo || undefined,
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const { run: onCreate, pending: creating } = useAsyncAction(async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await ManualRegistrationsApi.create({
        giveaway_id: Number(form.giveaway_id),
        participant_phone: form.participant_phone,
        participant_full_name: form.participant_full_name,
        quantity: Number(form.quantity),
        comment: form.comment || undefined,
      });
      setForm({
        giveaway_id: "",
        participant_phone: "",
        participant_full_name: "",
        quantity: "1",
        comment: "",
      });
      setNameLocked(false);
      showToast("Регистрация оформлена");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать регистрацию");
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

  const onCancel = async (r: ManualRegistration) => {
    const confirmed = await confirm(
      `Отменить регистрацию №${r.id} (${r.participant_full_name ?? r.participant_phone})?`,
    );
    if (!confirmed) return;
    void runRowAction(r.id, () => ManualRegistrationsApi.cancel(r.id), "Регистрация отменена");
  };

  const onRefund = (reason: string) => {
    if (!refundTarget) return;
    setRefundPending(true);
    ManualRegistrationsApi.refund(refundTarget.id, reason)
      .then(() => {
        showToast("Регистрация аннулирована, номера возвращены в оборот");
        setRefundTarget(null);
        load();
      })
      .catch((err) => {
        showToast(
          err instanceof Error ? err.message : "Не удалось аннулировать регистрацию",
          "error",
        );
      })
      .finally(() => setRefundPending(false));
  };

  const closeQrModal = () => {
    if (qrObjectUrlRef.current) {
      URL.revokeObjectURL(qrObjectUrlRef.current);
      qrObjectUrlRef.current = null;
    }
    setQrModal(null);
  };

  useEffect(() => {
    return () => {
      if (qrObjectUrlRef.current) URL.revokeObjectURL(qrObjectUrlRef.current);
    };
  }, []);

  const onGenerateQr = async (r: ManualRegistration) => {
    setPendingId(r.id);
    try {
      const updated = await ManualRegistrationsApi.generateQr(r.id);
      const { blob } = await apiDownload(ManualRegistrationsApi.qrPngUrl(r.id));
      const imageUrl = URL.createObjectURL(blob);
      qrObjectUrlRef.current = imageUrl;
      setQrModal({ registration: updated, imageUrl });
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось сформировать QR", "error");
    } finally {
      setPendingId(null);
    }
  };

  // page_size у /api/tickets принимает только фиксированный набор значений —
  // берём наименьший, покрывающий quantity регистрации (если она больше
  // максимального page_size, показываем первую страницу номерков).
  const ticketsPageSize = (quantity: number) =>
    PAGE_SIZES.find((size) => size >= quantity) ?? PAGE_SIZES[PAGE_SIZES.length - 1];

  const showAssignedTickets = async (registration: ManualRegistration) => {
    const { items } = await TicketsApi.list({
      manual_registration_id: registration.id,
      page_size: ticketsPageSize(registration.quantity),
    });
    setTicketsModal({
      registration,
      tickets: [...items].sort((a, b) => a.number - b.number),
    });
  };

  const onConfirm = async (r: ManualRegistration) => {
    setPendingId(r.id);
    try {
      const updated = await ManualRegistrationsApi.confirm(r.id);
      showToast("Регистрация подтверждена");
      load();
      await showAssignedTickets(updated);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось подтвердить", "error");
    } finally {
      setPendingId(null);
    }
  };

  return (
    <div>
      <h1>Ручные регистрации</h1>
      <form onSubmit={onCreate} className="inline-form">
        <select
          value={form.giveaway_id}
          onChange={(e) => setForm({ ...form, giveaway_id: e.target.value })}
          required
        >
          <option value="">Коллекция…</option>
          {giveaways.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({g.max_tickets - g.tickets_issued - g.tickets_reserved} своб.)
            </option>
          ))}
        </select>
        <input
          placeholder="Телефон покупателя"
          value={form.participant_phone}
          onChange={(e) => onPhoneChange(e.target.value)}
          onBlur={() => void onPhoneBlur()}
          required
        />
        <input
          placeholder="Имя покупателя"
          value={form.participant_full_name}
          onChange={(e) => setForm({ ...form, participant_full_name: e.target.value })}
          disabled={nameLocked}
          title={
            nameLocked
              ? "Участник уже зарегистрирован — изменить имя может только Super Admin"
              : undefined
          }
          required
        />
        <input
          type="number"
          min={1}
          value={form.quantity}
          onChange={(e) => setForm({ ...form, quantity: e.target.value })}
          required
        />
        <input
          placeholder="Комментарий"
          value={form.comment}
          onChange={(e) => setForm({ ...form, comment: e.target.value })}
        />
        <button type="submit" disabled={creating}>
          {creating ? "Оформляем…" : "Оформить"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}

      <div className="filters">
        <input
          placeholder="Участник (телефон/имя)"
          value={participantQuery}
          onChange={(e) => {
            setParticipantQuery(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={filterGiveawayId}
          onChange={(e) => {
            setFilterGiveawayId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все коллекции</option>
          {allGiveaways.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все статусы</option>
          <option value="PENDING">PENDING</option>
          <option value="CONFIRMED">CONFIRMED</option>
          <option value="CANCELLED">CANCELLED</option>
          <option value="REFUNDED">REFUNDED</option>
        </select>
        {canFilterByOperator && (
          <input
            placeholder="Оператор"
            value={operatorQuery}
            onChange={(e) => {
              setOperatorQuery(e.target.value);
              setPage(1);
            }}
          />
        )}
        <select
          value={paymentMethodFilter}
          onChange={(e) => {
            setPaymentMethodFilter(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Любая оплата</option>
          <option value="CASH">Наличные</option>
          <option value="CASHLESS">Безнал (QR)</option>
        </select>
        <input
          placeholder="Счёт №"
          value={invoiceNo}
          onChange={(e) => {
            setInvoiceNo(e.target.value);
            setPage(1);
          }}
        />
        <label>
          Создан с{" "}
          <input
            type="date"
            value={createdFrom}
            onChange={(e) => {
              setCreatedFrom(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label>
          по{" "}
          <input
            type="date"
            value={createdTo}
            onChange={(e) => {
              setCreatedTo(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>

      {hasPermission("sales_export") && (
        <div>
          <button onClick={() => downloadReport("csv")}>Экспорт CSV</button>
          <button onClick={() => downloadReport("xlsx")}>Экспорт XLSX</button>
        </div>
      )}
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Коллекция</th>
              <th>Участник</th>
              <th>Каналы</th>
              <th>Кол-во</th>
              <th>Сумма</th>
              <th>Оплата</th>
              <th>Оператор</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {registrations.length === 0 && <EmptyStateRow colSpan={10} />}
            {registrations.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.giveaway_name}</td>
                <td>{r.participant_full_name ?? r.participant_phone}</td>
                <td>
                  <ChannelBadges channels={r.participant_channels} />
                </td>
                <td>{r.quantity}</td>
                <td>{formatMoney(r.revenue)}</td>
                <td>
                  {PAYMENT_METHOD_LABEL[r.payment_method] ?? r.payment_method}
                  {r.invoice_no && <div className="muted-text">счёт {r.invoice_no}</div>}
                </td>
                <td>{r.operator_login}</td>
                <td>
                  <Badge tone={STATUS_TONE[r.status] ?? "muted"}>{r.status}</Badge>
                  {r.status === "REFUNDED" && r.refunded_at && (
                    <div className="muted-text" title={r.refund_reason ?? undefined}>
                      {formatDateTime(r.refunded_at)}
                      {r.refunded_by_login ? ` · ${r.refunded_by_login}` : ""}
                    </div>
                  )}
                </td>
                <td className="actions">
                  {r.status === "PENDING" && (
                    <>
                      <button disabled={pendingId === r.id} onClick={() => void onGenerateQr(r)}>
                        Сформировать QR
                      </button>
                      {r.payment_method === "CASHLESS" && (
                        <button
                          disabled={pendingId === r.id}
                          onClick={() =>
                            void runRowAction(
                              r.id,
                              () => ManualRegistrationsApi.switchToCash(r.id),
                              "Способ оплаты изменён на наличные",
                            )
                          }
                        >
                          Наличные
                        </button>
                      )}
                      <button disabled={pendingId === r.id} onClick={() => void onConfirm(r)}>
                        Подтвердить
                      </button>
                      <button
                        className="button-danger"
                        disabled={pendingId === r.id}
                        onClick={() => void onCancel(r)}
                      >
                        Отменить
                      </button>
                    </>
                  )}
                  {r.status === "CONFIRMED" && (
                    <>
                      <button
                        disabled={pendingId === r.id}
                        onClick={() => void showAssignedTickets(r)}
                      >
                        Показать номерки
                      </button>
                      {hasPermission("purchase_refund") && (
                        <button className="button-danger" onClick={() => setRefundTarget(r)}>
                          Аннулировать
                        </button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PaginationControls
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />
      {qrModal && (
        <div className="modal-overlay" onClick={closeQrModal}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p className="modal-message">
              Счёт № {qrModal.registration.invoice_no} на{" "}
              {formatMoney(qrModal.registration.revenue)} — покажите QR покупателю для оплаты по
              реквизитам, затем подтвердите регистрацию после поступления денег.
            </p>
            <img src={qrModal.imageUrl} alt="QR для оплаты по реквизитам" className="qr-preview" />
            <div className="modal-actions">
              <button className="button-secondary" onClick={closeQrModal}>
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}
      {ticketsModal && (
        <div className="modal-overlay" onClick={() => setTicketsModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p className="modal-message">
              Регистрация №{ticketsModal.registration.id} (
              {ticketsModal.registration.participant_full_name ??
                ticketsModal.registration.participant_phone}
              ) — присвоенные номерки:
            </p>
            <ul className="ticket-codes-list">
              {ticketsModal.tickets.map((t) => (
                <li key={t.id}>{t.full_code}</li>
              ))}
            </ul>
            <div className="modal-actions">
              <button className="button-secondary" onClick={() => setTicketsModal(null)}>
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}
      {refundTarget && (
        <RefundDialog
          message={
            `Аннулировать подтверждённую регистрацию №${refundTarget.id} ` +
            `(${refundTarget.participant_full_name ?? refundTarget.participant_phone}, ` +
            `${refundTarget.quantity} шт. на ${formatMoney(refundTarget.revenue)})? ` +
            "Номера вернутся в оборот, деньги нужно будет вернуть вручную."
          }
          pending={refundPending}
          onConfirm={onRefund}
          onClose={() => setRefundTarget(null)}
        />
      )}
    </div>
  );
}
