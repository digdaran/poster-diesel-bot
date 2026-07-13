import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

interface Props {
  children: ReactNode;
  permission?: string;
}

/**
 * Клиентское скрытие/редирект — только UX (п.11.2 ТЗ). Реальная защита — на
 * сервере (`require_permission` в backend/api/deps.py); при обращении к API без
 * прав сервер всё равно вернёт 403 независимо от этой проверки.
 */
export function ProtectedRoute({ children, permission }: Props) {
  const { user, loading, hasPermission } = useAuth();

  if (loading) return <div className="page-loading">Загрузка…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (permission && !hasPermission(permission)) {
    return <div className="page-forbidden">Недостаточно прав для просмотра этого раздела.</div>;
  }
  return <>{children}</>;
}
