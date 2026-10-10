// crypto.randomUUID() доступен только в «безопасном контексте» (https/localhost),
// а getRandomValues — везде, в том числе на стенде по обычному http.
export function newIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
