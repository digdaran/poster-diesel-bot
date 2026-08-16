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
  SalesByPeriodRow,
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
  channel?: string;
  created_from?: string;
  created_to?: string;
  total_tickets_min?: number;
  total_tickets_max?: number;
  active_tickets_min?: number;
  active_tickets_max?: number;
}

export const ParticipantsApi = {
  list: (params: ParticipantsFilter = {}) =>
    apiRequest<Page<Participant>>("/api/participants", { query: { ...params } }),
  findByPhone: (phone: string) =>
    apiRequest<Participant | null>("/api/participants/by-phone", { query: { phone } }),
  get: (id: number) => apiRequest<Participant>(`/api/participants/${id}`),
  update: (id: number, payload: { full_name?: string; phone?: string }) =>
    apiRequest<Participant>(`/api/participants/${id}`, { method: "PATCH", body: payload }),
  block: (id: number) =>
    apiRequest<Participant>(`/api/participants/${id}/block`, { method: "POST" }),
  unblock: (id: number) =>
    apiRequest<Participant>(`/api/participants/${id}/unblock`, { method: "POST" }),
};

export interface GiveawaysFilter {
  q?: string;
  is_registration_open?: boolean;
  is_locked?: boolean;
  is_archived?: boolean;
}

export const GiveawaysApi = {
  list: (params: GiveawaysFilter = {}) =>
    apiRequest<Giveaway[]>("/api/giveaways", { query: { ...params } }),
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
  archive: (id: number) => apiRequest<Giveaway>(`/api/giveaways/${id}/archive`, { method: "POST" }),
  unarchive: (id: number) =>
    apiRequest<Giveaway>(`/api/giveaways/${id}/unarchive`, { method: "POST" }),
  listPosters: (id: number) => apiRequest<GiveawayPoster[]>(`/api/giveaways/${id}/posters`),
  uploadPoster: (id: number, file: File) =>
    apiUpload<GiveawayPoster>(`/api/giveaways/${id}/posters`, file),
  deletePoster: (id: number, posterId: number) =>
    apiRequest<void>(`/api/giveaways/${id}/posters/${posterId}`, { method: "DELETE" }),
  posterFileUrl: (id: number, posterId: number) => `/api/giveaways/${id}/posters/${posterId}/file`,
};

export interface ManualRegistrationsFilter extends PageParams {
  giveaway_id?: number;
  participant_id?: number;
  participant_query?: string;
  operator_query?: string;
  payment_method?: string;
  invoice_no?: string;
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
  switchToCash: (id: number) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/switch-to-cash`, {
      method: "POST",
    }),
  refund: (id: number, reason: string) =>
    apiRequest<ManualRegistration>(`/api/manual-registrations/${id}/refund`, {
      method: "POST",
      body: { reason },
    }),
};

export interface SalesFilter extends PageParams {
  giveaway_id?: number;
  status_filter?: string;
  order_id?: string;
  invoice_no?: string;
  provider?: string;
  channel?: string;
  participant_id?: number;
  participant_query?: string;
  amount_mismatch?: boolean;
  oversold?: boolean;
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
  refund: (id: number, reason: string) =>
    apiRequest<Payment>(`/api/payments/${id}/refund`, { method: "POST", body: { reason } }),
};

export interface TicketsFilter extends PageParams {
  giveaway_id?: number;
  full_code?: string;
  participant_id?: number;
  participant_query?: string;
  source?: string;
  channel?: string;
  payment_id?: number;
  manual_registration_id?: number;
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

export interface AuditFilter extends PageParams {
  action?: string;
  entity_type?: string;
  entity_id?: number;
  actor_query?: string;
  ip_address?: string;
  created_from?: string;
  created_to?: string;
}

export const AuditApi = {
  list: (params: AuditFilter = {}) =>
    apiRequest<Page<AuditLogEntry>>("/api/audit", { query: { ...params } }),
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
      revenue_offline_cash: number;
      revenue_offline_cashless: number;
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
  salesByPeriod: (params: {
    granularity?: "hour" | "day" | "month";
    giveaway_id?: number;
    date_from?: string;
    date_to?: string;
  }) => apiRequest<SalesByPeriodRow[]>("/api/reports/sales-by-period", { query: { ...params } }),
};
