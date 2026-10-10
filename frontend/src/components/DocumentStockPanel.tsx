"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Banner, Button, Modal } from "@/components/ui";
import { errorText, fill } from "@/i18n/errors";
import { useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Page, type StockOperation, type StockOperationItem, type Warehouse } from "@/lib/api";

const KINDS = ["receipt", "issue", "supplier_return", "customer_return"] as const;

/** Складские операции по документу: список и оформление новой (только для утверждённого документа). */
export function DocumentStockPanel({ orgId, docId, approved, canRead, canManage }: { orgId: string; docId: string; approved: boolean; canRead: boolean; canManage: boolean }) {
  const t = useT();
  const router = useRouter();
  const [ops, setOps] = useState<StockOperationItem[] | null>(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("receipt");
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [warehouseId, setWarehouseId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    try {
      setOps((await api<Page<StockOperationItem>>(`/organizations/${orgId}/stock/operations?document_id=${docId}&page_size=50`)).items);
    } catch {
      setOps([]);
    }
  }, [orgId, docId]);

  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);

  async function openDialog() {
    setError(null);
    setOpen(true);
    if (!warehouses.length) {
      try {
        const p = await api<Page<Warehouse>>(`/organizations/${orgId}/warehouses?page_size=100&status=active`);
        setWarehouses(p.items);
      } catch {
        setWarehouses([]);
      }
    }
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const op = await api<StockOperation>(`/organizations/${orgId}/stock/operations/from-document`, {
        method: "POST", body: { document_id: docId, kind, warehouse_id: warehouseId },
      });
      router.push(`/o/${orgId}/stock/operations/${op.id}`);
    } catch (e) {
      setError(toApiError(e));
      setBusy(false);
    }
  }

  if (!canRead || (!approved && (ops?.length ?? 0) === 0)) return null;
  const problems = error?.code === "document_lines_not_ready" ? error.details : [];

  return (
    <section className="card" aria-labelledby="ds-h" data-testid="doc-stock">
      <div className="page-head" style={{ marginBottom: 4 }}>
        <h2 id="ds-h">{t.stock.docOperations}</h2>
        {approved && canManage ? <Button variant="secondary" className="btn-sm" onClick={() => void openDialog()}>{t.stock.fromDocument}</Button> : null}
      </div>
      {ops && ops.length === 0 ? <p className="muted">{t.stock.noDocOperations}</p> : null}
      <ul className="upload-list">
        {ops?.map((o) => (
          <li key={o.id}>
            <Link href={`/o/${orgId}/stock/operations/${o.id}`}>{t.stock.kinds[o.kind] ?? o.kind} · {o.warehouse_name}</Link>{" "}
            <span className={`badge ${o.status === "POSTED" ? "badge-ok" : "badge-warn"}`}>{t.stock.statuses[o.status] ?? o.status}</span>
            {o.reversed ? <> <span className="badge badge-muted">{t.stock.reversedBadge}</span></> : null}
          </li>
        ))}
      </ul>

      <Modal open={open} title={t.stock.fromDocTitle} onClose={() => !busy && setOpen(false)}
        footer={<>
          <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>{t.stock.cancel}</Button>
          <Button loading={busy} disabled={!warehouseId} onClick={() => void create()}>{t.stock.fromDocCreate}</Button>
        </>}>
        <p className="muted">{t.stock.fromDocBody}</p>
        <div className="field">
          <label htmlFor="ds-kind">{t.stock.fromDocKind}</label>
          <select id="ds-kind" value={kind} onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])}>
            {KINDS.map((k) => <option key={k} value={k}>{t.stock.kinds[k]}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ds-wh">{t.stock.fromDocWarehouse}</label>
          <select id="ds-wh" value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
            <option value="">—</option>
            {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </div>
        {error && problems.length === 0 ? <Banner>{errorText(t, error)}</Banner> : null}
        {problems.length ? (
          <Banner>
            {errorText(t, error!)}
            <ul>
              {problems.map((d, i) => <li key={i}>{fill((d.code ? t.stock.lineProblems[d.code] : undefined) ?? d.message, d.params)}</li>)}
            </ul>
            <Link href={`/o/${orgId}/products`}>{t.stock.openProducts}</Link>
          </Banner>
        ) : null}
      </Modal>
    </section>
  );
}
