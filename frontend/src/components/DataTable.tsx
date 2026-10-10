"use client";

import { Button, ErrorState, Skeleton } from "@/components/ui";
import { useT } from "@/i18n/LocaleProvider";
import type { ApiError } from "@/lib/api";

export type Column<T> = {
  key: string;
  header: string;
  sortKey?: string; // если задан — колонка сортируется на сервере
  render: (row: T) => React.ReactNode;
};

type Props<T> = {
  caption: string;
  columns: Column<T>[];
  rows: T[] | null;
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  sort: string;
  order: "asc" | "desc";
  // порядок передаётся явно, если его выбрал пользователь; иначе вызывающий переключает сам
  onSort: (sortKey: string, order?: "asc" | "desc") => void;
  onPage: (page: number) => void;
  rowKey: (row: T) => string;
  empty: React.ReactNode;
};

/** Общая таблица списков: серверные сортировка/пагинация, состояния loading/empty/error, карточки на мобильном. */
export function DataTable<T>(p: Props<T>) {
  const t = useT();
  if (p.error) return <ErrorState error={p.error} onRetry={p.onRetry} />;
  if (!p.rows) return <Skeleton lines={6} />;

  const pages = Math.max(1, Math.ceil(p.total / p.pageSize));
  const sortable = p.columns.filter((c) => c.sortKey);
  return (
    <section className="card" aria-label={p.caption} aria-busy={p.loading}>
      {sortable.length > 0 && p.rows.length > 0 ? (
        // На узких экранах заголовки таблицы скрыты, поэтому сортировка доступна отдельными элементами.
        <div className="mobile-sort">
          <label>
            <span className="sr-only">{t.list.sortBy}</span>
            <select
              aria-label={t.list.sortBy}
              value={p.sort}
              onChange={(e) => p.onSort(e.target.value, p.order)}
            >
              {sortable.map((c) => (
                <option key={c.sortKey} value={c.sortKey}>
                  {t.list.sortBy}: {c.header}
                </option>
              ))}
            </select>
          </label>
          <select
            aria-label={t.list.order}
            value={p.order}
            onChange={(e) => p.onSort(p.sort, e.target.value as "asc" | "desc")}
          >
            <option value="asc">{t.list.asc}</option>
            <option value="desc">{t.list.desc}</option>
          </select>
        </div>
      ) : null}
      {p.rows.length === 0 ? (
        <div className="empty">{p.empty}</div>
      ) : (
        <div className="table-wrap" style={{ opacity: p.loading ? 0.6 : 1 }}>
          <table className="tbl">
            <caption className="sr-only">{p.caption}</caption>
            <thead>
              <tr>
                {p.columns.map((c) => (
                  <th
                    key={c.key}
                    aria-sort={
                      c.sortKey && c.sortKey === p.sort ? (p.order === "asc" ? "ascending" : "descending") : undefined
                    }
                  >
                    {c.sortKey ? (
                      <button className="th-sort" onClick={() => p.onSort(c.sortKey as string)}>
                        {c.header}
                        <span aria-hidden>{c.sortKey === p.sort ? (p.order === "asc" ? " ▲" : " ▼") : ""}</span>
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {p.rows.map((row) => (
                <tr key={p.rowKey(row)}>
                  {p.columns.map((c) => (
                    <td key={c.key} data-label={c.header}>
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pager">
        <span className="muted">
          {t.list.total}: {p.total}
        </span>
        <div className="row-actions">
          <Button variant="secondary" className="btn-sm" disabled={p.page <= 1 || p.loading} onClick={() => p.onPage(p.page - 1)}>
            {t.list.prev}
          </Button>
          <span aria-live="polite">
            {t.list.page} {Math.min(p.page, pages)} {t.list.of} {pages}
          </span>
          <Button variant="secondary" className="btn-sm" disabled={p.page >= pages || p.loading} onClick={() => p.onPage(p.page + 1)}>
            {t.list.next}
          </Button>
        </div>
      </div>
    </section>
  );
}
