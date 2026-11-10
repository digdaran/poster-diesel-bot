import { useEffect, useState } from "react";
import { SystemApi } from "../api/resources";
import type { SystemChannelHealth } from "../api/types";
import { formatDateTime } from "../utils/format";

// Сбой связи с Telegram (заблокировали прокси, лёг туннель) раньше было видно только
// по жалобам участников: сообщения молча копились в очереди (DECISIONS_LOG.md №94).
// Backend раз в минуту проверяет Telegram, панель показывает плашку, пока он недоступен.

const POLL_INTERVAL_MS = 60 * 1000;
// Короткие обрывы не показываем: плашка появляется, если сбой длится дольше этого.
const MIN_OUTAGE_MS = 3 * 60 * 1000;

function utcMs(iso: string): number {
  return new Date(/[Zz]|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`).getTime();
}

export function ChannelHealthBanner() {
  const [health, setHealth] = useState<SystemChannelHealth | null>(null);

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const data = await SystemApi.channelHealth();
        if (!stopped) setHealth(data);
      } catch {
        // Сам бэкенд недоступен — это видно и без плашки, проверим в следующий раз.
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };

    void check();
    const timer = window.setInterval(() => void check(), POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const telegram = health?.telegram;
  if (!telegram || telegram.ok !== false || !telegram.failing_since) return null;
  if (Date.now() - utcMs(telegram.failing_since) < MIN_OUTAGE_MS) return null;

  const pending = health.pending_telegram_deliveries;
  return (
    <div className="health-banner" role="alert">
      <span>
        <strong>Telegram-бот недоступен</strong> с {formatDateTime(telegram.failing_since)}
        {pending > 0 ? ` — сообщения участникам ждут в очереди (${pending})` : ""}. Сообщите
        администратору сервера.
      </span>
    </div>
  );
}
