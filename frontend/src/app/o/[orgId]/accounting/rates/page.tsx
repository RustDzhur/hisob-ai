"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { Banner, Button, ErrorState, Field, Skeleton } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type CbuImport, type Currency, type ExchangeRateRow, type Page } from "@/lib/api";
import { formatQuantity, normalizeMoneyInput } from "@/lib/money";
import { useSession } from "@/lib/session";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function RatesPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");
  const base = org?.base_currency ?? "";

  const [data, setData] = useState<Page<ExchangeRateRow> | null>(null);
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<CbuImport | null>(null);
  const [page, setPage] = useState(1);
  const [cur, setCur] = useState("");
  const [date, setDate] = useState(iso(new Date()));
  const [nominal, setNominal] = useState("1");
  const [rate, setRate] = useState("");
  const [importDate, setImportDate] = useState(iso(new Date()));
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api<Page<ExchangeRateRow>>(`/organizations/${orgId}/accounting/rates?page=${page}&page_size=25`));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [orgId, page]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);
  useEffect(() => {
    if (!canRead) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then((list) => {
      const foreign = list.filter((c) => c.enabled && !c.is_base);
      setCurrencies(foreign);
      setCur((v) => v || foreign[0]?.code || "");
    }).catch(() => setCurrencies([]));
  }, [canRead, orgId]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;
  if (error) return <ErrorState error={error} onRetry={load} />;

  async function save() {
    setBusy("save");
    setActionError(null);
    try {
      await api(`/organizations/${orgId}/accounting/rates`, { method: "PUT", body: { currency: cur, rate_date: date, nominal: Number(nominal), rate: normalizeMoneyInput(rate) } });
      setRate("");
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function importCbu() {
    setBusy("import");
    setActionError(null);
    setResult(null);
    try {
      setResult(await api<CbuImport>(`/organizations/${orgId}/accounting/rates/import-cbu`, { method: "POST", body: { rate_date: importDate } }));
      await load();
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / 25)) : 1;
  return (
    <div className="stack">
      <div className="page-head"><h1>{t.accounting.ratesTitle}</h1></div>
      <AccountingTabs />
      <p className="hint">{t.accounting.ratesNote}</p>
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      {result ? (
        <Banner kind="info">
          {fill(t.accounting.rateImported, { n: result.imported.length })}
          {result.skipped_existing.length ? ` · ${fill(t.accounting.rateSkipped, { n: result.skipped_existing.length })}` : ""}
          {result.not_enabled.length ? ` · ${fill(t.accounting.rateNotEnabled, { n: result.not_enabled.length })}` : ""}
        </Banner>
      ) : null}
      {canManage ? (
        <section className="card">
          <div className="toolbar">
            <select aria-label={t.accounting.rateCurrency} value={cur} onChange={(e) => setCur(e.target.value)}>
              {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
            </select>
            <label><span className="muted">{t.accounting.rateDate} </span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
            <Field id="rate-nominal" type="number" label={t.accounting.rateNominal} value={nominal} onChange={(e) => setNominal(e.target.value)} />
            <Field id="rate-value" inputMode="decimal" label={t.accounting.rateValue} value={rate} onChange={(e) => setRate(e.target.value)} />
            <Button loading={busy === "save"} disabled={!cur || !rate.trim()} onClick={() => void save()}>{t.accounting.rateSet}</Button>
          </div>
          {base === "UZS" ? (
            <div className="toolbar">
              <label><span className="muted">{t.accounting.rateImportDate} </span><input type="date" value={importDate} onChange={(e) => setImportDate(e.target.value)} /></label>
              <Button variant="secondary" loading={busy === "import"} onClick={() => void importCbu()}>{t.accounting.rateImport}</Button>
            </div>
          ) : null}
        </section>
      ) : null}
      {!data ? <Skeleton lines={4} /> : data.items.length === 0 ? <div className="card"><p className="muted">{t.accounting.emptyRates}</p></div> : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>{t.accounting.rateDate}</th><th>{t.accounting.rateCurrency}</th><th>{t.accounting.rateNominal}</th><th>{t.accounting.rateValue}</th><th>{t.accounting.rateSource}</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id}>
                    <td>{dateOnly(r.rate_date)}</td><td>{r.currency}</td><td>{r.nominal}</td>
                    <td className="num">{formatQuantity(r.rate, locale)} {base}</td>
                    <td>{t.accounting.rateSources[r.source] ?? r.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pages > 1 ? (
            <div className="toolbar">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</Button>
              <span>{page} / {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</Button>
            </div>
          ) : null}
        </section>
      )}
    </div>
  );
}
