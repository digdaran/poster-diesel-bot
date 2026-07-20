import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { PanelUsersApi } from "../api/resources";
import type { PanelUser } from "../api/types";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { useToast } from "../components/Toast";
import { useConfirm } from "../components/ConfirmDialog";
import { Badge } from "../components/Badge";
import { EmptyStateRow } from "../components/EmptyState";
import { formatDateTime } from "../utils/format";

export function PanelUsersPage() {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [users, setUsers] = useState<PanelUser[]>([]);
  const [form, setForm] = useState({ login: "", password: "", role: "operator" });
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);

  const load = () => void PanelUsersApi.list().then(setUsers);
  useEffect(load, []);

  const { run: onCreate, pending: creating } = useAsyncAction(async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await PanelUsersApi.create(form);
      setForm({ login: "", password: "", role: "operator" });
      showToast("Пользователь создан");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать пользователя");
    }
  });

  const toggleBlock = async (u: PanelUser) => {
    if (!u.is_blocked) {
      const confirmed = await confirm(`Заблокировать доступ пользователю «${u.login}»?`);
      if (!confirmed) return;
    }
    setPendingId(u.id);
    try {
      await PanelUsersApi.update(u.id, { is_blocked: !u.is_blocked });
      showToast(u.is_blocked ? "Пользователь разблокирован" : "Пользователь заблокирован");
      load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Не удалось выполнить действие", "error");
    } finally {
      setPendingId(null);
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
        <button type="submit" disabled={creating}>
          {creating ? "Создаём…" : "Создать"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      <div className="table-wrapper">
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
            {users.length === 0 && <EmptyStateRow colSpan={5} />}
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.login}</td>
                <td>{u.role}</td>
                <td>
                  <Badge tone={u.is_blocked ? "danger" : "muted"}>
                    {u.is_blocked ? "Да" : "Нет"}
                  </Badge>
                </td>
                <td>{u.last_login_at ? formatDateTime(u.last_login_at) : "—"}</td>
                <td>
                  <button
                    className={u.is_blocked ? undefined : "button-danger"}
                    disabled={pendingId === u.id}
                    onClick={() => void toggleBlock(u)}
                  >
                    {u.is_blocked ? "Разблокировать" : "Заблокировать"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
