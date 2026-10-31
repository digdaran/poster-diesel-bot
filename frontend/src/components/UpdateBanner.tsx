import { useEffect, useState } from "react";

// Открытая вкладка панели не обновляется сама после деплоя и продолжает работать
// на старом коде (на проде так оператор видел 10 номерков вместо 12 — старая
// версия не учитывала подарки, DECISIONS_LOG.md №93). Периодически сверяем
// бандл, который отдаёт сервер, с загруженным в этой вкладке.

const CHECK_INTERVAL_MS = 5 * 60 * 1000;
const BUNDLE_RE = /\/assets\/index-[\w-]+\.js/;

function loadedBundle(): string | null {
  const scripts = document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]');
  for (const script of scripts) {
    const match = new URL(script.src, window.location.href).pathname.match(BUNDLE_RE);
    if (match) return match[0];
  }
  return null; // dev-сервер Vite: собранного бандла нет — проверка не нужна
}

async function serverBundle(): Promise<string | null> {
  const response = await fetch("/index.html", { cache: "no-store" });
  if (!response.ok) return null;
  return (await response.text()).match(BUNDLE_RE)?.[0] ?? null;
}

export function UpdateBanner() {
  const [outdated, setOutdated] = useState(false);

  useEffect(() => {
    const current = loadedBundle();
    if (current === null) return;

    let stopped = false;
    const check = async () => {
      try {
        const latest = await serverBundle();
        if (!stopped && latest !== null && latest !== current) setOutdated(true);
      } catch {
        // Сеть недоступна — проверим в следующий раз.
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };

    const timer = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (!outdated) return null;
  return (
    <div className="update-banner" role="alert">
      <span>Панель обновилась. Обновите страницу, чтобы работать в новой версии.</span>
      <button onClick={() => window.location.reload()}>Обновить</button>
    </div>
  );
}
