import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useTheme } from "../theme/ThemeContext";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { CommandPalette } from "./CommandPalette";
import type { CommandPaletteItem } from "./CommandPalette";

interface NavItem {
  to: string;
  label: string;
  permission: string;
  icon: IconName;
  // Открывается в отдельной вкладке/окне без сайдбара (см. App.tsx) — для
  // мониторинга на весь экран, а не обычная SPA-навигация.
  newTab?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", permission: "view_dashboard", icon: "dashboard" },
  {
    to: "/participants",
    label: "Участники",
    permission: "view_participants",
    icon: "participants",
  },
  { to: "/sales", label: "Продажи On-Line", permission: "view_sales", icon: "sales" },
  {
    to: "/monitoring",
    label: "Мониторинг",
    permission: "view_sales",
    icon: "monitoring",
    newTab: true,
  },
  {
    to: "/manual-registrations",
    label: "Ручные регистрации",
    permission: "manual_registration_create",
    icon: "manual-registrations",
  },
  { to: "/tickets", label: "Номера", permission: "view_tickets", icon: "tickets" },
  { to: "/giveaways", label: "Коллекции", permission: "view_giveaways", icon: "giveaways" },
  { to: "/archive", label: "Архив", permission: "view_giveaways", icon: "archive" },
  { to: "/broadcasts", label: "Рассылки", permission: "broadcast_view", icon: "broadcasts" },
  { to: "/reports", label: "Отчёты", permission: "reports_view", icon: "reports" },
  { to: "/settings", label: "Настройки", permission: "settings_view", icon: "settings" },
  {
    to: "/panel-users",
    label: "Пользователи панели",
    permission: "panel_users_manage",
    icon: "panel-users",
  },
  { to: "/audit", label: "Журнал аудита", permission: "audit_view", icon: "audit" },
];

const COLLAPSE_STORAGE_KEY = "raffle_sidebar_collapsed";

export function Layout() {
  const { user, hasPermission, logout } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const [navOpen, setNavOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1",
  );
  const [paletteOpen, setPaletteOpen] = useState(false);

  const visibleItems = NAV_ITEMS.filter((item) => hasPermission(item.permission));

  useEffect(() => {
    localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const paletteItems: CommandPaletteItem[] = visibleItems.map((item) => ({
    to: item.to,
    label: item.label,
    icon: item.icon,
    newTab: item.newTab,
  }));

  const nav = (
    <nav>
      {visibleItems.map((item) =>
        item.newTab ? (
          <a
            key={item.to}
            href={item.to}
            target="_blank"
            rel="noopener noreferrer"
            className="nav-link"
            title={collapsed ? item.label : undefined}
          >
            <Icon name={item.icon} />
            <span className="nav-link-label">{item.label}</span>
            <Icon name="external" size={12} className="nav-link-external-mark" />
          </a>
        ) : (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className="nav-link"
            title={collapsed ? item.label : undefined}
            onClick={() => setNavOpen(false)}
          >
            <Icon name={item.icon} />
            <span className="nav-link-label">{item.label}</span>
          </NavLink>
        ),
      )}
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
      <aside
        className={`sidebar${navOpen ? " sidebar-open" : ""}${collapsed ? " is-collapsed" : ""}`}
      >
        <div className="sidebar-header">
          <div className="sidebar-title">
            <span className="sidebar-mark">К</span>
            {!collapsed && <span style={{ marginLeft: 8 }}>Платформа коллекций</span>}
          </div>
          <button
            className="mobile-nav-toggle"
            onClick={() => setNavOpen(false)}
            aria-label="Закрыть меню"
          >
            ✕
          </button>
          <button
            className="sidebar-collapse-toggle"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? "Развернуть меню" : "Свернуть меню"}
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
          >
            <Icon name={collapsed ? "chevrons-right" : "chevrons-left"} size={15} />
          </button>
        </div>
        <button className="sidebar-search-trigger" onClick={() => setPaletteOpen(true)}>
          <Icon name="search" size={14} />
          <span>Быстрый переход…</span>
          <span className="hint">
            <span className="kbd">Ctrl</span>
            <span className="kbd">K</span>
          </span>
        </button>
        {nav}
        <div className="sidebar-footer">
          <div className="user-info">
            {user?.login} · {user?.role}
          </div>
          <div className="footer-actions">
            <button className="theme-toggle" onClick={toggleTheme} title="Переключить тему">
              <Icon name={resolvedTheme === "dark" ? "sun" : "moon"} size={15} />
              {!collapsed && (resolvedTheme === "dark" ? "Светлая" : "Тёмная")}
            </button>
            <button className="theme-toggle" onClick={logout} title="Выйти">
              <Icon name="logout" size={15} />
              {!collapsed && "Выйти"}
            </button>
          </div>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
      <CommandPalette
        items={paletteItems}
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
  );
}
