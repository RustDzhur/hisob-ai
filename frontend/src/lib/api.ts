export type FieldError = { field: string; message: string };

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
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = readCookie("hisob_csrf");
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте подключение и повторите.");
  }
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(
      res.status,
      data?.code ?? "http_error",
      data?.message ?? "Не удалось выполнить запрос.",
      data?.details ?? [],
      data?.correlation_id ?? res.headers.get("x-correlation-id"),
    );
  }
  return data as T;
}

export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  return Object.fromEntries(err.details.map((d) => [d.field, d.message]));
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
