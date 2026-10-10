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

/** Количество из СТРОКИ сервера (до 6 знаков): та же группировка и разделители, без валюты и без float. */
export function formatQuantity(value: string, locale: Locale): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!m) return value;
  const [, sign, int, frac] = m;
  const { group, decimal } = SEPARATORS[locale];
  return `${sign}${int.replace(/\B(?=(\d{3})+(?!\d))/g, group)}${frac ? decimal + frac : ""}`;
}

/**
 * Разница «фактически − по учёту» для количеств с точностью до 6 знаков без float: считается в BigInt
 * по масштабу 1e6. null — если пересчёт не введён или записан не как число.
 */
export function quantityDiff(counted: string, expected: string): string | null {
  if (counted === "" || !/^\d+(\.\d{1,6})?$/.test(counted) || !/^-?\d+(\.\d+)?$/.test(expected)) return null;
  const scale = (s: string) => {
    const neg = s.startsWith("-");
    const [i, f = ""] = s.replace("-", "").split(".");
    const n = BigInt(i + f.padEnd(6, "0").slice(0, 6));
    return neg ? -n : n;
  };
  const d = scale(counted) - scale(expected);
  const zero = BigInt(0);
  const sign = d < zero ? "-" : "";
  const abs = (d < zero ? -d : d).toString().padStart(7, "0");
  const frac = abs.slice(-6).replace(/0+$/, "");
  return `${sign}${abs.slice(0, -6)}${frac ? "." + frac : ""}`;
}
