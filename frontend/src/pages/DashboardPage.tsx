import { useEffect, useState } from "react";
import { DashboardApi } from "../api/resources";
import type { Dashboard } from "../api/types";

export function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);

  useEffect(() => {
    void DashboardApi.get().then(setData);
  }, []);

  if (!data) return <div>Загрузка…</div>;

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
          <div className="card-value">{(data.revenue_total / 100).toFixed(2)} ₽</div>
          <div className="card-label">Выручка</div>
        </div>
        <div className="card">
          <div className="card-value">{data.giveaways_count}</div>
          <div className="card-label">Розыгрышей</div>
        </div>
      </div>
    </div>
  );
}
