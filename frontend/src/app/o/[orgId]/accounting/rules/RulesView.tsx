"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AccountingTabs } from "@/components/AccountingTabs";
import { DataTable, type Column } from "@/components/DataTable";
import { Button, Skeleton } from "@/components/ui";
import { useFormat, useT } from "@/i18n/LocaleProvider";
import { api, toApiError, type ApiError, type Page, type PostingRuleItem } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useListParams } from "@/lib/useListParams";

export function RulesView() {
  const t = useT();
  const { dateOnly } = useFormat();
  const { orgId } = useParams<{ orgId: string }>();
  const { state: session } = useSession();
  const org = session.status === "ready" ? session.orgs.find((o) => o.id === orgId) : undefined;
  const canRead = !!org?.permissions.includes("accounting.read");
  const canManage = !!org?.permissions.includes("accounting.manage");
  const { state, update } = useListParams({ sort: "created_at", order: "desc", filters: { status: "all" } });
  const [data, setData] = useState<Page<PostingRuleItem> | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const qs = new URLSearchParams({ page: String(state.page), page_size: "25", status: state.filters.status });
    try {
      setData(await api<Page<PostingRuleItem>>(`/organizations/${orgId}/accounting/rules?${qs}`));
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
  if (!canRead) return <div className="card"><h1>{t.catalog.noAccessTitle}</h1><p className="muted">{t.accounting.noAccessBody}</p></div>;

  const columns: Column<PostingRuleItem>[] = [
    { key: "name", header: t.accounting.ruleName, render: (r) => <Link href={`/o/${orgId}/accounting/rules/${r.id}`}><strong>{r.name}</strong></Link> },
    { key: "key", header: t.accounting.ruleKey, render: (r) => `${r.rule_key} · v${r.version}` },
    { key: "type", header: t.accounting.ruleDocType, render: (r) => t.documents.types[r.doc_type] ?? r.doc_type },
    { key: "eff", header: `${t.accounting.ruleFrom} — ${t.accounting.ruleTo}`, render: (r) => `${r.effective_from ? dateOnly(r.effective_from) : "…"} — ${r.effective_to ? dateOnly(r.effective_to) : "…"}` },
    { key: "status", header: t.accounting.colStatus, render: (r) => <span className={`badge ${r.status === "APPROVED" ? "badge-ok" : r.status === "DRAFT" ? "badge-warn" : "badge-muted"}`}>{t.accounting.ruleStatuses[r.status] ?? r.status}</span> },
  ];
  const filtered = state.filters.status !== "all";
  return (
    <div className="stack">
      <div className="page-head">
        <h1>{t.accounting.rulesTitle}</h1>
        {canManage ? <Link className="btn" href={`/o/${orgId}/accounting/rules/new`}>{t.accounting.newRule}</Link> : null}
      </div>
      <AccountingTabs />
      <p className="hint">{t.accounting.rulesNote}</p>
      <div className="toolbar">
        <select aria-label={t.accounting.colStatus} value={state.filters.status} onChange={(e) => update({ filters: { status: e.target.value } })}>
          {Object.entries(t.accounting.ruleStatusFilters).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {filtered ? <Button variant="secondary" onClick={() => update({ filters: { status: "all" } })}>{t.list.reset}</Button> : null}
      </div>
      <DataTable
        caption={t.accounting.rulesTitle} columns={columns} rows={data?.items ?? null} total={data?.total ?? 0}
        page={state.page} pageSize={data?.page_size ?? 25} loading={loading} error={error} onRetry={load}
        sort="created_at" order="desc" onSort={() => undefined} onPage={(p) => update({ page: p })} rowKey={(r) => r.id}
        empty={filtered ? <p className="muted">{t.list.emptyFiltered}</p> : (
          <>
            <p className="muted">{t.accounting.emptyRules}</p>
            {canManage ? <Link className="btn" href={`/o/${orgId}/accounting/rules/new`}>{t.accounting.newRule}</Link> : null}
          </>
        )}
      />
    </div>
  );
}
