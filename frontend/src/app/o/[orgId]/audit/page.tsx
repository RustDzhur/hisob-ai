"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, ErrorState, Skeleton } from "@/components/ui";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, ApiError, type AuditPage, toApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

const PAGE_SIZE = 25;

export default function AuditPageView() {
  const t = useT();
  const { dateTime: formatDateTime } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state } = useSession();
  const org = state.status === "ready" ? state.orgs.find((o) => o.id === orgId) : undefined;
  const allowed = !!org?.permissions.includes("audit.read");
  const timeZone = org?.timezone;

  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [actions, setActions] = useState<string[]>([]);
  const [data, setData] = useState<AuditPage | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  const rangeInvalid = !!from && !!to && from > to;

  const load = useCallback(async () => {
    if (rangeInvalid) return;
    setLoading(true);
    setError(null);
    const q = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (action) q.set("action", action);
    if (from) q.set("date_from", from);
    if (to) q.set("date_to", to);
    try {
      setData(await api<AuditPage>(`/organizations/${orgId}/audit?${q}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, page, action, from, to, rangeInvalid]);

  useEffect(() => {
    if (allowed) void load();
  }, [allowed, load]);

  useEffect(() => {
    if (allowed) api<string[]>(`/organizations/${orgId}/audit/actions`).then(setActions).catch(() => setActions([]));
  }, [allowed, orgId]);

  if (state.status !== "ready") return <Skeleton />;
  if (!allowed) {
    return (
      <div className="card">
        <h1>{t.audit.noAccessTitle}</h1>
        <p className="muted">{t.audit.noAccessBody}</p>
      </div>
    );
  }

  const label = (a: string) => t.audit.actions[a] ?? a;
  const fmt = (iso: string) => formatDateTime(iso, timeZone);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const reset = () => { setAction(""); setFrom(""); setTo(""); setPage(1); };

  return (
    <div className="stack">
      <div>
        <h1>{t.audit.title}</h1>
        <p className="muted">{t.audit.lead}</p>
      </div>

      <section className="card">
        <div className="filters">
          <div className="field">
            <label htmlFor="f-action">{t.audit.action}</label>
            <select id="f-action" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
              <option value="">{t.audit.allActions}</option>
              {actions.map((a) => <option key={a} value={a}>{label(a)}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-from">{t.audit.from}</label>
            <input id="f-from" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
          </div>
          <div className="field">
            <label htmlFor="f-to">{t.audit.to}</label>
            <input id="f-to" type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} />
          </div>
          <div className="field">
            <Button variant="secondary" onClick={reset}>{t.audit.reset}</Button>
          </div>
        </div>
        {rangeInvalid ? <p className="field-error" role="alert">{t.audit.badRange}</p> : null}
      </section>

      {error ? <ErrorState error={error} onRetry={load} /> : null}
      {loading && !data ? <Skeleton lines={6} /> : null}

      {data && !error ? (
        <section className="card" aria-busy={loading}>
          {data.items.length === 0 ? (
            <p className="muted">{t.audit.empty}</p>
          ) : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>{t.audit.colTime}</th>
                    <th>{t.audit.colAction}</th>
                    <th>{t.audit.colActor}</th>
                    <th>{t.audit.colObject}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((e) => (
                    <tr key={e.id}>
                      <td data-label={t.audit.colTime}>{fmt(e.occurred_at)}</td>
                      <td data-label={t.audit.colAction}>
                        {label(e.action)}
                        <div className="cell-sub"><code>{e.action}</code></div>
                        {e.before || e.after ? (
                          <details>
                            <summary>{t.audit.details}</summary>
                            <pre className="json">{JSON.stringify({ before: e.before, after: e.after }, null, 2)}</pre>
                            {e.correlation_id ? <div className="cell-sub">correlation_id: <code>{e.correlation_id}</code></div> : null}
                            {e.ip ? <div className="cell-sub">IP: {e.ip}</div> : null}
                          </details>
                        ) : null}
                      </td>
                      <td data-label={t.audit.colActor}>
                        {e.actor ? <>{e.actor.full_name}<div className="cell-sub">{e.actor.email}</div></> : <span className="muted">{t.audit.system}</span>}
                      </td>
                      <td data-label={t.audit.colObject}>{e.object_type ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pager">
            <span className="muted">{t.audit.total}: {data.total}</span>
            <div className="row-actions">
              <Button variant="secondary" className="btn-sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>{t.audit.prev}</Button>
              <span aria-live="polite">{t.audit.page} {data.page} {t.audit.of} {pages}</span>
              <Button variant="secondary" className="btn-sm" disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)}>{t.audit.next}</Button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
