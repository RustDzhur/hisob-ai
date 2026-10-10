"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DataTable, type Column } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner, Button, Drawer, Skeleton } from "@/components/ui";
import { errorText } from "@/i18n/errors";
import { useFormat, useLocale, useT } from "@/i18n/LocaleProvider";
import { api, ApiError, toApiError, type ApiError as ApiErrorT, type DocumentFull, type DocumentListItem, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

const DOC_TYPES = ["invoice", "esf", "waybill", "ttn", "act", "contract", "receipt", "statement", "payment_order", "return", "other"];
const ACCEPT = ".pdf,.jpg,.jpeg,.png,.xlsx,.csv,application/pdf,image/jpeg,image/png";

type Result = { name: string; state: "pending" | "uploading" | "ok" | "dup" | "error"; docId?: string; message?: string };

export function DocumentsView() {
  const t = useT();
  const { locale } = useLocale();
  const { dateOnly, date } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("documents.read");
  const canUpload = !!org?.permissions.includes("documents.upload");

  const { state, update } = useListParams({ sort: "created_at", order: "desc", filters: { status: "active", doc_type: "" } });
  const [searchText, setSearchText] = useState(state.q);
  const [data, setData] = useState<Page<DocumentListItem> | null>(null);
  const [error, setError] = useState<ApiErrorT | null>(null);
  const [loading, setLoading] = useState(true);

  const [drawer, setDrawer] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [docType, setDocType] = useState("invoice");
  const [results, setResults] = useState<Result[]>([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [aiOn, setAiOn] = useState(false);
  const [aiConfigured, setAiConfigured] = useState(false);

  useEffect(() => {
    if (searchText === state.q) return;
    const id = setTimeout(() => update({ q: searchText }), 300);
    return () => clearTimeout(id);
  }, [searchText, state.q, update]);

  useEffect(() => {
    if (!canUpload) return;
    api<{ configured: boolean }>(`/organizations/${orgId}/documents/ai-status`)
      .then((r) => setAiConfigured(r.configured))
      .catch(() => setAiConfigured(false));
  }, [canUpload, orgId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", sort: state.sort, order: state.order, status: state.filters.status });
    if (state.filters.doc_type) qs.set("doc_type", state.filters.doc_type);
    if (state.q) qs.set("q", state.q);
    try {
      setData(await api<Page<DocumentListItem>>(`/organizations/${orgId}/documents?${qs}`));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setLoading(false);
    }
  }, [orgId, state]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  if (session.status !== "ready") return <Skeleton />;
  if (!canRead) {
    return (
      <div className="card">
        <h1>{t.catalog.noAccessTitle}</h1>
        <p className="muted">{t.documents.noAccessBody}</p>
      </div>
    );
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    // копируем сразу: FileList «живой» и очищается вместе с value поля ввода
    const picked = Array.from(list);
    setFiles((prev) => [...prev, ...picked]);
    setResults([]);
  }

  async function startUpload() {
    setBusy(true);
    const res: Result[] = files.map((f) => ({ name: f.name, state: "pending" }));
    setResults([...res]);
    for (let i = 0; i < files.length; i++) {
      res[i] = { ...res[i], state: "uploading" };
      setResults([...res]);
      const fd = new FormData();
      fd.append("file", files[i]);
      fd.append("doc_type", docType);
      if (aiOn && aiConfigured) fd.append("extract", "true");
      try {
        const d = await api<DocumentFull>(`/organizations/${orgId}/documents`, { method: "POST", body: fd });
        res[i] = { name: files[i].name, state: "ok", docId: d.id };
      } catch (e) {
        const err = toApiError(e);
        if (err.code === "document_duplicate") {
          res[i] = { name: files[i].name, state: "dup", docId: String(err.details[0]?.params?.document_id ?? "") };
        } else {
          const detail = err.details[0]?.code;
          res[i] = { name: files[i].name, state: "error", message: errorText(t, new ApiError(err.status, detail && t.errors[detail] ? detail : err.code, err.message)) };
        }
      }
      setResults([...res]);
    }
    setFiles([]);
    setBusy(false);
    await load();
  }

  const columns: Column<DocumentListItem>[] = [
    { key: "type", header: t.documents.colType, render: (d) => t.documents.types[d.doc_type] ?? d.doc_type },
    {
      key: "number", header: t.documents.colNumber, sortKey: "number",
      render: (d) => (
        <>
          <Link href={`/o/${orgId}/documents/${d.id}`}><strong>{d.number ?? "—"}</strong></Link>
          {d.original_name ? <div className="sub">{d.original_name}</div> : null}
        </>
      ),
    },
    { key: "date", header: t.documents.colDate, sortKey: "doc_date", render: (d) => (d.doc_date ? dateOnly(d.doc_date) : "—") },
    { key: "cp", header: t.documents.colCounterparty, render: (d) => d.counterparty_name ?? "—" },
    { key: "total", header: t.documents.colTotal, render: (d) => (d.total_amount && d.currency ? <span className="num">{formatMoney(d.total_amount, d.currency, locale)}</span> : "—") },
    { key: "status", header: t.documents.colStatus, render: (d) => <StatusBadge status={d.status} /> },
    { key: "created", header: t.documents.colCreated, sortKey: "created_at", render: (d) => date(d.created_at) },
  ];

  const filtered = !!(state.q || state.filters.status !== "active" || state.filters.doc_type);
  const okCount = results.filter((r) => r.state === "ok").length;

  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.documents.title}</h1>
        {canUpload ? <Button onClick={() => { setDrawer(true); setResults([]); setFiles([]); }}>{t.documents.upload}</Button> : null}
      </div>

      <div className="toolbar">
        <input type="search" value={searchText} onChange={(e) => setSearchText(e.target.value)}
          placeholder={t.documents.searchPlaceholder} aria-label={t.list.search} maxLength={100} />
        <select aria-label={t.documents.filterStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.documents.statusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select aria-label={t.documents.filterType} value={state.filters.doc_type} onChange={(e) => update({ filters: { doc_type: e.target.value } })}>
          <option value="">{t.documents.allTypes}</option>
          {DOC_TYPES.map((k) => <option key={k} value={k}>{t.documents.types[k]}</option>)}
        </select>
        {filtered ? (
          <Button variant="secondary" onClick={() => { setSearchText(""); update({ q: "", filters: { status: "active", doc_type: "" } }); }}>{t.list.reset}</Button>
        ) : null}
      </div>

      <DataTable
        caption={t.documents.title}
        columns={columns}
        rows={data?.items ?? null}
        total={data?.total ?? 0}
        page={state.page}
        pageSize={data?.page_size ?? 25}
        loading={loading}
        error={error}
        onRetry={load}
        sort={state.sort}
        order={state.order}
        onSort={(key, order) => update({ sort: key, order: order ?? (state.sort === key && state.order === "desc" ? "asc" : "desc") })}
        onPage={(p) => update({ page: p })}
        rowKey={(d) => d.id}
        empty={
          filtered ? (
            <p className="muted">{t.list.emptyFiltered}</p>
          ) : (
            <>
              <p className="muted">{t.documents.empty}</p>
              {canUpload ? <Button onClick={() => setDrawer(true)}>{t.documents.upload}</Button> : null}
            </>
          )
        }
      />

      <Drawer open={drawer} title={t.documents.uploadTitle} onClose={() => !busy && setDrawer(false)}>
        <p className="muted">{t.documents.uploadHint}</p>
        <div className="field">
          <label htmlFor="up-type">{t.documents.docType}</label>
          <select id="up-type" value={docType} onChange={(e) => setDocType(e.target.value)} disabled={busy}>
            {DOC_TYPES.map((k) => <option key={k} value={k}>{t.documents.types[k]}</option>)}
          </select>
        </div>
        <div className={`dropzone${over ? " over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); addFiles(e.dataTransfer.files); }}>
          <div className="row-actions" style={{ justifyContent: "center" }}>
            <label className="btn btn-secondary">
              {t.documents.chooseFile}
              <input type="file" multiple accept={ACCEPT} hidden disabled={busy} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
            <label className="btn btn-secondary">
              {t.documents.takePhoto}
              <input type="file" accept="image/*" capture="environment" hidden disabled={busy} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
          </div>
          {files.length ? (
            <ul className="upload-list">{files.map((f, i) => <li key={`${f.name}-${i}`}>{f.name}</li>)}</ul>
          ) : null}
        </div>
        {canUpload && org?.permissions.includes("documents.review") ? (
          <label className="ack-row">
            <input type="checkbox" checked={aiOn && aiConfigured} disabled={busy || !aiConfigured} onChange={(e) => setAiOn(e.target.checked)} />
            <span>{t.ai.extractNow}{!aiConfigured ? <span className="hint"> — {t.ai.extractUnavailable}</span> : null}</span>
          </label>
        ) : null}
        <Button onClick={() => void startUpload()} loading={busy} disabled={files.length === 0}>{t.documents.startUpload}</Button>

        {results.length ? (
          <ul className="upload-list" aria-live="polite">
            {results.map((r, i) => (
              <li key={`${r.name}-${i}`}>
                <strong>{r.name}</strong>{" — "}
                {r.state === "uploading" || r.state === "pending" ? t.documents.uploading : null}
                {r.state === "ok" ? <>{t.documents.uploaded}. <Link href={`/o/${orgId}/documents/${r.docId}`}>{t.documents.open}</Link></> : null}
                {r.state === "dup" ? <>{t.documents.alreadyUploaded}. <Link href={`/o/${orgId}/documents/${r.docId}`}>{t.documents.openExisting}</Link></> : null}
                {r.state === "error" ? <span className="field-error">{t.documents.failedFile}: {r.message}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {okCount > 0 && !busy ? <Banner kind="info">{t.documents.uploaded}: {okCount}</Banner> : null}
      </Drawer>
    </div>
  );
}
