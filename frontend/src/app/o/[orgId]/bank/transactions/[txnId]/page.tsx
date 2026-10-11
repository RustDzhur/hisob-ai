"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BankTabs } from "@/components/BankTabs";
import { MatchReasons } from "@/components/MatchReasons";
import { Banner, Button, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type BankTransactionDetail, type DocBrief, type DocCandidate, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";

type DocFound = { id: string; number: string | null; total_amount: string | null; currency: string | null };

export default function BankTransactionPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly, dateTime } = useFormat();
  const { orgId, txnId } = useParams<{ orgId: string; txnId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("bank.read");
  const canReconcile = !!org?.permissions.includes("bank.reconcile");

  const [txn, setTxn] = useState<BankTransactionDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [found, setFound] = useState<DocFound[]>([]);
  const [manual, setManual] = useState<{ doc: DocFound; amount: string }[]>([]);
  const [reason, setReason] = useState("");
  const base = `/organizations/${orgId}/bank`;

  const load = useCallback(async () => {
    try {
      const d = await api<BankTransactionDetail>(`${base}/transactions/${txnId}`);
      setTxn(d);
      setAmounts(Object.fromEntries(d.candidates.map((c) => [c.document.id, c.suggested_amount])));
    } catch (e) {
      setError(toApiError(e));
    }
  }, [base, txnId]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  async function act(key: string, path: string, body?: unknown) {
    setBusy(key);
    setError(null);
    try {
      const d = await api<BankTransactionDetail>(`${base}${path}`, { method: "POST", body });
      setTxn(d);
      setAmounts(Object.fromEntries(d.candidates.map((c) => [c.document.id, c.suggested_amount])));
      setManual([]);
      setFound([]);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function runSearch() {
    try {
      const qs = new URLSearchParams({ q: search, status: "approved", page_size: "10" });
      const p = await api<Page<DocFound>>(`/organizations/${orgId}/documents?${qs}`);
      setFound(p.items);
    } catch (e) {
      setError(toApiError(e));
    }
  }

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.bank.noAccessBody}</p></div>;
  if (!txn) return error ? <Banner>{errorText(t, error)}</Banner> : <Skeleton />;

  const active = txn.matches.filter((m) => m.status === "CONFIRMED");
  const history = txn.matches.filter((m) => m.status !== "CONFIRMED");
  const cur = txn.currency;
  const docLabel = (d: DocBrief | DocCandidate["document"]) => `${d.number ?? "—"}${d.counterparty_name ? ` · ${d.counterparty_name}` : ""}`;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.bank.txnDetail}</h1>
        <Link className="btn" href={`/o/${orgId}/bank`}>{t.bank.backToTxn}</Link>
      </div>
      <BankTabs />
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      <section className="card" data-testid="txn-summary">
        <dl className="kv">
          <dt>{t.bank.colDate}</dt><dd>{dateOnly(txn.txn_date)}</dd>
          <dt>{t.bank.colAccount}</dt><dd>{txn.bank_account.name}</dd>
          <dt>{t.bank.colAmount}</dt><dd className="num">{formatMoney(txn.amount, cur, locale)}</dd>
          <dt>{t.bank.colDescription}</dt><dd>{txn.description ?? "—"}</dd>
          <dt>{t.bank.colCounterparty}</dt><dd>{txn.counterparty?.name ?? txn.counterparty_name ?? "—"}{txn.counterparty_tin ? ` (${txn.counterparty_tin})` : ""}</dd>
          <dt>{t.bank.colStatus}</dt><dd><span className={`badge ${txn.status === "MATCHED" ? "badge-ok" : txn.status === "IGNORED" ? "badge-muted" : "badge-warn"}`}>{t.bank.statuses[txn.status]}</span></dd>
          <dt>{t.bank.matchedAmount}</dt><dd className="num" data-testid="txn-matched">{formatMoney(txn.matched_amount, cur, locale)}</dd>
          <dt>{t.bank.remaining}</dt><dd className="num" data-testid="txn-remaining">{formatMoney(txn.remaining, cur, locale)}</dd>
        </dl>
        {canReconcile && txn.status !== "MATCHED" && active.length === 0 ? (
          txn.status === "IGNORED" ? (
            <Button variant="secondary" loading={busy === "unignore"} onClick={() => void act("unignore", `/transactions/${txnId}/unignore`)}>{t.bank.unignore}</Button>
          ) : (
            <div className="toolbar">
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t.bank.ignoreReason} aria-label={t.bank.ignoreReason} maxLength={500} />
              <Button variant="secondary" loading={busy === "ignore"} onClick={() => void act("ignore", `/transactions/${txnId}/ignore`, { reason: reason || null })}>{t.bank.ignore}</Button>
            </div>
          )
        ) : null}
      </section>

      <section className="card" aria-labelledby="m-h">
        <h2 id="m-h">{t.bank.matchesTitle}</h2>
        {active.length === 0 ? <p className="muted">{t.bank.noMatches}</p> : (
          <ul className="plain" data-testid="txn-matches">
            {active.map((m) => (
              <li key={m.id}>
                <Link href={`/o/${orgId}/documents/${m.document_id}`}>{m.document_number ?? "—"}</Link> — <span className="num">{formatMoney(m.amount, m.currency, locale)}</span>
                {m.created_by_name ? <span className="hint"> · {m.created_by_name}, {dateTime(m.created_at)}</span> : null}
                <MatchReasons reasons={m.reasons} />
                {canReconcile ? <Button className="btn-sm" variant="secondary" loading={busy === m.id} onClick={() => void act(m.id, `/matches/${m.id}/cancel`)}>{t.bank.cancelMatch}</Button> : null}
              </li>
            ))}
          </ul>
        )}
        {history.length ? (
          <ul className="plain hint">
            {history.map((m) => <li key={m.id}>{t.bank.cancelled}: {m.document_number ?? "—"} — {formatMoney(m.amount, m.currency, locale)}</li>)}
          </ul>
        ) : null}
      </section>

      {canReconcile && txn.status !== "IGNORED" && Number(txn.remaining) > 0 ? (
        <section className="card" aria-labelledby="c-h">
          <h2 id="c-h">{t.bank.candidatesTitle}</h2>
          <p className="hint">{t.bank.candidatesNote}</p>
          {txn.candidates.length === 0 ? <p className="muted">{t.bank.noCandidates}</p> : (
            <ul className="plain" data-testid="txn-candidates">
              {txn.candidates.map((c) => (
                <li key={c.document.id} className="suggest" style={{ display: "block", marginBottom: 8 }}>
                  <Link href={`/o/${orgId}/documents/${c.document.id}`}>{docLabel(c.document)}</Link>{" "}
                  <span className="hint">{c.document.doc_date ? dateOnly(c.document.doc_date) : ""} · {t.bank.docRemaining.replace("{amount}", formatMoney(c.document.remaining, cur, locale))} · {t.bank.score}: {c.score}</span>
                  <MatchReasons reasons={c.reasons} />
                  <div className="toolbar">
                    <input className="num" inputMode="decimal" aria-label={t.bank.amountToMatch} value={amounts[c.document.id] ?? ""} onChange={(e) => setAmounts({ ...amounts, [c.document.id]: e.target.value })} />
                    <Button className="btn-sm" loading={busy === `c${c.document.id}`} onClick={() => void act(`c${c.document.id}`, `/transactions/${txnId}/matches`, { allocations: [{ document_id: c.document.id, amount: amounts[c.document.id] }] })}>{t.bank.confirm}</Button>
                    <Button className="btn-sm" variant="secondary" loading={busy === `r${c.document.id}`} onClick={() => void act(`r${c.document.id}`, `/transactions/${txnId}/reject-candidate`, { document_id: c.document.id })}>{t.bank.notThis}</Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <h3>{t.bank.findDocument}</h3>
          <div className="toolbar">
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t.bank.findDocument} maxLength={100} />
            <Button variant="secondary" onClick={() => void runSearch()}>{t.list.search}</Button>
          </div>
          {found.map((d) => (
            <div key={d.id} className="toolbar">
              <span>{d.number ?? "—"} {d.total_amount ? `· ${formatMoney(d.total_amount, d.currency ?? cur, locale)}` : ""}</span>
              {!manual.some((m) => m.doc.id === d.id) ? <Button className="btn-sm" variant="secondary" onClick={() => setManual([...manual, { doc: d, amount: txn.remaining }])}>+</Button> : null}
            </div>
          ))}
          {manual.length ? (
            <div data-testid="manual-alloc">
              {manual.map((m, i) => (
                <div key={m.doc.id} className="toolbar">
                  <span>{m.doc.number ?? "—"}</span>
                  <input className="num" inputMode="decimal" aria-label={t.bank.amountToMatch} value={m.amount} onChange={(e) => setManual(manual.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} />
                </div>
              ))}
              <Button loading={busy === "manual"} onClick={() => void act("manual", `/transactions/${txnId}/matches`, { allocations: manual.map((m) => ({ document_id: m.doc.id, amount: m.amount })) })}>{t.bank.confirm}</Button>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
