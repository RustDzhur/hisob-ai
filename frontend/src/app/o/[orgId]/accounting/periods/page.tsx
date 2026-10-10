"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { Banner, Button, ConfirmDialog, ErrorState, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type AccountingPeriod, type ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function PeriodsPage() {
  const t = useT();
  const { dateOnly, dateTime } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");
  const [periods, setPeriods] = useState<AccountingPeriod[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [target, setTarget] = useState<{ p: AccountingPeriod; close: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const now = new Date();
  const [year, setYear] = useState(String(now.getFullYear()));
  const [month, setMonth] = useState(String(now.getMonth() + 1));

  const load = useCallback(async () => {
    setError(null);
    try {
      setPeriods(await api<AccountingPeriod[]>(`/organizations/${orgId}/accounting/periods`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;
  if (error) return <ErrorState error={error} onRetry={load} />;

  async function change() {
    if (!target) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/accounting/periods/${target.p.id}/${target.close ? "close" : "reopen"}`, { method: "POST" });
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setTarget(null);
      setBusy(false);
    }
  }

  async function create() {
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/accounting/periods`, { method: "POST", body: { year: Number(year), month: Number(month) } });
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    }
  }

  return (
    <div className="stack">
      <div className="page-head"><h1>{t.accounting.periodsTitle}</h1></div>
      <AccountingTabs />
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {canManage ? (
        <section className="card toolbar">
          <label>{t.accounting.year} <input type="number" min={2000} max={2100} value={year} onChange={(e) => setYear(e.target.value)} /></label>
          <label>{t.accounting.month} <input type="number" min={1} max={12} value={month} onChange={(e) => setMonth(e.target.value)} /></label>
          <Button variant="secondary" onClick={() => void create()}>{t.accounting.createPeriod}</Button>
        </section>
      ) : null}
      {!periods ? <Skeleton lines={4} /> : periods.length === 0 ? <div className="card"><p className="muted">{t.accounting.emptyPeriods}</p></div> : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t.accounting.colPeriod}</th><th>{t.accounting.colEntries}</th><th>{t.accounting.colStatus}</th><th /></tr></thead>
              <tbody>
                {periods.map((p) => (
                  <tr key={p.id}>
                    <td>{dateOnly(p.start_date)} — {dateOnly(p.end_date)}</td>
                    <td>{p.entries_count}</td>
                    <td>
                      <span className={`badge ${p.status === "OPEN" ? "badge-ok" : "badge-muted"}`}>{p.status === "OPEN" ? t.accounting.open : t.accounting.closed}</span>
                      {p.closed_at ? <span className="hint"> {dateTime(p.closed_at)}</span> : null}
                    </td>
                    <td>
                      {canManage ? (
                        <Button variant="secondary" className="btn-sm" onClick={() => setTarget({ p, close: p.status === "OPEN" })}>
                          {p.status === "OPEN" ? t.accounting.closePeriod : t.accounting.reopenPeriod}
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <ConfirmDialog open={!!target} title={target?.close ? t.accounting.closeTitle : t.accounting.reopenTitle}
        body={target?.close ? t.accounting.closeBody : t.accounting.reopenBody}
        confirmLabel={target?.close ? t.accounting.closePeriod : t.accounting.reopenPeriod} busy={busy}
        onCancel={() => setTarget(null)} onConfirm={() => void change()} />
    </div>
  );
}
