import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GiveawaysApi, ManualRegistrationsApi, ParticipantsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { usePagination } from "../hooks/usePagination";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { PaginationControls } from "../components/PaginationControls";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatMoney } from "../utils/format";
import type { Giveaway, ManualRegistration } from "../api/types";

const STATUS_TONE: Record<string, "success" | "danger" | "muted"> = {
  CONFIRMED: "success",
  CANCELLED: "danger",
  PENDING: "muted",
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

  const [filterGiveawayId, setFilterGiveawayId] = useState("");
  const [participantQuery, setParticipantQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const debouncedParticipantQuery = useDebouncedValue(participantQuery);

  const load = () =>
    void ManualRegistrationsApi.list({
      page,
      page_size: pageSize,
      giveaway_id: filterGiveawayId ? Number(filterGiveawayId) : undefined,
      participant_query: debouncedParticipantQuery || undefined,
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

  return (
    <div>
      <h1>Ручные регистрации</h1>
      <form onSubmit={onCreate} className="inline-form">
        <select
          value={form.giveaway_id}
          onChange={(e) => setForm({ ...form, giveaway_id: e.target.value })}
          required
        >
          <option value="">Розыгрыш…</option>
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
          <option value="">Все розыгрыши</option>
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
        </select>
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
              <th>Розыгрыш</th>
              <th>Участник</th>
              <th>Кол-во</th>
              <th>Сумма</th>
              <th>Оператор</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {registrations.length === 0 && <EmptyStateRow colSpan={8} />}
            {registrations.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.giveaway_name}</td>
                <td>{r.participant_full_name ?? r.participant_phone}</td>
                <td>{r.quantity}</td>
                <td>{formatMoney(r.revenue)}</td>
                <td>{r.operator_login}</td>
                <td>
                  <Badge tone={STATUS_TONE[r.status] ?? "muted"}>{r.status}</Badge>
                </td>
                <td className="actions">
                  {r.status === "PENDING" && (
                    <>
                      <button
                        disabled={pendingId === r.id}
                        onClick={() =>
                          void runRowAction(
                            r.id,
                            () => ManualRegistrationsApi.confirm(r.id),
                            "Регистрация подтверждена",
                          )
                        }
                      >
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
    </div>
  );
}
