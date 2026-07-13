import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

interface NavItem {
  to: string;
  label: string;
  permission: string;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", permission: "view_dashboard" },
  { to: "/participants", label: "Участники", permission: "view_participants" },
  { to: "/sales", label: "Продажи", permission: "view_sales" },
  {
    to: "/manual-registrations",
    label: "Ручные регистрации",
    permission: "manual_registration_create",
  },
  { to: "/tickets", label: "Номерки", permission: "view_tickets" },
  { to: "/giveaways", label: "Розыгрыши", permission: "view_giveaways" },
  { to: "/broadcasts", label: "Рассылки", permission: "broadcast_view" },
  { to: "/reports", label: "Отчёты", permission: "reports_view" },
  { to: "/settings", label: "Настройки", permission: "settings_view" },
  { to: "/panel-users", label: "Пользователи панели", permission: "panel_users_manage" },
  { to: "/audit", label: "Журнал аудита", permission: "audit_view" },
];

export function Layout() {
  const { user, hasPermission, logout } = useAuth();

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-title">Платформа розыгрышей</div>
        <nav>
          {NAV_ITEMS.filter((item) => hasPermission(item.permission)).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-info">
            {user?.login} · {user?.role}
          </div>
          <button onClick={logout}>Выйти</button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
