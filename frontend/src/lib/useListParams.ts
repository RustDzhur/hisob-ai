"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

export type ListState = {
  page: number;
  q: string;
  sort: string;
  order: "asc" | "desc";
  filters: Record<string, string>;
};

/**
 * Состояние списка хранится в адресной строке (page, q, sort, order, фильтры):
 * ссылка воспроизводит вид списка, кнопка «Назад» работает. Значения по умолчанию в URL не пишутся.
 */
export function useListParams(defaults: {
  sort: string;
  order: "asc" | "desc";
  filters: Record<string, string>;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();

  const state: ListState = useMemo(() => {
    const page = Number(search.get("page"));
    const order = search.get("order");
    const filters: Record<string, string> = {};
    for (const k of Object.keys(defaults.filters)) filters[k] = search.get(k) ?? defaults.filters[k];
    return {
      page: Number.isInteger(page) && page >= 1 ? page : 1,
      q: search.get("q") ?? "",
      sort: search.get("sort") ?? defaults.sort,
      order: order === "asc" || order === "desc" ? order : defaults.order,
      filters,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const update = useCallback(
    (patch: Partial<Omit<ListState, "filters">> & { filters?: Record<string, string> }) => {
      const next: ListState = {
        ...state,
        ...patch,
        filters: { ...state.filters, ...(patch.filters ?? {}) },
        // любое изменение условий возвращает на первую страницу, кроме явной смены страницы
        page: patch.page ?? 1,
      };
      const qs = new URLSearchParams();
      if (next.page > 1) qs.set("page", String(next.page));
      if (next.q) qs.set("q", next.q);
      if (next.sort !== defaults.sort) qs.set("sort", next.sort);
      if (next.order !== defaults.order) qs.set("order", next.order);
      for (const [k, v] of Object.entries(next.filters)) if (v !== defaults.filters[k]) qs.set(k, v);
      const str = qs.toString();
      router.replace(str ? `${pathname}?${str}` : pathname, { scroll: false });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state, pathname, router],
  );

  return { state, update };
}
