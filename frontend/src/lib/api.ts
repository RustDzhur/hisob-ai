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

// ---------------- склад (срез 4.1) ----------------
export type NameRef = { id: string; name: string };
export type UnitRef = { code: string; name_uz: string | null; name_ru: string | null; name_en: string | null };
export type StockBalance = {
  warehouse: NameRef; product_id: string; sku: string; name: string; unit: UnitRef; quantity: string;
  reserved: string; available: string; min_quantity: string | null; low: boolean;
};
export type StockMovement = {
  id: string; operation_id: string; kind: string; reference: string | null; warehouse: NameRef; product_id: string;
  sku: string; name: string; unit: UnitRef; quantity_delta: string; op_date: string; occurred_at: string;
};
export type StockLine = { position: number; product_id: string; sku: string; name: string; unit: UnitRef; quantity: string };
export type StockOperation = {
  id: string; kind: "receipt" | "issue" | "transfer" | "supplier_return" | "customer_return" | "count_adjustment"; status: "DRAFT" | "POSTED"; op_date: string; warehouse: NameRef;
  to_warehouse: NameRef | null; reference: string | null; notes: string | null; version: number; lines: StockLine[];
  source_document: { id: string; number: string | null; doc_type: string } | null; reverses_id: string | null; reversed_by_id: string | null;
  created_by_name: string | null; posted_by_name: string | null; posted_at: string | null; created_at: string; updated_at: string;
};
export type StockOperationItem = {
  id: string; kind: string; status: string; op_date: string; warehouse_name: string; to_warehouse_name: string | null;
  reference: string | null; lines_count: number; is_reversal: boolean; reversed: boolean; created_at: string;
};
export type Reconciliation = {
  ok: boolean; checked_pairs: number;
  mismatches: { warehouse: NameRef; product_id: string; sku: string; name: string; balance: string; movements_sum: string }[];
};

export type StockCountLine = {
  product_id: string; sku: string; name: string; unit: UnitRef; expected_quantity: string; counted_quantity: string | null; applied_delta: string | null;
};
export type StockCount = {
  id: string; status: "IN_PROGRESS" | "POSTED"; warehouse: NameRef; notes: string | null; version: number; operation_id: string | null;
  lines: StockCountLine[]; created_by_name: string | null; posted_by_name: string | null; posted_at: string | null; created_at: string; updated_at: string;
};
export type StockCountItem = { id: string; status: string; warehouse_name: string; lines_count: number; counted_count: number; created_at: string; posted_at: string | null };
export type StockReservation = {
  id: string; warehouse: NameRef; product_id: string; sku: string; name: string; unit: UnitRef; quantity: string; reason: string | null;
  status: "ACTIVE" | "RELEASED"; created_by_name: string | null; created_at: string; released_at: string | null;
};

// ---------------- бухгалтерия (срез 5.1) ----------------
export type Account = {
  id: string; code: string; name: string; parent_id: string | null; parent_code: string | null; allow_posting: boolean;
  notes: string | null; archived: boolean; has_entries: boolean; created_at: string; updated_at: string;
};
export type AccountingPeriod = { id: string; start_date: string; end_date: string; status: "OPEN" | "CLOSED"; closed_at: string | null; entries_count: number };
export type JournalLine = { position: number; account_id: string; account_code: string; account_name: string; debit: string; credit: string; memo: string | null };
export type JournalEntry = {
  id: string; number: number; entry_date: string; currency: string; description: string; reverses_id: string | null;
  reversed_by_id: string | null; period_id: string; created_by_name: string | null; created_at: string; total: string; lines: JournalLine[];
  source_document: { id: string; number: string | null; doc_type: string } | null; rule: { id: string; key: string; version: number; name: string } | null;
};
export type JournalEntryItem = { id: string; number: number; entry_date: string; currency: string; description: string; total: string; is_reversal: boolean; reversed: boolean };
export type TrialRow = {
  account_id: string; code: string; name: string; opening_debit: string; opening_credit: string; turnover_debit: string;
  turnover_credit: string; closing_debit: string; closing_credit: string;
};
export type TrialBalance = { currency: string; date_from: string; date_to: string; rows: TrialRow[]; totals: TrialRow | null; balanced: boolean };
export type LedgerRow = { entry_id: string; number: number; entry_date: string; description: string; memo: string | null; debit: string; credit: string; balance_debit: string; balance_credit: string };
export type Ledger = { account_id: string; code: string; name: string; currency: string; date_from: string; date_to: string; opening_debit: string; opening_credit: string; rows: LedgerRow[]; total: number };

// ---------------- правила проводок (срез 5.2) ----------------
export type RuleLine = { position: number; account_id: string; account_code: string; account_name: string; side: "debit" | "credit"; amount_source: "total_amount" | "tax_total" | "total_minus_tax"; memo: string | null };
export type PostingRule = {
  id: string; rule_key: string; version: number; name: string; description: string | null; doc_type: string; status: "DRAFT" | "APPROVED" | "RETIRED";
  legal_basis: string | null; effective_from: string | null; effective_to: string | null; approved_by_name: string | null; approved_at: string | null; created_at: string; lines: RuleLine[];
};
export type PostingRuleItem = { id: string; rule_key: string; version: number; name: string; doc_type: string; status: string; effective_from: string | null; effective_to: string | null; approved_at: string | null };
export type Proposal = {
  rule: { id: string; key: string; version: number; name: string }; legal_basis: string | null; entry_date: string | null; currency: string | null; description: string;
  lines: { account_id: string; account_code: string; account_name: string; debit: string; credit: string; memo: string | null }[]; problems: string[]; posted_entry_id: string | null;
};
