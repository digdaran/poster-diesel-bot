/**
 * HTTP-клиент API панели: JWT access+refresh ТОЛЬКО через заголовок Authorization,
 * без cookie (п.11.1 ТЗ). Refresh-токен хранится в localStorage (persist между
 * перезагрузками страницы); access-токен держим в памяти и дублируем в
 * localStorage для восстановления сессии при полной перезагрузке вкладки.
 */

const REFRESH_KEY = "raffle_refresh_token";
const ACCESS_KEY = "raffle_access_token";

let accessToken: string | null = localStorage.getItem(ACCESS_KEY);

export function getAccessToken(): string | null {
  return accessToken;
}

export function setTokens(access: string, refresh?: string): void {
  accessToken = access;
  localStorage.setItem(ACCESS_KEY, access);
  if (refresh) {
    localStorage.setItem(REFRESH_KEY, refresh);
  }
}

export function clearTokens(): void {
  accessToken = null;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  const resp = await fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!resp.ok) {
    clearTokens();
    return false;
  }
  const data = await resp.json();
  setTokens(data.access_token);
  return true;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
    return fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  };

  let resp = await doFetch();
  if (resp.status === 401 && getRefreshToken()) {
    const refreshed = await refreshAccessToken();
    if (refreshed) resp = await doFetch();
  }

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = data.detail ?? detail;
    } catch {
      /* тело не JSON — оставляем statusText */
    }
    throw new ApiError(resp.status, detail);
  }

  if (resp.status === 204) return undefined as T;
  const contentType = resp.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return (await resp.json()) as T;
  }
  return (await resp.blob()) as unknown as T;
}

export async function apiDownload(
  path: string,
  query?: RequestOptions["query"],
): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  const resp = await fetch(buildUrl(path, query), { headers });
  if (!resp.ok) throw new ApiError(resp.status, resp.statusText);
  const disposition = resp.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match ? match[1] : "export";
  return { blob: await resp.blob(), filename };
}
