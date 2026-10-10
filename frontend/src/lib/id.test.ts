import { describe, expect, it } from "vitest";
import { newIdempotencyKey } from "./id";

describe("newIdempotencyKey", () => {
  it("32 hex-символа, подходит под требование сервера 8–200, уникален", () => {
    const a = newIdempotencyKey();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(newIdempotencyKey()).not.toBe(a);
  });
});
