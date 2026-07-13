import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GiveawaysApi, ManualRegistrationsApi } from "../api/resources";
import type { Giveaway, ManualRegistration } from "../api/types";

export function ManualRegistrationsPage() {
  const [registrations, setRegistrations] = useState<ManualRegistration[]>([]);
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [form, setForm] = useState({
    giveaway_id: "",
    participant_phone: "",
    quantity: "1",
    comment: "",
  });
  const [error, setError] = useState<string | null>(null);

  const load = () => void ManualRegistrationsApi.list().then(setRegistrations);
  useEffect(load, []);
  useEffect(() => {
    void GiveawaysApi.list().then((all) =>
      setGiveaways(all.filter((g) => g.is_registration_open && !g.is_locked)),
    );
  }, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await ManualRegistrationsApi.create({
        giveaway_id: Number(form.giveaway_id),
        participant_phone: form.participant_phone,
        quantity: Number(form.quantity),
        comment: form.comment || undefined,
      });
      setForm({ giveaway_id: "", participant_phone: "", quantity: "1", comment: "" });
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
          onChange={(e) => setForm({ ...form, participant_phone: e.target.value })}
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
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Розыгрыш</th>
            <th>Участник</th>
            <th>Кол-во</th>
            <th>Статус</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {registrations.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.giveaway_id}</td>
              <td>{r.participant_id}</td>
              <td>{r.quantity}</td>
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
