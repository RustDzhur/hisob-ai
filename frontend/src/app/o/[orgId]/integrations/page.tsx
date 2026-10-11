"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type IntegrationProvider, type IntegrationRun } from "@/lib/api";
import { useSession } from "@/lib/session";
import { newIdempotencyKey } from "@/lib/id";

export default function IntegrationsPage() {
  const t = useT();
  const { dateTime } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("integrations.read");
  const canManage = !!org?.permissions.includes("integrations.manage");
  const [providers, setProviders] = useState<IntegrationProvider[] | null>(null);
  const [runs, setRuns] = useState<IntegrationRun[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const base = `/organizations/${orgId}/integrations`;

  const load = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([api<IntegrationProvider[]>(base), api<IntegrationRun[]>(`${base}/runs`)]);
      setProviders(p);
      setRuns(r);
    } catch (e) {
      setError(toApiError(e));
    }
  }, [base]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  async function act(key: string, path: string, body?: unknown) {
    setBusy(key);
    setError(null);
    try {
      await api(`${base}${path}`, { method: "POST", body });
      await load();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.integrations.noAccessBody}</p></div>;

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.integrations.title}</h1></div>
      <p className="hint">{t.integrations.intro}</p>
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      {providers === null ? <Skeleton /> : (
        <div className="stack" data-testid="providers">
          {providers.map((p) => (
            <section key={p.code} className="card" data-testid={`provider-${p.code}`}>
              <h2>
                {p.title}{" "}
                <span className={`badge ${p.status === "AVAILABLE" ? "badge-ok" : p.status === "MOCK" ? "badge-warn" : "badge-muted"}`}>{t.integrations.statuses[p.status]}</span>
              </h2>
              {p.status === "NOT_CONFIGURED" ? <p className="muted">{t.integrations.notConfiguredNote}</p> : null}
              {p.status === "MOCK" ? <p className="muted">{t.integrations.mockNote}</p> : null}
              {p.status === "AVAILABLE" ? (
                <>
                  <p className="muted">{t.integrations.fileNote}</p>
                  <div className="toolbar">
                    <Link className="btn btn-secondary" href={`/o/${orgId}/bank/accounts`}>{t.integrations.openBank}</Link>
                    <Link className="btn btn-secondary" href={`/o/${orgId}/documents`}>{t.integrations.openDocuments}</Link>
                  </div>
                </>
              ) : null}
              {p.can_sync ? (
                <>
                  <p>
                    {p.enabled ? t.integrations.enabled : t.integrations.disabledState} · {t.integrations.lastSync}: {p.last_sync_at ? dateTime(p.last_sync_at) : t.integrations.never}
                    {p.sync_cursor !== null ? ` · ${t.integrations.cursor}: ${p.sync_cursor}` : ""}
                  </p>
                  {canManage ? (
                    <div className="toolbar">
                      {p.enabled ? (
                        <>
                          <Button loading={busy === `s${p.code}`} onClick={() => void act(`s${p.code}`, `/${p.code}/sync`, { request_id: newIdempotencyKey() })}>{t.integrations.sync}</Button>
                          <Button variant="secondary" loading={busy === `d${p.code}`} onClick={() => void act(`d${p.code}`, `/${p.code}/disable`)}>{t.integrations.disable}</Button>
                        </>
                      ) : (
                        <Button loading={busy === `e${p.code}`} onClick={() => void act(`e${p.code}`, `/${p.code}/enable`)}>{t.integrations.enable}</Button>
                      )}
                    </div>
                  ) : null}
                </>
              ) : null}
            </section>
          ))}
        </div>
      )}
      <section className="card" aria-labelledby="runs-h">
        <h2 id="runs-h">{t.integrations.runsTitle}</h2>
        {runs.length === 0 ? <p className="muted">{t.integrations.noRuns}</p> : (
          <div className="table-wrap">
            <table className="lines-table" data-testid="runs">
              <thead><tr><th>{t.integrations.colWhen}</th><th>{t.integrations.colProvider}</th><th>{t.integrations.colMode}</th><th>{t.integrations.colStatus}</th><th className="num">{t.integrations.colRecords}</th><th>{t.integrations.colCursor}</th><th>{t.integrations.colBy}</th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td>{dateTime(r.created_at)}</td>
                    <td>{r.provider}</td>
                    <td>{r.mode}</td>
                    <td>{t.integrations.runStatuses[r.status]}{r.error_code ? ` (${t.integrations.runErrors[r.error_code] ?? r.error_code})` : ""}</td>
                    <td className="num">{r.records_count}</td>
                    <td>{r.cursor_before ?? "—"} → {r.cursor_after ?? "—"}</td>
                    <td>{r.created_by_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
