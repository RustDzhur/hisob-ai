import { describe, expect, it } from "vitest";
import { safeNext } from "./next";

describe("safeNext", () => {
  it("пропускает внутренние пути", () => {
    expect(safeNext("/invite/abc")).toBe("/invite/abc");
  });
  it("отбрасывает внешние и подозрительные", () => {
    for (const v of [null, "", "https://evil.example", "//evil.example", "evil", "/\\evil.example"]) {
      expect(safeNext(v)).toBeNull();
    }
  });
});
