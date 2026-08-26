export function EmptyStateRow({
  colSpan,
  message = "Нет данных",
}: {
  colSpan: number;
  message?: string;
}) {
  return (
    <tr className="empty-state-row">
      <td colSpan={colSpan}>{message}</td>
    </tr>
  );
}

export function LoadingState({ message = "Загрузка…" }: { message?: string }) {
  return (
    <div className="loading-state">
      <span>{message}</span>
      <div className="loading-state-bars" aria-hidden="true">
        <span className="skeleton-bar" />
        <span className="skeleton-bar" />
        <span className="skeleton-bar" />
      </div>
    </div>
  );
}
