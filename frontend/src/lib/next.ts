// Редирект после входа только на внутренние пути (защита от open redirect).
export function safeNext(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null;
  return value;
}
