import type { Locale } from "./index";

type Named = { name_uz?: string | null; name_ru?: string | null; name_en?: string | null };

/** Название на языке интерфейса; если его нет — на любом доступном (приоритет: uz, ru, en). */
export function pickName(item: Named, locale: Locale): string {
  const own = item[`name_${locale}` as const];
  return own || item.name_uz || item.name_ru || item.name_en || "";
}
