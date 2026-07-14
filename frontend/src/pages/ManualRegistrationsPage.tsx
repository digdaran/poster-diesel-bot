import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GiveawaysApi, ManualRegistrationsApi, ParticipantsApi } from "../api/resources";
import { apiDownload } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Giveaway, ManualRegistration } from "../api/types";

export function ManualRegistrationsPage() {
  const { hasPermission, user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const [registrations, setRegistrations] = useState<ManualRegistration[]>([]);
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [form, setForm] = useState({
    giveaway_id: "",
    participant_phone: "",
    participant_full_name: "",
    quantity: "1",
    comment: "",
  });
  const [nameLocked, setNameLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => void ManualRegistrationsApi.list().then(setRegistrations);
  useEffect(load, []);
  useEffect(() => {
    void GiveawaysApi.list().then((all) =>
      setGiveaways(all.filter((g) => g.is_registration_open && !g.is_locked)),
    );
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
    const { blob, filename } = await apiDownload("/api/manual-registrations", { export: format });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onCreate = async (e: FormEvent) => {
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
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать регистрацию");
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
        <button type="submit">Оформить</button>
      </form>
      {error && <div className="error">{error}</div>}
      {hasPermission("sales_export") && (
        <div>
          <button onClick={() => downloadReport("csv")}>Экспорт CSV</button>
          <button onClick={() => downloadReport("xlsx")}>Экспорт XLSX</button>
        </div>
      )}
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
          {registrations.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.giveaway_name}</td>
              <td>{r.participant_full_name ?? r.participant_phone}</td>
              <td>{r.quantity}</td>
              <td>{(r.revenue / 100).toFixed(2)} ₽</td>
              <td>{r.operator_login}</td>
              <td>{r.status}</td>
              <td className="actions">
                {r.status === "PENDING" && (
                  <>
                    <button onClick={() => ManualRegistrationsApi.confirm(r.id).then(load)}>
                      Подтвердить
                    </button>
                    <button onClick={() => ManualRegistrationsApi.cancel(r.id).then(load)}>
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
  );
}
