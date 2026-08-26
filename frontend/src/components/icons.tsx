// Небольшой набор геометричных линейных иконок для сайдбара, командной
// палитры и переключателей — без внешней зависимости (в проекте нет
// иконного пакета, а тянуть его ради ~20 иконок не оправдано). Единый стиль:
// stroke=currentColor, толщина 1.6, скруглённые концы.
import type { ReactNode } from "react";

export type IconName =
  | "dashboard"
  | "participants"
  | "sales"
  | "monitoring"
  | "manual-registrations"
  | "tickets"
  | "giveaways"
  | "archive"
  | "broadcasts"
  | "reports"
  | "settings"
  | "panel-users"
  | "audit"
  | "search"
  | "sun"
  | "moon"
  | "logout"
  | "chevrons-left"
  | "chevrons-right"
  | "external";

const PATHS: Record<IconName, ReactNode> = {
  dashboard: (
    <>
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="5" rx="1.5" />
      <rect x="13" y="10" width="8" height="11" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
    </>
  ),
  participants: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6" />
      <circle cx="17.5" cy="9.5" r="2.3" />
      <path d="M15.8 14.3c2.5 0.3 4.4 2.2 4.7 5.2" />
    </>
  ),
  sales: (
    <>
      <line x1="2" y1="20" x2="22" y2="20" />
      <line x1="5" y1="20" x2="5" y2="12" />
      <line x1="10.5" y1="20" x2="10.5" y2="6" />
      <line x1="16" y1="20" x2="16" y2="14" />
      <line x1="20" y1="20" x2="20" y2="9" />
    </>
  ),
  monitoring: <polyline points="2,13 7,13 9.5,5.5 13,19.5 15.5,13 22,13" />,
  "manual-registrations": (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4V3.5a1 1 0 011-1h4a1 1 0 011 1V4" />
      <path d="M8.5 13l2.4 2.4L16 10" />
    </>
  ),
  tickets: (
    <>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <line x1="9" y1="6" x2="9" y2="18" strokeDasharray="2 2.4" />
      <circle cx="9" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  giveaways: (
    <>
      <polygon points="12,3 21,8 12,13 3,8" />
      <polyline points="3,13 12,18 21,13" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="5" rx="1.5" />
      <rect x="5" y="9" width="14" height="11" rx="1.5" />
      <line x1="10" y1="13.5" x2="14" y2="13.5" />
    </>
  ),
  broadcasts: <path d="M22 2L11 13M22 2l-7 20-4-9-9-4z" />,
  reports: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <polyline points="7,15 10,10 13,13 17,7" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="7" />
      <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
      <line x1="12" y1="2.5" x2="12" y2="5.3" />
      <line x1="12" y1="18.7" x2="12" y2="21.5" />
      <line x1="2.5" y1="12" x2="5.3" y2="12" />
      <line x1="18.7" y1="12" x2="21.5" y2="12" />
    </>
  ),
  "panel-users": (
    <>
      <path d="M12 2.3l7 2.9v6.1c0 5-3.1 8.4-7 9.7-3.9-1.3-7-4.7-7-9.7V5.2l7-2.9z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  audit: (
    <>
      <circle cx="4.5" cy="6" r="1" fill="currentColor" stroke="none" />
      <line x1="8" y1="6" x2="20" y2="6" />
      <circle cx="4.5" cy="12" r="1" fill="currentColor" stroke="none" />
      <line x1="8" y1="12" x2="20" y2="12" />
      <circle cx="4.5" cy="18" r="1" fill="currentColor" stroke="none" />
      <line x1="8" y1="18" x2="20" y2="18" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="20" y1="20" x2="15.3" y2="15.3" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <g strokeLinecap="round">
        <line x1="12" y1="2" x2="12" y2="4.5" />
        <line x1="12" y1="19.5" x2="12" y2="22" />
        <line x1="2" y1="12" x2="4.5" y2="12" />
        <line x1="19.5" y1="12" x2="22" y2="12" />
        <line x1="4.9" y1="4.9" x2="6.6" y2="6.6" />
        <line x1="17.4" y1="17.4" x2="19.1" y2="19.1" />
        <line x1="4.9" y1="19.1" x2="6.6" y2="17.4" />
        <line x1="17.4" y1="6.6" x2="19.1" y2="4.9" />
      </g>
    </>
  ),
  moon: <path d="M20 14.5A8.5 8.5 0 1110.3 3.2a6.7 6.7 0 009.7 11.3z" />,
  logout: (
    <>
      <path d="M9.5 21H5.3a2 2 0 01-2-2V5a2 2 0 012-2H9.5" />
      <polyline points="15.5,17 20.5,12 15.5,7" />
      <line x1="20.5" y1="12" x2="8.7" y2="12" />
    </>
  ),
  "chevrons-left": (
    <>
      <polyline points="11.5,17 6.5,12 11.5,7" />
      <polyline points="18.5,17 13.5,12 18.5,7" />
    </>
  ),
  "chevrons-right": (
    <>
      <polyline points="12.5,17 17.5,12 12.5,7" />
      <polyline points="5.5,17 10.5,12 5.5,7" />
    </>
  ),
  external: (
    <>
      <path d="M14 3h7v7" />
      <path d="M21 3l-9.5 9.5" />
      <path d="M19 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h6" />
    </>
  ),
};

export function Icon({
  name,
  size = 18,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={"icon" + (className ? ` ${className}` : "")}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
