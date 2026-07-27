import { apiDownload, apiRequest, apiUpload } from "./client";
import type {
  AuditLogEntry,
  BankReconciliationStatus,
  Broadcast,
  ChannelSalesRow,
  Dashboard,
  Giveaway,
  GiveawayPoster,
  ManualRegistration,
  Page,
  PageSize,
  PanelUser,
  Participant,
  Payment,
  PaymentReceipt,
  PlatformSettings,
  RevenueByGiveawayRow,
  Ticket,
} from "./types";

interface PageParams {
  page?: number;
  page_size?: PageSize;
}

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

export interface ParticipantsFilter extends PageParams {
  q?: string;
  phone_verified?: boolean;
  is_blocked?: boolean;
  created_from?: string;
  created_to?: string;
}

export const ParticipantsApi = {
  list: (params: ParticipantsFilter = {}) =>
    apiRequest<Page<Participant>>("/api/participants", { query: { ...params } }),
  findByPhone: (phone: string) =>
    apiRequest<Participant | null>("/api/participants/by-phone", { query: { phone } }),
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
  listPosters: (id: number) => apiRequest<GiveawayPoster[]>(`/api/giveaways/${id}/posters`),
  uploadPoster: (id: number, file: File) =>
    apiUpload<GiveawayPoster>(`/api/giveaways/${id}/posters`, file),
  deletePoster: (id: number, posterId: number) =>
    apiRequest<void>(`/api/giveaways/${id}/posters/${posterId}`, { method: "DELETE" }),
  posterFileUrl: (id: number, posterId: number) => `/api/giveaways/${id}/posters/${posterId}/file`,
};

export interface ManualRegistrationsFilter extends PageParams {
  giveaway_id?: number;
  participant_query?: string;
  status_filter?: string;
  created_from?: string;
  created_to?: string;
}

export const ManualRegistrationsApi = {
  list: (params: ManualRegistrationsFilter = {}) =>
    apiRequest<Page<ManualRegistration>>("/api/manual-registrations", { query: { ...params } }),
  create: (payload: {
    giveaway_id: number;
    participant_phone: string;
    participant_full_name: string;
    quantity: number;
    comment?: string;
  }) =>
    apiRequest<ManualRegistration>("/api/manual-registrations", { method: "POST", body: payload }),
  confirm: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/confirm`, { method: "POST" }),
  cancel: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/cancel`, { method: "POST" }),
  generateQr: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/generate-qr`, {
      method: "POST",
    }),
  qrPngUrl: (id: number) => `/api/manual-registrations/${id}/qr.png`,
};

export interface SalesFilter extends PageParams {
  giveaway_id?: number;
  status_filter?: string;
  order_id?: string;
  provider?: string;
  channel?: string;
  participant_query?: string;
  created_from?: string;
  created_to?: string;
}

export const SalesApi = {
  list: (params: SalesFilter = {}) =>
    apiRequest<Page<Payment>>("/api/payments", { query: { ...params } }),
  listReceipts: (paymentId: number) =>
    apiRequest<PaymentReceipt[]>(`/api/payments/${paymentId}/receipts`),
  downloadReceipt: (paymentId: number, receiptId: number) =>
    apiDownload(`/api/payments/${paymentId}/receipts/${receiptId}/file`),
};

export interface TicketsFilter extends PageParams {
  giveaway_id?: number;
  full_code?: string;
  participant_query?: string;
  source?: string;
  channel?: string;
  created_from?: string;
  created_to?: string;
}

export const TicketsApi = {
  list: (params: TicketsFilter = {}) =>
    apiRequest<Page<Ticket>>("/api/tickets", { query: { ...params } }),
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

export const BankReconciliationApi = {
  getStatus: () => apiRequest<BankReconciliationStatus>("/api/bank-reconciliation/status"),
};

export const ReportsApi = {
  financialSummary: (giveaway_id?: number) =>
    apiRequest<{
      revenue_online: number;
      revenue_offline: number;
      revenue_total: number;
      successful_payments_count: number;
      average_check: number;
    }>("/api/reports/financial-summary", { query: { giveaway_id } }),
  onlineVsOffline: (giveaway_id?: number) =>
    apiRequest<Record<string, { count: number; amount: number }>>(
      "/api/reports/online-vs-offline",
      {
        query: { giveaway_id },
      },
    ),
  revenueByGiveaway: () => apiRequest<RevenueByGiveawayRow[]>("/api/reports/revenue-by-giveaway"),
  salesByChannel: (giveaway_id?: number) =>
    apiRequest<ChannelSalesRow[]>("/api/reports/by-channel", { query: { giveaway_id } }),
};
