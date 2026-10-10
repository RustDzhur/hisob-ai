export type FieldError = {
  field: string;
  message: string;
  code?: string;
  params?: Record<string, unknown>;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: FieldError[] = [],
    public correlationId: string | null = null,
  ) {
    super(message);
  }
}

export type User = { id: string; email: string; full_name: string };
export type Organization = {
  id: string;
  name: string;
  tin: string | null;
  timezone: string;
  base_currency: string;
  role: string;
  permissions: string[];
};

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const hit = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

type Options = { method?: string; body?: unknown; idempotencyKey?: string };

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  if (opts.body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = readCookie("hisob_csrf");
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the server.");
  }
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(
      res.status,
      data?.code ?? "http_error",
      data?.message ?? "The request could not be completed.",
      data?.details ?? [],
      data?.correlation_id ?? res.headers.get("x-correlation-id"),
    );
  }
  return data as T;
}

export function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "unknown", "Unknown error.");
}

export type Member = {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  status: "active" | "suspended";
  joined_at: string;
};
export type Invitation = { id: string; email: string; role: string; expires_at: string; expired: boolean; created_at: string };
export type InvitationCreated = Invitation & { token: string };
export type InvitationPreview = {
  organization_name: string;
  role: string;
  email: string;
  state: "pending" | "expired" | "accepted" | "revoked";
};
export type Role = { code: string; name: string };

export type AuditEvent = {
  id: string;
  occurred_at: string;
  action: string;
  actor: { id: string; email: string; full_name: string } | null;
  object_type: string | null;
  object_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  correlation_id: string | null;
  ip: string | null;
};
export type AuditPage = { items: AuditEvent[]; total: number; page: number; page_size: number };
export type SessionInfo = {
  id: string;
  created_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
};

export type Currency = {
  code: string;
  minor_units: number;
  name_uz: string;
  name_ru: string;
  name_en: string;
  enabled: boolean;
  is_base: boolean;
};
export type Unit = {
  id: string;
  code: string;
  name_uz: string | null;
  name_ru: string | null;
  name_en: string | null;
  is_system: boolean;
  is_active: boolean;
};

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };
export type Counterparty = {
  id: string;
  name: string;
  tin: string | null;
  is_customer: boolean;
  is_supplier: boolean;
  address: string | null;
  phone: string | null;
  email: string | null;
  notes: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
};

export type Product = {
  id: string;
  kind: "goods" | "service";
  sku: string;
  name: string;
  unit: { id: string; code: string; name_uz: string | null; name_ru: string | null; name_en: string | null };
  price: string | null;
  price_currency: string | null;
  ikpu: string | null;
  description: string | null;
  barcodes: string[];
  archived: boolean;
  created_at: string;
  updated_at: string;
};

export type Warehouse = {
  id: string;
  name: string;
  address: string | null;
  notes: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
};

export type DocIssue = { code: string; severity: "error" | "warning"; field: string | null; params: Record<string, unknown> };
export type DocLine = {
  position: number;
  description: string;
  product_id: string | null;
  product_sku: string | null;
  unit_id: string | null;
  unit_code: string | null;
  quantity: string | null;
  unit_price: string | null;
  discount: string | null;
  tax_amount: string | null;
  amount: string | null;
};
export type DocumentFull = {
  id: string;
  doc_type: string;
  status: string;
  number: string | null;
  doc_date: string | null;
  counterparty: { id: string; name: string; tin: string | null; archived: boolean } | null;
  currency: string | null;
  total_amount: string | null;
  tax_total: string | null;
  notes: string | null;
  file: { original_name: string; content_type: string; size_bytes: number; sha256: string; inline: boolean } | null;
  lines: DocLine[];
  issues: DocIssue[];
  version: number;
  extraction: Extraction;
  duplicate_of_id: string | null;
  rejection_reason: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
};
export type DocumentListItem = {
  id: string;
  doc_type: string;
  status: string;
  number: string | null;
  doc_date: string | null;
  counterparty_name: string | null;
  currency: string | null;
  total_amount: string | null;
  original_name: string | null;
  version: number;
  created_at: string;
};

export type ExtractedField = {
  path: string;
  value: string | null;
  confidence: number | null;
  evidence: string | null;
  validation: "ok" | "invalid";
  applied: boolean;
};
export type MatchCandidate = {
  id: string;
  name: string;
  tin?: string | null;
  sku?: string;
  reason: string;
  score: number;
  party?: string;
  archived: boolean;
};
export type Extraction = {
  job: {
    id: string;
    status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    provider: string;
    model: string;
    mock: boolean;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
    finished_at: string | null;
  } | null;
  fields: ExtractedField[];
  counterparty_candidates: MatchCandidate[];
  line_matches: Record<string, MatchCandidate[]>;
};
export type AIStatus = { configured: boolean; provider: string | null; model: string | null; mock: boolean };
