import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { GiveawaysApi } from "../api/resources";
import type { Giveaway } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export function GiveawaysPage() {
  const { hasPermission } = useAuth();
  const canEdit = hasPermission("giveaway_edit");
  const canLock = hasPermission("giveaway_lock");
  const [giveaways, setGiveaways] = useState<Giveaway[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", prefix: "", ticket_price: "", max_tickets: "" });
  const [error, setError] = useState<string | null>(null);

  const load = () => void GiveawaysApi.list().then(setGiveaways);
  useEffect(load, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await GiveawaysApi.create({
        name: form.name,
        prefix: form.prefix,
        ticket_price: Math.round(Number(form.ticket_price) * 100),
        max_tickets: Number(form.max_tickets),
      });
      setForm({ name: "", prefix: "", ticket_price: "", max_tickets: "" });
      setShowForm(false);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка создания розыгрыша");
    }
  };

  return (
    <div>
      <h1>Розыгрыши</h1>
      {canEdit && (
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Отмена" : "Новый розыгрыш"}
        </button>
      )}
      {showForm && (
        <form onSubmit={onCreate} className="inline-form">
          <input
            placeholder="Название"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            required
          />
          <input
            placeholder="Префикс (напр. AUG)"
            value={form.prefix}
            onChange={(e) => setForm({ ...form, prefix: e.target.value })}
            required
          />
          <input
            placeholder="Цена номерка, ₽"
            type="number"
            step="0.01"
            value={form.ticket_price}
            onChange={(e) => setForm({ ...form, ticket_price: e.target.value })}
            required
          />
          <input
            placeholder="Тираж"
            type="number"
            value={form.max_tickets}
            onChange={(e) => setForm({ ...form, max_tickets: e.target.value })}
            required
          />
          <button type="submit">Создать</button>
        </form>
      )}
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Префикс</th>
            <th>Цена</th>
            <th>Тираж</th>
            <th>Выдано</th>
            <th>Резерв</th>
            <th>Регистрация</th>
            <th>Блок</th>
            {(canEdit || canLock) && <th>Действия</th>}
          </tr>
        </thead>
        <tbody>
          {giveaways.map((g) => (
            <tr key={g.id}>
              <td>{g.name}</td>
              <td>{g.prefix}</td>
              <td>{(g.ticket_price / 100).toFixed(2)} ₽</td>
              <td>{g.max_tickets}</td>
              <td>{g.tickets_issued}</td>
              <td>{g.tickets_reserved}</td>
              <td>{g.is_registration_open ? "Открыта" : "Закрыта"}</td>
              <td>{g.is_locked ? "Да" : "Нет"}</td>
              {(canEdit || canLock) && (
                <td className="actions">
                  {canEdit && !g.opened_at && (
                    <button onClick={() => GiveawaysApi.open(g.id).then(load)}>
                      Открыть регистрацию
                    </button>
                  )}
                  {canLock &&
                    (g.is_locked ? (
                      <button onClick={() => GiveawaysApi.unlock(g.id).then(load)}>
                        Разблокировать
                      </button>
                    ) : (
                      <button onClick={() => GiveawaysApi.lock(g.id).then(load)}>
                        Заблокировать
                      </button>
                    ))}
                  {canEdit && g.is_registration_open && (
                    <button onClick={() => GiveawaysApi.closeRegistration(g.id).then(load)}>
                      Закрыть регистрацию
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
