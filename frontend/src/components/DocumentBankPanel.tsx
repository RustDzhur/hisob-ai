"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { MatchReasons } from "@/components/MatchReasons";
import { Banner, Button } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type DocumentBank } from "@/lib/api";
import { formatMoney } from "@/lib/money";

/** Банковские платежи по утверждённому документу: сопоставления, остаток, подходящие операции. */
export function DocumentBankPanel({ orgId, docId, approved, canRead, canReconcile }: { orgId: string; docId: string; approved: boolean; canRead: boolean; canReconcile: boolean }) {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly } = useFormat();
  const [data, setData] = useState<DocumentBank | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setData(await api<DocumentBank>(`/organizations/${orgId}/bank/documents/${docId}`));
    } catch {
      setData(null);
    }
  }, [orgId, docId]);
  useEffect(() => {
    if (canRead && approved) void load();
  }, [canRead, approved, load]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const allocations = Object.entries(picked).map(([transaction_id, amount]) => ({ transaction_id, amount }));
      setData(await api<DocumentBank>(`/organizations/${orgId}/bank/matches/merge`, { method: "POST", body: { document_id: docId, allocations } }));
      setPicked({});
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!canRead || !approved || !data) return null;
  const cur = data.currency ?? "";
  const active = data.matches.filter((m) => m.status === "ACTIVE");
  return (
    <section className="card" aria-labelledby="db-h" data-testid="doc-bank">
      <h2 id="db-h">{t.bank.paymentsTitle}</h2>
      <p className="hint">{t.bank.paymentsNote}</p>
      {error ? <Banner>{errorText(t, error)}</Banner> : null}
      <dl className="kv">
        <dt>{t.bank.paidTotal}</dt><dd className="num" data-testid="doc-paid">{formatMoney(data.matched_total, cur, locale)}</dd>
        {data.remaining !== null ? <><dt>{t.bank.docTotalRemaining}</dt><dd className="num" data-testid="doc-unpaid">{formatMoney(data.remaining, cur, locale)}</dd></> : null}
      </dl>
      {active.length === 0 ? <p className="muted">{t.bank.noMatches}</p> : (
        <ul className="plain">
          {active.map((m) => (
            <li key={m.id}>
              <Link href={`/o/${orgId}/bank/transactions/${m.transaction_id}`}>{dateOnly(m.txn_date)}</Link> — <span className="num">{formatMoney(m.amount, m.currency, locale)}</span>
              <MatchReasons reasons={m.reasons} />
            </li>
          ))}
        </ul>
      )}
      {canReconcile ? (
        data.candidates.length === 0 ? <p className="muted">{t.bank.noPayments}</p> : (
          <>
            <ul className="plain" data-testid="doc-candidates">
              {data.candidates.map((c) => (
                <li key={c.transaction.id} className="suggest" style={{ display: "block", marginBottom: 8 }}>
                  <label>
                    <input type="checkbox" checked={c.transaction.id in picked} onChange={(e) => {
                      const next = { ...picked };
                      if (e.target.checked) next[c.transaction.id] = c.suggested_amount; else delete next[c.transaction.id];
                      setPicked(next);
                    }} />{" "}
                    {dateOnly(c.transaction.txn_date)} · {formatMoney(c.transaction.amount, c.transaction.currency, locale)} · {c.transaction.bank_account_name}
                  </label>{" "}
                  <Link href={`/o/${orgId}/bank/transactions/${c.transaction.id}`}>{t.bank.openTxn}</Link>
                  <div className="hint">{c.transaction.description ?? ""}</div>
                  <MatchReasons reasons={c.reasons} />
                  {c.transaction.id in picked ? (
                    <input className="num" inputMode="decimal" aria-label={t.bank.amountToMatch} value={picked[c.transaction.id]} onChange={(e) => setPicked({ ...picked, [c.transaction.id]: e.target.value })} />
                  ) : null}
                </li>
              ))}
            </ul>
            <Button loading={busy} disabled={Object.keys(picked).length === 0} onClick={() => void confirm()}>{t.bank.confirmSelected}</Button>
          </>
        )
      ) : null}
    </section>
  );
}
