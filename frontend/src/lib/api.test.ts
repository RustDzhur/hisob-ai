import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, fieldErrors } from "./api";

function mockFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fn = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("api()", () => {
  beforeEach(() => {
    document.cookie = "hisob_csrf=tok123";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "hisob_csrf=; max-age=0";
  });

  it("не отправляет CSRF на GET, отправляет на POST вместе с Idempotency-Key", async () => {
    const fn = mockFetch(200, { ok: 1 });
    await api("/x");
    expect(fn.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
    await api("/x", { method: "POST", body: { a: 1 }, idempotencyKey: "key-12345678" });
    const h = fn.mock.calls[1][1].headers;
    expect(h["X-CSRF-Token"]).toBe("tok123");
    expect(h["Idempotency-Key"]).toBe("key-12345678");
    expect(fn.mock.calls[1][0]).toBe("/api/v1/x");
  });

  it("превращает ошибку API в ApiError с кодом и correlation_id", async () => {
    mockFetch(409, {
      code: "email_taken",
      message: "Занято",
      details: [{ field: "email", message: "Email уже используется." }],
      correlation_id: "cid-1",
    });
    const err = (await api("/auth/register", { method: "POST", body: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("email_taken");
    expect(err.correlationId).toBe("cid-1");
    expect(fieldErrors(err)).toEqual({ email: "Email уже используется." });
  });

  it("204 возвращает undefined", async () => {
    mockFetch(204, undefined);
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("сетевая ошибка даёт понятное сообщение", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("failed")));
    const err = (await api("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("network");
    expect(err.message).toMatch(/Нет связи/);
  });
});
