import { useEffect, useState } from "react";
import { DashboardApi } from "../api/resources";
import type { Dashboard } from "../api/types";
import { LoadingState } from "../components/EmptyState";
import { formatMoney } from "../utils/format";

export function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);

  useEffect(() => {
    void DashboardApi.get().then(setData);
  }, []);

  if (!data) return <LoadingState />;

  return (
    <div>
      <h1>Dashboard</h1>
      <div className="cards">
        <div className="card">
          <div className="card-value">{data.participants_count}</div>
          <div className="card-label">Участников</div>
        </div>
        <div className="card">
          <div className="card-value">{data.tickets_issued_count}</div>
          <div className="card-label">Номерков выдано</div>
        </div>
        <div className="card">
          <div className="card-value">{formatMoney(data.revenue_online)}</div>
          <div className="card-label">Эквайринг</div>
        </div>
        <div className="card">
          <div className="card-value">{formatMoney(data.revenue_offline)}</div>
          <div className="card-label">Наличные (оператор)</div>
        </div>
        <div className="card">
          <div className="card-value">{formatMoney(data.revenue_total)}</div>
          <div className="card-label">Итого выручка</div>
        </div>
        <div className="card">
          <div className="card-value">{data.giveaways_count}</div>
          <div className="card-label">Розыгрышей</div>
        </div>
      </div>
    </div>
  );
}
