"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type JournalEntry, type Proposal } from "@/lib/api";
import { formatMoney } from "@/lib/money";

/** Предложения проводок по утверждённому документу (по утверждённым правилам бухгалтера). Проводит человек. */
export function DocumentJournalPanel({ orgId, docId, approved, canRead, canPost }: { orgId: string; docId: string; approved: boolean; canRead: boolean; canPost: boolean }) {
  const t = useT();
  const { locale } = useLocale();
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api<Proposal[]>(`/organizations/${orgId}/accounting/documents/${docId}/proposals`));
    } catch {
      setItems([]);
    }
  }, [orgId, docId]);
  useEffect(() => {
    if (canRead && approved) void load();
  }, [canRead, approved, load]);

  async function post(p: Proposal) {
    setBusy(p.rule.id);
    setError(null);
    try {
      await api<JournalEntry>(`/organizations/${orgId}/accounting/entries/from-document`, { method: "POST", body: { document_id: docId, rule_id: p.rule.id } });
      await load();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  if (!canRead || !approved) return null;
  return (
    <section className="card" aria-labelledby="dj-h" data-testid="doc-journal">
      <h2 id="dj-h">{t.accounting.docEntries}</h2>
      {items && items.length === 0 ? <p className="muted">{t.accounting.docNoRules}</p> : <p className="hint">{t.accounting.docProposalNote}</p>}
      {error ? <Banner>{errorText(t, error)}{error.details.map((d, i) => <div key={i}>{t.accounting.problems[d.code ?? ""] ?? d.code}</div>)}</Banner> : null}
      {items?.map((p) => (
        <div key={p.rule.id} className="suggest" style={{ display: "block", marginBottom: 8 }}>
          <strong>{p.rule.name}</strong> <span className="hint">v{p.rule.version}{p.legal_basis ? ` · ${t.accounting.ruleBasis}: ${p.legal_basis}` : ""}</span>
          {p.lines.length ? (
            <div className="table-wrap">
              <table className="lines-table">
                <tbody>
                  {p.lines.map((l, i) => (
                    <tr key={i}>
                      <td>{l.account_code} · {l.account_name}</td>
                      <td className="num">{Number(l.debit) !== 0 ? `${t.accounting.colDebit} ${formatMoney(l.debit, p.currency ?? "", locale)}` : ""}</td>
                      <td className="num">{Number(l.credit) !== 0 ? `${t.accounting.colCredit} ${formatMoney(l.credit, p.currency ?? "", locale)}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {p.warnings.map((c) => <div key={c} className="issue issue-warning">{t.accounting.proposalWarnings[c] ?? c}</div>)}
          {p.problems.map((c) => <div key={c} className="issue issue-warning">{t.accounting.problems[c] ?? c}</div>)}
          {p.posted_entry_id ? (
            <p><span className="badge badge-ok">{t.accounting.docPosted}</span> <Link href={`/o/${orgId}/accounting/entries/${p.posted_entry_id}`}>{t.accounting.docOpenEntry}</Link></p>
          ) : canPost ? (
            <Button className="btn-sm" loading={busy === p.rule.id} disabled={p.problems.length > 0} onClick={() => void post(p)}>{t.accounting.docPost}</Button>
          ) : null}
        </div>
      ))}
    </section>
  );
}
