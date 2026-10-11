"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BankTabs } from "@/components/BankTabs";
import { Banner, Button, ErrorState, Field, Skeleton } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import {
  api, toApiError, type ApiError, type BankAccount, type BankImportRow, type ImportProfile, type StatementHeaders, type StatementPreview,
} from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";

const DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d", "%m/%d/%Y"];
const DEFAULT_PROFILE: ImportProfile = {
  format: "csv", delimiter: ";", encoding: "utf-8", header_row: 1, sheet: null, date_column: "", date_format: "%d.%m.%Y", amount_mode: "signed", amount_column: "",
  incoming_column: null, outgoing_column: null, decimal_separator: ",", thousands_separator: " ", currency_column: null, description_column: null, reference_column: null,
  counterparty_name_column: null, counterparty_tin_column: null,
};

export default function BankAccountPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime, dateOnly } = useFormat();
  const { orgId, accountId } = useParams<{ orgId: string; accountId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("bank.read");
  const canManage = !!org?.permissions.includes("bank.manage");
  const canImport = !!org?.permissions.includes("bank.import");

  const [account, setAccount] = useState<BankAccount | null>(null);
  const [profile, setProfile] = useState<ImportProfile>(DEFAULT_PROFILE);
  const [imports, setImports] = useState<BankImportRow[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [sample, setSample] = useState<File | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [skipErrors, setSkipErrors] = useState(false);
  const [result, setResult] = useState<BankImportRow | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const a = await api<BankAccount>(`/organizations/${orgId}/bank/accounts/${accountId}`);
      setAccount(a);
      const p = await api<ImportProfile | null>(`/organizations/${orgId}/bank/accounts/${accountId}/profile`);
      if (p) {
        setProfile(p);
        setHeaders([p.date_column, p.amount_column, p.incoming_column, p.outgoing_column, p.currency_column, p.description_column, p.reference_column, p.counterparty_name_column, p.counterparty_tin_column].filter((x): x is string => !!x));
      }
      setImports(await api<BankImportRow[]>(`/organizations/${orgId}/bank/accounts/${accountId}/imports`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, accountId]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.bank.noAccessBody}</p></div>;
  if (error) return error.status === 404 ? <div className="card"><h1>{t.bank.notFound}</h1><Link href={`/o/${orgId}/bank/accounts`}>{t.bank.back}</Link></div> : <ErrorState error={error} onRetry={load} />;
  if (!account) return <Skeleton lines={6} />;

  const set = <K extends keyof ImportProfile>(k: K, v: ImportProfile[K]) => setProfile({ ...profile, [k]: v });
  const fileParams = () => JSON.stringify({ format: profile.format, delimiter: profile.delimiter, encoding: profile.encoding, header_row: profile.header_row, sheet: profile.sheet });

  async function readColumns() {
    if (!sample) return;
    setBusy("headers");
    setActionError(null);
    try {
      const fd = new FormData();
      fd.append("file", sample);
      fd.append("profile", fileParams());
      const r = await api<StatementHeaders>(`/organizations/${orgId}/bank/accounts/${accountId}/headers`, { method: "POST", body: fd });
      setHeaders(r.headers);
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function saveProfile() {
    setBusy("profile");
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/bank/accounts/${accountId}/profile`, { method: "PUT", body: profile });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function check() {
    if (!file) return;
    setBusy("preview");
    setActionError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      setPreview(await api<StatementPreview>(`/organizations/${orgId}/bank/accounts/${accountId}/preview`, { method: "POST", body: fd }));
    } catch (e) {
      setPreview(null);
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function doImport() {
    if (!file) return;
    setBusy("import");
    setActionError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (skipErrors) fd.append("skip_errors", "true");
      setResult(await api<BankImportRow>(`/organizations/${orgId}/bank/accounts/${accountId}/imports`, { method: "POST", body: fd }));
      setPreview(null);
      setFile(null);
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  const colSelect = (id: string, label: string, value: string | null, onChange: (v: string | null) => void, optional = true) => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} disabled={!canManage}>
        {optional ? <option value="">{t.bank.pNone}</option> : <option value="">—</option>}
        {headers.map((h) => <option key={h} value={h}>{h}</option>)}
      </select>
    </div>
  );

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/bank/accounts`}>← {t.bank.back}</Link></p>
          <h1 style={{ marginTop: 4 }}>{account.name} <span className="badge badge-info">{account.currency}</span></h1>
          <p className="muted" style={{ margin: 0 }}>{account.bank_name ? `${account.bank_name} · ` : ""}<code>{account.account_number}</code></p>
        </div>
        <Link className="btn btn-secondary" href={`/o/${orgId}/bank?account_id=${account.id}`}>{t.bank.showTxn}</Link>
      </div>
      <BankTabs />
      {actionError ? <Banner>{errorText(t, actionError)}{actionError.details.slice(0, 5).map((d, i) => <div key={i}>{d.code && t.bank.rowErrorCodes[d.code] ? `${t.bank.rowErrorCodes[d.code]}${d.params?.row_number ? ` (${d.params.row_number})` : ""}` : ""}</div>)}</Banner> : null}

      {canManage ? (
        <section className="card" aria-labelledby="bp-h">
          <h2 id="bp-h">{t.bank.profileTitle}</h2>
          <p className="hint">{t.bank.profileHint}</p>
          <div className="doc-grid">
            <div className="field">
              <label htmlFor="p-format">{t.bank.pFormat}</label>
              <select id="p-format" value={profile.format} onChange={(e) => set("format", e.target.value as "csv" | "xlsx")}><option value="csv">CSV</option><option value="xlsx">XLSX</option></select>
            </div>
            {profile.format === "csv" ? (
              <>
                <div className="field">
                  <label htmlFor="p-delim">{t.bank.pDelimiter}</label>
                  <select id="p-delim" value={profile.delimiter} onChange={(e) => set("delimiter", e.target.value)}>{[";", ",", "\t", "|"].map((d) => <option key={d} value={d}>{d === "\t" ? "TAB" : d}</option>)}</select>
                </div>
                <div className="field">
                  <label htmlFor="p-enc">{t.bank.pEncoding}</label>
                  <select id="p-enc" value={profile.encoding} onChange={(e) => set("encoding", e.target.value as "utf-8" | "cp1251")}><option value="utf-8">UTF-8</option><option value="cp1251">Windows-1251</option></select>
                </div>
              </>
            ) : <Field id="p-sheet" label={t.bank.pSheet} value={profile.sheet ?? ""} onChange={(e) => set("sheet", e.target.value || null)} />}
            <Field id="p-hrow" type="number" label={t.bank.pHeaderRow} value={String(profile.header_row)} onChange={(e) => set("header_row", Number(e.target.value) || 1)} />
          </div>
          <div className="toolbar">
            <label><span className="muted">{t.bank.sampleFile} </span><input type="file" accept=".csv,.xlsx" onChange={(e) => setSample(e.target.files?.[0] ?? null)} /></label>
            <Button variant="secondary" loading={busy === "headers"} disabled={!sample} onClick={() => void readColumns()}>{t.bank.readColumns}</Button>
          </div>
          <div className="doc-grid">
            {colSelect("p-date", t.bank.pDateColumn, profile.date_column, (v) => set("date_column", v ?? ""), false)}
            <div className="field">
              <label htmlFor="p-dfmt">{t.bank.pDateFormat}</label>
              <select id="p-dfmt" value={profile.date_format} onChange={(e) => set("date_format", e.target.value)}>{DATE_FORMATS.map((f) => <option key={f} value={f}>{f.replace("%d", "ДД").replace("%m", "ММ").replace("%Y", "ГГГГ")}</option>)}</select>
            </div>
            <div className="field">
              <label htmlFor="p-mode">{t.bank.pAmountMode}</label>
              <select id="p-mode" value={profile.amount_mode} onChange={(e) => set("amount_mode", e.target.value as "signed" | "split")}>{Object.entries(t.bank.pAmountModes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            </div>
            {profile.amount_mode === "signed"
              ? colSelect("p-amount", t.bank.pAmountColumn, profile.amount_column, (v) => set("amount_column", v), false)
              : (<>{colSelect("p-in", t.bank.pIncoming, profile.incoming_column, (v) => set("incoming_column", v), false)}{colSelect("p-out", t.bank.pOutgoing, profile.outgoing_column, (v) => set("outgoing_column", v), false)}</>)}
            <div className="field">
              <label htmlFor="p-dec">{t.bank.pDecimal}</label>
              <select id="p-dec" value={profile.decimal_separator} onChange={(e) => set("decimal_separator", e.target.value as "," | ".")}><option value=",">,</option><option value=".">.</option></select>
            </div>
            <div className="field">
              <label htmlFor="p-thou">{t.bank.pThousands}</label>
              <select id="p-thou" value={profile.thousands_separator} onChange={(e) => set("thousands_separator", e.target.value)}>
                <option value=" ">␣</option><option value=",">,</option><option value=".">.</option><option value="'">&apos;</option><option value="">{t.bank.pThousandsNone}</option>
              </select>
            </div>
            {colSelect("p-cur", t.bank.pCurrencyColumn, profile.currency_column, (v) => set("currency_column", v))}
            {colSelect("p-desc", t.bank.pDescription, profile.description_column, (v) => set("description_column", v))}
            {colSelect("p-ref", t.bank.pReference, profile.reference_column, (v) => set("reference_column", v))}
            {colSelect("p-cpn", t.bank.pCpName, profile.counterparty_name_column, (v) => set("counterparty_name_column", v))}
            {colSelect("p-cpt", t.bank.pCpTin, profile.counterparty_tin_column, (v) => set("counterparty_tin_column", v))}
          </div>
          <div className="drawer-actions" style={{ alignItems: "center" }}>
            <Button loading={busy === "profile"} disabled={!profile.date_column || (profile.amount_mode === "signed" ? !profile.amount_column : !(profile.incoming_column && profile.outgoing_column))} onClick={() => void saveProfile()}>{t.bank.saveProfile}</Button>
            {saved ? <span className="hint" role="status">{t.bank.profileSaved}</span> : null}
          </div>
        </section>
      ) : null}

      {canImport ? (
        <section className="card" aria-labelledby="bi-h">
          <h2 id="bi-h">{t.bank.importTitle}</h2>
          {!account.has_profile ? <p className="hint">{t.bank.noProfile}</p> : null}
          <div className="toolbar">
            <label><span className="muted">{t.bank.importFile} </span><input type="file" accept=".csv,.xlsx" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null); }} /></label>
            <Button variant="secondary" loading={busy === "preview"} disabled={!file || !account.has_profile} onClick={() => void check()}>{t.bank.check}</Button>
          </div>
          {preview ? (
            <>
              <p data-testid="preview-summary"><strong>{fill(t.bank.previewTotal, { total: preview.total, would: preview.would_import, dups: preview.duplicates, errs: preview.errors_count })}</strong></p>
              {preview.already_imported ? <Banner kind="info">{t.bank.previewAlready}</Banner> : null}
              {preview.errors.length ? (
                <div className="issue issue-warning">
                  <strong>{t.bank.rowErrors}:</strong>
                  <ul>{preview.errors.map((e) => <li key={e.row_number}>#{e.row_number}: {t.bank.rowErrorCodes[e.code] ?? e.code}</li>)}</ul>
                  <label className="ack-row"><input type="checkbox" checked={skipErrors} onChange={(e) => setSkipErrors(e.target.checked)} /><span>{t.bank.skipErrors}</span></label>
                </div>
              ) : null}
              <div className="table-wrap">
                <table className="lines-table">
                  <thead><tr><th>#</th><th>{t.bank.colDate}</th><th>{t.bank.colAmount}</th><th>{t.bank.colDescription}</th><th>{t.bank.colCounterparty}</th></tr></thead>
                  <tbody>
                    {preview.sample.slice(0, 20).map((r) => (
                      <tr key={r.row_number} style={r.duplicate ? { opacity: 0.55 } : undefined}>
                        <td>{r.row_number}</td><td>{dateOnly(r.txn_date)}</td><td className="num">{formatMoney(r.amount, account.currency, locale)}</td>
                        <td>{r.description ?? "—"}</td><td>{r.counterparty_name ?? r.counterparty_tin ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="drawer-actions">
                <Button loading={busy === "import"} disabled={preview.would_import === 0 || preview.already_imported || (preview.errors_count > 0 && !skipErrors)} onClick={() => void doImport()}>{t.bank.doImport}</Button>
              </div>
            </>
          ) : null}
          {result ? (
            <Banner kind="info">
              <span data-testid="import-result">{fill(t.bank.imported, { n: result.rows_imported })}</span>
              {result.rows_duplicate ? ` · ${fill(t.bank.importedDup, { n: result.rows_duplicate })}` : ""}
              {result.rows_error ? ` · ${fill(t.bank.importedErr, { n: result.rows_error })}` : ""}
            </Banner>
          ) : null}
        </section>
      ) : null}

      <section className="card" aria-labelledby="bh-h">
        <h2 id="bh-h">{t.bank.importsHistory}</h2>
        {imports.length === 0 ? <p className="muted">—</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t.bank.colDate}</th><th>{t.bank.colFile}</th><th>{t.bank.colRows}</th><th>{t.bank.colImported}</th><th>{t.bank.colDup}</th><th>{t.bank.colErr}</th><th /></tr></thead>
              <tbody>
                {imports.map((i) => (
                  <tr key={i.id}>
                    <td>{dateTime(i.created_at)}</td><td>{i.filename}</td><td>{i.rows_total}</td><td>{i.rows_imported}</td><td>{i.rows_duplicate}</td><td>{i.rows_error}</td>
                    <td><Link href={`/o/${orgId}/bank?import_id=${i.id}`}>{t.bank.showTxn}</Link></td>
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
