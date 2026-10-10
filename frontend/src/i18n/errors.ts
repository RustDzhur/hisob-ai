import { ApiError } from "@/lib/api";
import type { Dict } from "./ru";

/** Текст ошибки на языке интерфейса; для неизвестного кода — сообщение сервера. */
export function errorText(t: Dict, err: ApiError): string {
  return t.errors[err.code] ?? err.message;
}

export function fill(template: string, params: Record<string, unknown> | undefined): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => String(params?.[k] ?? `{${k}}`));
}

/** Ошибки по полям формы: поле -> локализованное сообщение (по details[].code). */
export function fieldErrorMap(t: Dict, err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  const out: Record<string, string> = {};
  for (const d of err.details) {
    const tpl = (d.code && t.fieldErrors[d.code]) || d.message;
    out[d.field] = fill(tpl, d.params);
  }
  return out;
}
