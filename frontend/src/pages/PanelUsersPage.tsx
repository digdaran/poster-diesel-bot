import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { PanelUsersApi } from "../api/resources";
import type { PanelUser } from "../api/types";

export function PanelUsersPage() {
  const [users, setUsers] = useState<PanelUser[]>([]);
  const [form, setForm] = useState({ login: "", password: "", role: "operator" });
  const [error, setError] = useState<string | null>(null);

  const load = () => void PanelUsersApi.list().then(setUsers);
  useEffect(load, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await PanelUsersApi.create(form);
      setForm({ login: "", password: "", role: "operator" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать пользователя");
    }
  };

  return (
    <div>
      <h1>Пользователи панели</h1>
      <form onSubmit={onCreate} className="inline-form">
        <input
          placeholder="Логин"
          value={form.login}
          onChange={(e) => setForm({ ...form, login: e.target.value })}
          required
        />
        <input
          placeholder="Пароль"
          type="password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          required
          minLength={8}
        />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="operator">Operator</option>
          <option value="administrator">Administrator</option>
          <option value="super_admin">Super Admin</option>
        </select>
        <button type="submit">Создать</button>
      </form>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>Логин</th>
            <th>Роль</th>
            <th>Заблокирован</th>
            <th>Последний вход</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.login}</td>
              <td>{u.role}</td>
              <td>{u.is_blocked ? "Да" : "Нет"}</td>
              <td>{u.last_login_at ? new Date(u.last_login_at).toLocaleString("ru-RU") : "—"}</td>
              <td>
                <button
                  onClick={() =>
                    PanelUsersApi.update(u.id, { is_blocked: !u.is_blocked }).then(load)
                  }
                >
                  {u.is_blocked ? "Разблокировать" : "Заблокировать"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
