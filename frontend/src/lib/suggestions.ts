import type { ExtractedField } from "@/lib/api";

export type Kind = "text" | "decimal" | "date";
export type Level = "high" | "medium" | "low" | "unknown";

/** Десятичное число из строки без перехода к float: пробелы убираются, запятая -> точка, хвостовые нули срезаются. */
export function normDecimal(s: string): string {
  const v = s.replace(/[\s ]/g, "").replace(",", ".");
  if (!/^-?\d+(\.\d+)?$/.test(v)) return v;
  const [int, frac = ""] = v.split(".");
  const f = frac.replace(/0+$/, "");
  return f ? `${int}.${f}` : int;
}

export function sameValue(kind: Kind, a: string, b: string): boolean {
  if (kind === "decimal") return normDecimal(a) === normDecimal(b);
  if (kind === "text") return a.trim().toLowerCase() === b.trim().toLowerCase();
  return a.trim() === b.trim();
}

export function confidenceLevel(c: number | null): Level {
  if (c === null) return "unknown";
  if (c >= 0.85) return "high";
  if (c >= 0.6) return "medium";
  return "low";
}

export type SuggestionState = "none" | "same" | "differs" | "invalid";

/** Как показать подсказку AI рядом с полем формы. */
export function suggestionState(current: string, hint: ExtractedField | undefined, kind: Kind): SuggestionState {
  if (!hint || hint.value === null) return "none";
  if (hint.validation === "invalid") return "invalid";
  if (current.trim() !== "" && sameValue(kind, current, hint.value)) return "same";
  return "differs";
}

/** Значение для подстановки в форму из подсказки. */
export function valueForForm(kind: Kind, path: string, value: string): string {
  if (path === "currency") return value.trim().toUpperCase();
  return kind === "decimal" ? normDecimal(value) : value;
}

/** Минимальная уверенность по полям строки (для компактного индикатора), null если полей нет. */
export function lineConfidence(fields: ExtractedField[], position: number): number | null {
  const prefix = `lines.${position}.`;
  const vals = fields.filter((f) => f.path.startsWith(prefix) && f.confidence !== null).map((f) => f.confidence as number);
  return vals.length ? Math.min(...vals) : null;
}
