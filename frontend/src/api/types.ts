export interface MeResponse {
  id: number;
  login: string;
  role: "super_admin" | "administrator" | "operator";
  permissions: string[];
}

export interface Participant {
  id: number;
  phone: string;
  phone_verified: boolean;
  full_name: string | null;
  is_blocked: boolean;
  channels: string[];
  created_at: string;
}

export interface Giveaway {
  id: number;
  name: string;
  prefix: string;
  ticket_price: number;
  max_tickets: number;
  tickets_issued: number;
  tickets_reserved: number;
  is_registration_open: boolean;
  is_locked: boolean;
  opened_at: string | null;
  digital_poster_caption: string | null;
  created_at: string;
}

export interface ManualRegistration {
  id: number;
  participant_id: number;
  participant_phone: string;
  participant_full_name: string | null;
  participant_channels: string[];
  giveaway_id: number;
  giveaway_name: string;
  quantity: number;
  revenue: number;
  status: "PENDING" | "CONFIRMED" | "CANCELLED";
  operator_id: number;
  operator_login: string;
  comment: string | null;
  created_at: string;
  confirmed_at: string | null;
  cancelled_at: string | null;
}

export interface Payment {
  id: number;
  order_id: string;
  participant_id: number;
  participant_phone: string;
  participant_full_name: string | null;
  giveaway_id: number;
  giveaway_name: string;
  provider: string;
  channel: string | null;
  amount: number;
  quantity: number;
  status: "PENDING" | "SUCCEEDED" | "FAILED";
  created_at: string;
  confirmed_at: string | null;
  invoice_no: string | null;
  oversold: boolean;
  amount_mismatch: boolean;
  amount_mismatch_bank_amount: number | null;
  receipt_count: number;
}

export interface PaymentReceipt {
  id: number;
  payment_id: number;
  original_filename: string | null;
  content_type: string | null;
  uploaded_at: string;
}

export interface Ticket {
  id: number;
  giveaway_id: number;
  giveaway_name: string;
  number: number;
  full_code: string;
  participant_id: number;
  participant_phone: string;
  participant_full_name: string | null;
  source: "online" | "manual";
  channel: string | null;
  created_at: string;
}

export interface PanelUser {
  id: number;
  login: string;
  role: string;
  is_blocked: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface PlatformSettings {
  payment_provider_override: string | null;
  ignore_phone_verification: boolean;
  online_status_poll_interval_sec: number;
  online_status_poll_max_attempts: number;
  online_reservation_ttl_sec: number;
  manual_reservation_ttl_sec: number;
  support_contacts: Record<string, string>;
}

export interface AuditLogEntry {
  id: number;
  action: string;
  actor_type: string;
  actor_id: number | null;
  actor_label: string;
  entity_type: string | null;
  entity_id: number | null;
  details: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
}

export interface Broadcast {
  id: number;
  title: string;
  message_text: string;
  audience_filter: Record<string, unknown>;
  status: "DRAFT" | "SENDING" | "SENT" | "FAILED";
  stats: Record<string, number>;
  created_at: string;
  sent_at: string | null;
}

export interface Dashboard {
  participants_count: number;
  tickets_issued_count: number;
  revenue_online: number;
  revenue_offline: number;
  revenue_total: number;
  giveaways_count: number;
}

export interface RevenueByGiveawayRow {
  giveaway_id: number;
  giveaway_name: string;
  revenue_online: number;
  revenue_offline: number;
  revenue_total: number;
  tickets_issued: number;
}

export interface ChannelSalesRow {
  channel: string;
  count: number;
  amount: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const PAGE_SIZES = [10, 50, 100, 500] as const;
export type PageSize = (typeof PAGE_SIZES)[number];
