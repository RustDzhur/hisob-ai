import type { Locale } from "@/i18n";

/**
 * Разделители заданы явно, а не берутся из Intl: данные ICU различаются между браузерами
 * (в части сборок нет узбекских числовых данных), а вид суммы в учёте должен быть одинаковым везде.
 */
const SEPARATORS: Record<Locale, { group: string; decimal: string }> = {
  uz: { group: " ", decimal: "," },
  ru: { group: " ", decimal: "," },
  en: { group: ",", decimal: "." },
};

/**
 * Форматирование денег из СТРОКИ сервера без перехода к float: целая часть группируется по разрядам
 * текстовой обработкой, дробная остаётся как есть. Курсов и пересчёта нет — валюта показывается рядом.
 */
export function formatMoney(value: string, currency: string, locale: Locale): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) return `${value} ${currency}`;
  const [, sign, int, frac] = m;
  const { group, decimal } = SEPARATORS[locale];
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, group);
  return `${sign}${grouped}${frac ? decimal + frac : ""} ${currency}`;
}

/** Ввод пользователя -> строка для API: запятая как десятичный разделитель допускается, пробелы убираются. */
export function normalizeMoneyInput(raw: string): string {
  return raw.replace(/[\s ]/g, "").replace(",", ".");
}
