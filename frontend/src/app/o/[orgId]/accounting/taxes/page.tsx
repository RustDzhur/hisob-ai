"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { Banner, Button, ErrorState, Field, Skeleton, TextArea } from "@/components/ui";
import { errorText, fieldErrorMap } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type TaxCodeRow } from "@/lib/api";
import { normalizeMoneyInput } from "@/lib/money";
import { useSession } from "@/lib/session";

export default function TaxesPage() {
  const t = useT();
  const { dateTime } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");
  const [rows, setRows] = useState<TaxCodeRow[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [rate, setRate] = useState("");
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<TaxCodeRow | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(await api<TaxCodeRow[]>(`/organizations/${orgId}/accounting/tax-codes`));
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

  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setActionError(null);
    setErrs({});
    try {
      await fn();
      await load();
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setErrs(fe);
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }
  const post = (path: string, body?: unknown) => api(`/organizations/${orgId}/accounting/tax-codes${path}`, { method: "POST", body });

  const submit = () => run("save", async () => {
    const body = { name, rate: normalizeMoneyInput(rate), source_url: url || null, source_note: note || null };
    if (editing) await api(`/organizations/${orgId}/accounting/tax-codes/${editing.id}`, { method: "PATCH", body });
    else await post("", { code, ...body });
    setEditing(null); setCode(""); setName(""); setRate(""); setUrl(""); setNote("");
  });
  const edit = (r: TaxCodeRow) => { setEditing(r); setCode(r.code); setName(r.name); setRate(r.rate); setUrl(r.source_url ?? ""); setNote(r.source_note ?? ""); };

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.accounting.taxesTitle}</h1>
        {canManage ? <Button variant="secondary" loading={busy === "suggest"} onClick={() => void run("suggest", () => post("/suggest-reference").then(() => undefined))} title={t.accounting.taxSuggestHint}>{t.accounting.taxSuggest}</Button> : null}
      </div>
      <AccountingTabs />
      <p className="hint">{t.accounting.taxNote}</p>
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {canManage ? (
        <section className="card">
          <h2>{editing ? t.accounting.taxEdit : t.accounting.taxAdd}</h2>
          <div className="doc-grid">
            <Field id="tx-code" label={t.accounting.taxCode} value={code} maxLength={32} disabled={!!editing} onChange={(e) => setCode(e.target.value)} error={errs.code} />
            <Field id="tx-name" label={t.accounting.taxName} value={name} maxLength={300} onChange={(e) => setName(e.target.value)} error={errs.name} />
            <Field id="tx-rate" inputMode="decimal" label={t.accounting.taxRate} value={rate} onChange={(e) => setRate(e.target.value)} error={errs.rate} />
            <Field id="tx-url" label={t.accounting.taxSourceUrl} value={url} maxLength={500} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <TextArea id="tx-note" label={t.accounting.taxSourceNote} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
          <div className="drawer-actions">
            <Button loading={busy === "save"} disabled={!code.trim() || !name.trim() || !rate.trim()} onClick={() => void submit()}>{t.accounting.save}</Button>
            {editing ? <Button variant="secondary" onClick={() => { setEditing(null); setCode(""); setName(""); setRate(""); setUrl(""); setNote(""); }}>{t.accounting.cancel}</Button> : null}
          </div>
        </section>
      ) : null}
      {!rows ? <Skeleton lines={4} /> : rows.length === 0 ? <div className="card"><p className="muted">{t.accounting.emptyTaxes}</p></div> : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t.accounting.taxCode}</th><th>{t.accounting.taxName}</th><th>{t.accounting.taxRate}</th><th>{t.accounting.colStatus}</th><th>{t.accounting.taxSourceNote}</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.code}{r.is_default ? <> <span className="badge badge-info">{t.accounting.taxIsDefault}</span></> : null}</td>
                    <td>{r.name}</td>
                    <td className="num">{r.rate}</td>
                    <td>
                      <span className={`badge ${r.status === "CONFIRMED" ? "badge-ok" : "badge-warn"}`}>{t.accounting.taxStatuses[r.status]}</span>
                      {r.confirmed_by_name ? <div className="hint">{t.accounting.taxConfirmedBy}: {r.confirmed_by_name}{r.confirmed_at ? ` · ${dateTime(r.confirmed_at)}` : ""}</div> : null}
                    </td>
                    <td style={{ maxWidth: 360 }}>
                      {r.source_note ? <div className="hint">{r.source_note}</div> : null}
                      {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer noopener">{r.source_url}</a> : null}
                    </td>
                    <td>
                      {canManage ? (
                        <span className="row-actions">
                          {r.status === "DRAFT" ? <Button variant="secondary" className="btn-sm" onClick={() => edit(r)}>{t.accounting.taxEdit}</Button> : null}
                          {r.status === "DRAFT" ? <Button variant="secondary" className="btn-sm" onClick={() => void run(`c${r.id}`, () => post(`/${r.id}/confirm`).then(() => undefined))}>{t.accounting.taxConfirm}</Button> : null}
                          {r.status === "CONFIRMED" && !r.is_default ? <Button variant="secondary" className="btn-sm" onClick={() => void run(`d${r.id}`, () => post(`/${r.id}/set-default`).then(() => undefined))}>{t.accounting.taxDefault}</Button> : null}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
