"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { EntityPicker } from "@/components/EntityPicker";
import { Banner, Button, ErrorState, Field, Modal, Skeleton } from "@/components/ui";
import { errorText, fieldErrorMap, fill } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type Account, type ApiError, type Currency, type JournalEntry, type Page } from "@/lib/api";
import { formatMoney, formatScaled4, normalizeMoneyInput, sumAmounts4 } from "@/lib/money";
import { useSession } from "@/lib/session";

type LineForm = { account_id: string; label: string; debit: string; credit: string; memo: string };

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const blankLine = (): LineForm => ({ account_id: "", label: "", debit: "", credit: "", memo: "" });

export default function EntryPage() {
  const t = useT();
  const { locale } = useLocale();
  const { dateTime, dateOnly } = useFormat();
  const router = useRouter();
  const { orgId, entryId } = useParams<{ orgId: string; entryId: string }>();
  const isNew = entryId === "new";
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canPost = !!org?.permissions.includes("accounting.post");

  const [entry, setEntry] = useState<JournalEntry | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [fieldErrs, setFieldErrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [dlg, setDlg] = useState(false);
  const [reverseDate, setReverseDate] = useState(today());
  const [currencies, setCurrencies] = useState<Currency[]>([]);
  const [date, setDate] = useState(today());
  const [currency, setCurrency] = useState("");
  const [description, setDescription] = useState("");
  const [lines, setLines] = useState<LineForm[]>([blankLine(), blankLine()]);

  useEffect(() => {
    if (!canRead || isNew) return;
    api<JournalEntry>(`/organizations/${orgId}/accounting/entries/${entryId}`).then(setEntry).catch((e) => setError(toApiError(e)));
  }, [canRead, isNew, orgId, entryId]);
  useEffect(() => {
    if (!canRead || !isNew) return;
    api<Currency[]>(`/organizations/${orgId}/currencies`).then((list) => {
      const enabled = list.filter((c) => c.enabled);
      setCurrencies(enabled);
      setCurrency((cur) => cur || enabled.find((c) => c.is_base)?.code || enabled[0]?.code || "");
    }).catch(() => setCurrencies([]));
  }, [canRead, isNew, orgId]);

  const totals = useMemo(() => {
    const d = sumAmounts4(lines.map((l) => l.debit));
    const c = sumAmounts4(lines.map((l) => l.credit));
    return { d, c, diff: d - c };
  }, [lines]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;
  if (error) {
    return error.status === 404 ? (
      <div className="card"><h1>{t.accounting.notFound}</h1><Link href={`/o/${orgId}/accounting`}>{t.accounting.back}</Link></div>
    ) : <ErrorState error={error} />;
  }
  if (!isNew && !entry) return <Skeleton lines={6} />;

  const searchAccounts = async (q: string) =>
    (await api<Page<Account>>(`/organizations/${orgId}/accounting/accounts?page_size=10&q=${encodeURIComponent(q)}`)).items.filter((a) => a.allow_posting);
  const setLine = (i: number, patch: Partial<LineForm>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const err = (f: string) => fieldErrs[f];
  const complete = lines.every((l) => l.account_id && (l.debit.trim() !== "") !== (l.credit.trim() !== "")) && description.trim() !== "" && currency !== "";
  const balanced = totals.diff === BigInt(0) && totals.d > BigInt(0);

  async function submit() {
    setActionError(null);
    setFieldErrs({});
    setBusy("post");
    try {
      const body = {
        entry_date: date, currency, description,
        lines: lines.map((l) => ({
          account_id: l.account_id, debit: normalizeMoneyInput(l.debit) || "0", credit: normalizeMoneyInput(l.credit) || "0", memo: l.memo || null,
        })),
      };
      const e = await api<JournalEntry>(`/organizations/${orgId}/accounting/entries`, { method: "POST", body });
      router.replace(`/o/${orgId}/accounting/entries/${e.id}`);
    } catch (e) {
      const fe = fieldErrorMap(t, e);
      if (Object.keys(fe).length) setFieldErrs(fe);
      setActionError(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  async function reverse() {
    if (!entry) return;
    setBusy("reverse");
    setActionError(null);
    try {
      const rv = await api<JournalEntry>(`/organizations/${orgId}/accounting/entries/${entry.id}/reverse`, { method: "POST", body: { entry_date: reverseDate } });
      router.push(`/o/${orgId}/accounting/entries/${rv.id}`);
    } catch (e) {
      setActionError(toApiError(e));
    } finally {
      setDlg(false);
      setBusy(null);
    }
  }

  if (!isNew && entry) {
    return (
      <div className="stack">
        <div className="page-head">
          <div>
            <p style={{ margin: 0 }}><Link href={`/o/${orgId}/accounting`}>← {t.accounting.back}</Link></p>
            <h1 style={{ marginTop: 4 }}>
              № {entry.number} · {dateOnly(entry.entry_date)} <span className="badge badge-ok">{t.accounting.posted}</span>
              {entry.reverses_id ? <> <span className="badge badge-muted">{t.accounting.reversalBadge}</span></> : null}
              {entry.reversed_by_id ? <> <span className="badge badge-muted">{t.accounting.reversedBadge}</span></> : null}
            </h1>
            <p className="muted" style={{ margin: 0 }}>{t.accounting.createdBy}: {entry.created_by_name ?? "—"} · {dateTime(entry.created_at)}</p>
          </div>
          <div className="actions-bar">
            {canPost && !entry.reverses_id && !entry.reversed_by_id ? <Button variant="secondary" onClick={() => setDlg(true)}>{t.accounting.reverse}</Button> : null}
          </div>
        </div>
        <AccountingTabs />
        {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
        {entry.source_document ? (
          <Banner kind="info">
            {t.accounting.sourceDocument}: <Link href={`/o/${orgId}/documents/${entry.source_document.id}`}>{t.documents.types[entry.source_document.doc_type] ?? entry.source_document.doc_type}{entry.source_document.number ? ` № ${entry.source_document.number}` : ""}</Link>
            {entry.rule ? <> · {t.accounting.appliedRule}: <Link href={`/o/${orgId}/accounting/rules/${entry.rule.id}`}>{entry.rule.name} ({t.accounting.ruleVersion} {entry.rule.version})</Link></> : null}
          </Banner>
        ) : null}
        {entry.reverses_id ? <Banner kind="info">{t.accounting.reversalOf}: <Link href={`/o/${orgId}/accounting/entries/${entry.reverses_id}`}>{t.accounting.open}</Link></Banner> : null}
        {entry.reversed_by_id ? <Banner kind="info">{t.accounting.reversedBy}: <Link href={`/o/${orgId}/accounting/entries/${entry.reversed_by_id}`}>{t.accounting.open}</Link></Banner> : null}
        <section className="card">
          <p>{entry.description}</p>
          <div className="table-wrap">
            <table className="lines-table">
              <thead><tr><th>#</th><th>{t.accounting.lAccount}</th><th>{t.accounting.lDebit}</th><th>{t.accounting.lCredit}</th><th>{t.accounting.lMemo}</th></tr></thead>
              <tbody>
                {entry.lines.map((l) => (
                  <tr key={l.position}>
                    <td>{l.position}</td>
                    <td>{l.account_code} · {l.account_name}</td>
                    <td className="num">{Number(l.debit) !== 0 ? formatMoney(l.debit, entry.currency, locale) : ""}</td>
                    <td className="num">{Number(l.credit) !== 0 ? formatMoney(l.credit, entry.currency, locale) : ""}</td>
                    <td>{l.memo ?? ""}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr><td /><td><strong>{t.accounting.totals}</strong></td><td className="num"><strong>{formatMoney(entry.total, entry.currency, locale)}</strong></td><td className="num"><strong>{formatMoney(entry.total, entry.currency, locale)}</strong></td><td /></tr></tfoot>
            </table>
          </div>
        </section>
        <Modal open={dlg} title={t.accounting.reverseTitle} onClose={() => setDlg(false)}
          footer={<>
            <Button variant="secondary" onClick={() => setDlg(false)}>{t.accounting.cancel}</Button>
            <Button loading={busy === "reverse"} onClick={() => void reverse()}>{t.accounting.reverse}</Button>
          </>}>
          <p className="muted">{t.accounting.reverseBody}</p>
          <Field id="rv-date" type="date" label={t.accounting.reverseDate} value={reverseDate} onChange={(e) => setReverseDate(e.target.value)} />
        </Modal>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p style={{ margin: 0 }}><Link href={`/o/${orgId}/accounting`}>← {t.accounting.back}</Link></p>
          <h1 style={{ marginTop: 4 }}>{t.accounting.newEntry}</h1>
        </div>
      </div>
      <AccountingTabs />
      {actionError ? <Banner>{errorText(t, actionError)}</Banner> : null}
      <section className="card" aria-labelledby="ne-h">
        <h2 id="ne-h">{t.accounting.entriesTitle}</h2>
        <fieldset disabled={!canPost} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <div className="doc-grid">
            <Field id="e-date" type="date" label={t.accounting.fDate} value={date} onChange={(e) => setDate(e.target.value)} error={err("entry_date")} />
            <div className="field">
              <label htmlFor="e-cur">{t.accounting.fCurrency}</label>
              <select id="e-cur" value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
              </select>
              {err("currency") ? <p className="field-error" role="alert">{err("currency")}</p> : null}
            </div>
          </div>
          <Field id="e-desc" label={t.accounting.fDescription} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
          <h3 className="issue-h">{t.accounting.lines}</h3>
          <div className="table-wrap">
            <table className="lines-table">
              <thead><tr><th>#</th><th>{t.accounting.lAccount}</th><th>{t.accounting.lDebit}</th><th>{t.accounting.lCredit}</th><th>{t.accounting.lMemo}</th><th /></tr></thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td style={{ minWidth: 220 }}>
                      <EntityPicker<Account> hideLabel label={`${t.accounting.lAccount} ${i + 1}`} value={l.account_id ? { id: l.account_id, label: l.label } : null}
                        search={searchAccounts} toItem={(a) => ({ id: a.id, label: a.code, hint: a.name })}
                        onChange={(it) => setLine(i, { account_id: it?.id ?? "", label: it?.label ?? "" })} error={err(`lines.${i + 1}.account_id`)} />
                    </td>
                    <td><input inputMode="decimal" aria-label={`${t.accounting.lDebit} ${i + 1}`} value={l.debit} onChange={(e) => setLine(i, { debit: e.target.value })} /></td>
                    <td><input inputMode="decimal" aria-label={`${t.accounting.lCredit} ${i + 1}`} value={l.credit} onChange={(e) => setLine(i, { credit: e.target.value })} /></td>
                    <td><input aria-label={`${t.accounting.lMemo} ${i + 1}`} value={l.memo} maxLength={300} onChange={(e) => setLine(i, { memo: e.target.value })} /></td>
                    <td><Button type="button" variant="secondary" className="btn-sm" aria-label={`${t.accounting.removeLine} ${i + 1}`} disabled={lines.length <= 2} onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</Button></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr><td /><td><strong>{t.accounting.totals}</strong></td><td className="num">{formatScaled4(totals.d)}</td><td className="num">{formatScaled4(totals.c)}</td><td /><td /></tr>
              </tfoot>
            </table>
          </div>
          <div style={{ marginTop: 8 }}>
            <Button type="button" variant="secondary" className="btn-sm" disabled={lines.length >= 200} onClick={() => setLines([...lines, blankLine()])}>{t.accounting.addLine}</Button>
          </div>
        </fieldset>
        <p className={balanced ? "hint" : "field-error"} role="status" data-testid="balance-status">
          {balanced ? t.accounting.balanced : totals.diff !== BigInt(0) ? fill(t.accounting.unbalanced, { diff: formatScaled4(totals.diff < BigInt(0) ? -totals.diff : totals.diff) }) : ""}
        </p>
        {canPost ? (
          <div className="drawer-actions">
            <Button onClick={() => void submit()} loading={busy === "post"} disabled={!complete || !balanced}>{t.accounting.post}</Button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
