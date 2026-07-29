import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useTheme } from "../theme/ThemeContext";

interface NavItem {
  to: string;
  label: string;
  permission: string;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", permission: "view_dashboard" },
  { to: "/participants", label: "Участники", permission: "view_participants" },
  { to: "/sales", label: "Продажи On-Line", permission: "view_sales" },
  {
    to: "/manual-registrations",
    label: "Ручные регистрации",
    permission: "manual_registration_create",
  },
  { to: "/tickets", label: "Номера", permission: "view_tickets" },
  { to: "/giveaways", label: "Коллекции", permission: "view_giveaways" },
  { to: "/broadcasts", label: "Рассылки", permission: "broadcast_view" },
  { to: "/reports", label: "Отчёты", permission: "reports_view" },
  { to: "/settings", label: "Настройки", permission: "settings_view" },
  { to: "/panel-users", label: "Пользователи панели", permission: "panel_users_manage" },
  { to: "/audit", label: "Журнал аудита", permission: "audit_view" },
];

export function Layout() {
  const { user, hasPermission, logout } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const [navOpen, setNavOpen] = useState(false);

  const nav = (
    <nav>
      {NAV_ITEMS.filter((item) => hasPermission(item.permission)).map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className="nav-link"
          onClick={() => setNavOpen(false)}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="layout">
      <div className="mobile-topbar">
        <button className="mobile-nav-toggle" onClick={() => setNavOpen(true)}>
          ☰
        </button>
        <span className="sidebar-title">Платформа коллекций</span>
      </div>
      {navOpen && <div className="sidebar-backdrop" onClick={() => setNavOpen(false)} />}
      <aside className={`sidebar${navOpen ? " sidebar-open" : ""}`}>
        <div className="sidebar-header">
          <div className="sidebar-title">Платформа коллекций</div>
          <button className="mobile-nav-toggle" onClick={() => setNavOpen(false)}>
            ✕
          </button>
        </div>
        {nav}
        <div className="sidebar-footer">
          <div className="user-info">
            {user?.login} · {user?.role}
          </div>
          <div className="footer-actions">
            <button className="theme-toggle" onClick={toggleTheme} title="Переключить тему">
              {resolvedTheme === "dark" ? "☀️ Светлая" : "🌙 Тёмная"}
            </button>
            <button className="theme-toggle" onClick={logout}>
              Выйти
            </button>
          </div>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
