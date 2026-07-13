import { apiRequest } from "./client";
import type {
  AuditLogEntry,
  Broadcast,
  Dashboard,
  Giveaway,
  ManualRegistration,
  PanelUser,
  Participant,
  Payment,
  PlatformSettings,
  Ticket,
} from "./types";

export const AuthApi = {
  login: (login: string, password: string) =>
    apiRequest<{ access_token: string; refresh_token: string }>("/api/auth/login", {
      method: "POST",
      body: { login, password },
    }),
  me: () => apiRequest<import("./types").MeResponse>("/api/auth/me"),
};

export const DashboardApi = {
  get: () => apiRequest<Dashboard>("/api/dashboard"),
};

export const ParticipantsApi = {
  list: (q?: string) => apiRequest<Participant[]>("/api/participants", { query: { q } }),
  get: (id: number) => apiRequest<Participant>(`/api/participants/${id}`),
  update: (id: number, full_name: string) =>
    apiRequest<Participant>(`/api/participants/${id}`, { method: "PATCH", body: { full_name } }),
  block: (id: number) =>
    apiRequest<Participant>(`/api/participants/${id}/block`, { method: "POST" }),
  unblock: (id: number) =>
    apiRequest<Participant>(`/api/participants/${id}/unblock`, { method: "POST" }),
};

export const GiveawaysApi = {
  list: () => apiRequest<Giveaway[]>("/api/giveaways"),
  get: (id: number) => apiRequest<Giveaway>(`/api/giveaways/${id}`),
  create: (payload: {
    name: string;
    prefix: string;
    ticket_price: number;
    max_tickets: number;
    digital_poster_caption?: string;
  }) => apiRequest<Giveaway>("/api/giveaways", { method: "POST", body: payload }),
  update: (id: number, payload: { name?: string; digital_poster_caption?: string }) =>
    apiRequest<Giveaway>(`/api/giveaways/${id}`, { method: "PATCH", body: payload }),
  open: (id: number) => apiRequest<Giveaway>(`/api/giveaways/${id}/open`, { method: "POST" }),
  lock: (id: number) => apiRequest<Giveaway>(`/api/giveaways/${id}/lock`, { method: "POST" }),
  unlock: (id: number) => apiRequest<Giveaway>(`/api/giveaways/${id}/unlock`, { method: "POST" }),
  closeRegistration: (id: number) =>
    apiRequest<Giveaway>(`/api/giveaways/${id}/close-registration`, { method: "POST" }),
};

export const ManualRegistrationsApi = {
  list: () => apiRequest<ManualRegistration[]>("/api/manual-registrations"),
  create: (payload: {
    giveaway_id: number;
    participant_phone: string;
    quantity: number;
    comment?: string;
  }) =>
    apiRequest<ManualRegistration>("/api/manual-registrations", { method: "POST", body: payload }),
  confirm: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/confirm`, { method: "POST" }),
  cancel: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/cancel`, { method: "POST" }),
};

export const SalesApi = {
  list: (params?: { giveaway_id?: number; status_filter?: string }) =>
    apiRequest<Payment[]>("/api/payments", { query: params }),
};

export const TicketsApi = {
  list: (giveaway_id?: number) => apiRequest<Ticket[]>("/api/tickets", { query: { giveaway_id } }),
};

export const PanelUsersApi = {
  list: () => apiRequest<PanelUser[]>("/api/panel-users"),
  create: (payload: { login: string; password: string; role: string }) =>
    apiRequest<PanelUser>("/api/panel-users", { method: "POST", body: payload }),
  update: (id: number, payload: { is_blocked?: boolean; role?: string; password?: string }) =>
    apiRequest<PanelUser>(`/api/panel-users/${id}`, { method: "PATCH", body: payload }),
};

export const SettingsApi = {
  get: () => apiRequest<PlatformSettings>("/api/settings"),
  updateSupportContacts: (support_contacts: Record<string, string>) =>
    apiRequest<PlatformSettings>("/api/settings/support-contacts", {
      method: "PATCH",
      body: { support_contacts },
    }),
  updatePaymentProvider: (payment_provider_override: string | null) =>
    apiRequest<PlatformSettings>("/api/settings/payment-provider", {
      method: "PATCH",
      body: { payment_provider_override },
    }),
  updateIgnorePhoneVerification: (ignore_phone_verification: boolean) =>
    apiRequest<PlatformSettings>("/api/settings/ignore-phone-verification", {
      method: "PATCH",
      body: { ignore_phone_verification },
    }),
};

export const AuditApi = {
  list: () => apiRequest<AuditLogEntry[]>("/api/audit"),
};

export const BroadcastsApi = {
  list: () => apiRequest<Broadcast[]>("/api/broadcasts"),
  create: (payload: {
    title: string;
    message_text: string;
    audience_filter?: Record<string, unknown>;
  }) => apiRequest<Broadcast>("/api/broadcasts", { method: "POST", body: payload }),
  send: (id: number) => apiRequest<Broadcast>(`/api/broadcasts/${id}/send`, { method: "POST" }),
};

export const ReportsApi = {
  financialSummary: (giveaway_id?: number) =>
    apiRequest<{ revenue_total: number; successful_payments_count: number; average_check: number }>(
      "/api/reports/financial-summary",
      { query: { giveaway_id } },
    ),
  onlineVsOffline: (giveaway_id?: number) =>
    apiRequest<Record<string, { count: number; amount: number }>>(
      "/api/reports/online-vs-offline",
      {
        query: { giveaway_id },
      },
    ),
};
