"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BankTabs } from "@/components/BankTabs";
import { Banner, Button, Drawer, ErrorState, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type BankAccount, type Currency, type Page } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function BankAccountsPage() {
  const t = useT();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("bank.read");
  const canManage = !!org?.permissions.includes("bank.manage");
  const [data, setData] = useState<Page<BankAccount> | null>(null);
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [form, setForm] = useState({ name: "", bank_name: "", account_number: "", currency: "", notes: "" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api<Page<BankAccount>>(`/organizations/${orgId}/bank/accounts?page_size=100&status=all`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);
  useEffect(() => {
    if (!canRead) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then((l) => {
      const enabled = l.filter((c) => c.enabled);
      setCurrencies(enabled);
      setForm((f) => ({ ...f, currency: f.currency || enabled.find((c) => c.is_base)?.code || "" }));
    }).catch(() => setCurrencies([]));
  }, [canRead, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.bank.noAccessBody}</p></div>;
  if (error) return <ErrorState error={error} onRetry={load} />;

  async function create() {
    setBusy(true);
    setErrs({});
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/bank/accounts`, { method: "POST", body: { name: form.name, bank_name: form.bank_name || null, account_number: form.account_number, currency: form.currency, notes: form.notes || null } });
      setDrawer(false);
      setForm({ ...form, name: "", bank_name: "", account_number: "", notes: "" });
      await load();
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setErrs(fe);
      setActionError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  async function setArchived(a: BankAccount, archived: boolean) {
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/bank/accounts/${a.id}/${archived ? "archive" : "restore"}`, { method: "POST" });
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.bank.accountsTitle}</h1>
        {canManage ? <Button onClick={() => { setErrs({}); setActionError(null); setDrawer(true); }}>{t.bank.newAccount}</Button> : null}
      </div>
      <BankTabs />
      <p className="hint">{t.bank.note}</p>
      {actionError && !drawer ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {!data ? <Skeleton lines={4} /> : data.items.length === 0 ? <div className="card"><p className="muted">{t.bank.emptyAccounts}</p></div> : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t.bank.colName}</th><th>{t.bank.colBank}</th><th>{t.bank.colNumber}</th><th>{t.bank.colCurrency}</th><th>{t.bank.colTxn}</th><th /></tr></thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td><Link href={`/o/${orgId}/bank/accounts/${a.id}`}><strong>{a.name}</strong></Link>{a.archived ? <> <span className="badge badge-muted">{t.bank.archived}</span></> : null}{!a.has_profile ? <div className="hint">{t.bank.noProfile}</div> : null}</td>
                    <td>{a.bank_name ?? "—"}</td><td><code>{a.account_number}</code></td><td>{a.currency}</td><td>{a.transactions_count}</td>
                    <td>
                      <span className="row-actions">
                        <Link className="btn btn-secondary btn-sm" href={`/o/${orgId}/bank/accounts/${a.id}`}>{t.bank.open}</Link>
                        {canManage ? (a.archived ? <Button variant="secondary" className="btn-sm" onClick={() => void setArchived(a, false)}>{t.bank.restore}</Button> : <Button variant="secondary" className="btn-sm" onClick={() => void setArchived(a, true)}>{t.bank.archive}</Button>) : null}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <Drawer open={drawer} title={t.bank.newAccount} onClose={() => !busy && setDrawer(false)}>
        <Field id="ba-name" label={t.bank.fName} value={form.name} maxLength={200} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errs.name} />
        <Field id="ba-bank" label={t.bank.fBank} value={form.bank_name} maxLength={200} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} />
        <Field id="ba-number" label={t.bank.fNumber} value={form.account_number} maxLength={40} onChange={(e) => setForm({ ...form, account_number: e.target.value })} error={errs.account_number} />
        <div className="field">
          <label htmlFor="ba-cur">{t.bank.fCurrency}</label>
          <select id="ba-cur" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
            {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
          </select>
          {errs.currency ? <p className="field-error" role="alert">{errs.currency}</p> : null}
        </div>
        <TextArea id="ba-notes" label={t.bank.fNotes} value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
        <div className="drawer-actions">
          <Button onClick={() => void create()} loading={busy} disabled={!form.name.trim() || !form.account_number.trim() || !form.currency}>{t.bank.save}</Button>
          <Button variant="secondary" onClick={() => setDrawer(false)} disabled={busy}>{t.bank.cancel}</Button>
        </div>
      </Drawer>
    </div>
  );
}
