"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Banner, Button } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError as ApiErrorT, type DocumentFull, type Extraction, type MatchCandidate } from "@/lib/api";
import { confidenceLevel } from "@/lib/suggestions";

export type Suggestion = { path: string; label: string; current: string; value: string; confidence: number | null; evidence: string | null };

type Props = {
  orgId: string;
  docId: string;
  extraction: Extraction;
  canRun: boolean;
  suggestions: Suggestion[];
  onApply: (s: Suggestion[]) => void;
  onCounterparty: (c: MatchCandidate) => void;
  onDoc: (d: DocumentFull) => void;
  editable: boolean;
};

const ACTIVE = new Set(["queued", "running"]);

export function ExtractionPanel({ orgId, docId, extraction, canRun, suggestions, onApply, onCounterparty, onDoc, editable }: Props) {
  const t = useT();
  const { dateTime } = useFormat();
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [err, setErr] = useState<ApiErrorT | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [appliedMsg, setAppliedMsg] = useState(false);
  const [slow, setSlow] = useState(false);
  const job = extraction.job;
  const active = !!job && ACTIVE.has(job.status);
  const startedRef = useRef<number>(0);
  const onDocRef = useRef(onDoc);
  onDocRef.current = onDoc;

  useEffect(() => {
    if (!canRun) return;
    api<{ configured: boolean }>(`/organizations/${orgId}/documents/ai-status`)
      .then((s) => setConfigured(s.configured))
      .catch(() => setConfigured(false));
  }, [canRun, orgId]);

  const refresh = useCallback(async () => {
    try {
      onDocRef.current(await api<DocumentFull>(`/organizations/${orgId}/documents/${docId}`));
    } catch (e) {
      setErr(toApiError(e));
    }
  }, [orgId, docId]);

  // опрос, пока задача в очереди или выполняется; прекращается при выходе со страницы
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return;
    }
    startedRef.current = startedRef.current || Date.now();
    const id = setInterval(() => {
      if (Date.now() - startedRef.current > 90_000) setSlow(true);
      void refresh();
    }, 3000);
    return () => clearInterval(id);
  }, [active, refresh]);

  async function post(path: string, name: string) {
    setErr(null);
    setBusy(name);
    try {
      startedRef.current = Date.now();
      await api(`/organizations/${orgId}/documents/${docId}/${path}`, { method: "POST" });
      await refresh();
    } catch (e) {
      setErr(toApiError(e));
    } finally {
      setBusy(null);
    }
  }

  if (!job && !canRun) return null;
  const jobError = job?.error_code ? (t.ai.errors[job.error_code] ?? job.error_message ?? job.error_code) : null;
  const confText = (c: number | null) => (c === null ? "—" : `${Math.round(c * 100)}%`);
  const invalid = extraction.fields.filter((f) => f.validation === "invalid");

  return (
    <section className="card" aria-labelledby="ai-h" data-testid="ai-panel">
      <div className="page-head" style={{ marginBottom: 4 }}>
        <h2 id="ai-h">{t.ai.title}</h2>
        {canRun && editable && !active && configured !== false ? (
          <Button variant="secondary" className="btn-sm" loading={busy === "extract"} onClick={() => void post("extract", "extract")}>
            {job ? t.ai.rerun : t.ai.run}
          </Button>
        ) : null}
        {active && canRun ? (
          <Button variant="secondary" className="btn-sm" loading={busy === "cancel"} onClick={() => void post("extract/cancel", "cancel")}>{t.ai.cancel}</Button>
        ) : null}
      </div>
      <p className="hint">{t.ai.note}</p>
      {canRun && configured === false ? <Banner kind="info">{t.ai.notConfigured}</Banner> : null}
      {err ? <Banner>{errorText(t, err)}</Banner> : null}

      {job ? (
        <div className="ai-status" data-status={job.status}>
          {job.mock ? <div className="issue issue-warning" role="note">{t.ai.mock}</div> : null}
          <p style={{ margin: "4px 0" }}>
            {job.status === "queued" ? <span role="status">{t.ai.queued}</span> : null}
            {job.status === "running" ? <span role="status">{t.ai.running}</span> : null}
            {job.status === "succeeded" ? <span className="badge badge-ok">{t.ai.succeeded}</span> : null}
            {job.status === "failed" ? <span className="badge badge-danger">{t.ai.failed}</span> : null}
            {job.status === "cancelled" ? <span className="badge badge-muted">{t.ai.cancelled}</span> : null}
            {" "}
            <span className="hint">{t.ai.provider}: {job.provider} · {t.ai.model}: {job.model}{job.finished_at ? ` · ${t.ai.finished}: ${dateTime(job.finished_at)}` : ""}</span>
          </p>
          {slow && active ? <Banner kind="info">{t.ai.stuck}</Banner> : null}
          {job.status === "failed" && jobError ? <div className="issue issue-error" role="alert">{jobError}</div> : null}
        </div>
      ) : null}

      {extraction.counterparty_candidates.length && editable ? (
        <>
          <h3 className="issue-h">{t.ai.candidates}: {t.documents.fCounterparty}</h3>
          <ul className="upload-list">
            {extraction.counterparty_candidates.map((c) => (
              <li key={c.id}>
                <strong>{c.name}</strong>{c.tin ? ` · ${c.tin}` : ""} <span className="hint">({t.ai.reasons[c.reason] ?? c.reason})</span>{" "}
                <Button variant="secondary" className="btn-sm" onClick={() => onCounterparty(c)}>{t.ai.choose}</Button>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {suggestions.length && editable ? (
        <>
          <h3 className="issue-h">{t.ai.suggests}</h3>
          <ul className="suggest-list">
            {suggestions.map((s) => (
              <li key={s.path} className="suggest" data-path={s.path}>
                <div>
                  <strong>{s.label}</strong>:{" "}
                  {s.current ? <><s className="muted">{s.current}</s> → </> : null}
                  <span className="suggest-value">{s.value}</span>{" "}
                  <span className={`badge conf-${confidenceLevel(s.confidence)}`} title={t.ai.conf}>{confText(s.confidence)}</span>
                  {s.evidence ? <div className="hint">{t.ai.evidence}: «{s.evidence}»</div> : null}
                </div>
                <Button variant="secondary" className="btn-sm" onClick={() => { onApply([s]); setAppliedMsg(false); }}>{t.ai.apply}</Button>
              </li>
            ))}
          </ul>
          <Button className="btn-sm" onClick={() => { onApply(suggestions); setAppliedMsg(true); }}>{t.ai.applyAll}</Button>
        </>
      ) : null}
      {appliedMsg && suggestions.length === 0 ? <Banner kind="info">{t.ai.appliedAll}</Banner> : null}

      {invalid.length ? (
        <>
          <h3 className="issue-h">{t.ai.invalid}</h3>
          <ul className="upload-list">
            {invalid.map((f) => <li key={f.path}><code>{f.path}</code>: {f.value} {f.evidence ? <span className="hint">«{f.evidence}»</span> : null}</li>)}
          </ul>
        </>
      ) : null}
    </section>
  );
}
