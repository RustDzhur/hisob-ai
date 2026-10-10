import { describe, expect, it } from "vitest";
import type { ExtractedField } from "./api";
import { confidenceLevel, lineConfidence, normDecimal, sameValue, suggestionState, valueForForm } from "./suggestions";

const hint = (over: Partial<ExtractedField> = {}): ExtractedField => ({
  path: "number", value: "INV-1", confidence: 0.9, evidence: "No INV-1", validation: "ok", applied: false, ...over,
});

describe("normDecimal / sameValue", () => {
  it("нормализует формы записи одной суммы без float", () => {
    expect(normDecimal("1 250,50")).toBe("1250.5");
    expect(normDecimal("336.00")).toBe("336");
    expect(normDecimal("0.0500")).toBe("0.05");
    expect(normDecimal("abc")).toBe("abc");
    expect(sameValue("decimal", "336.00", "336")).toBe(true);
    expect(sameValue("decimal", "9007199254740993.10", "9007199254740993.1")).toBe(true); // больше 2^53
    expect(sameValue("decimal", "9007199254740993", "9007199254740992")).toBe(false);
  });
  it("текст — без учёта регистра и пробелов по краям, дата — точно", () => {
    expect(sameValue("text", " inv-1 ", "INV-1")).toBe(true);
    expect(sameValue("date", "2026-10-01", "2026-10-02")).toBe(false);
  });
});

describe("suggestionState", () => {
  it("none, same, differs, invalid", () => {
    expect(suggestionState("", undefined, "text")).toBe("none");
    expect(suggestionState("", hint({ value: null }), "text")).toBe("none");
    expect(suggestionState("INV-1", hint(), "text")).toBe("same");
    expect(suggestionState("", hint(), "text")).toBe("differs"); // пустое поле — можно применить
    expect(suggestionState("OTHER", hint(), "text")).toBe("differs");
    expect(suggestionState("", hint({ validation: "invalid" }), "text")).toBe("invalid");
    expect(suggestionState("336", hint({ path: "total_amount", value: "336.00" }), "decimal")).toBe("same");
  });
});

describe("прочее", () => {
  it("уровни уверенности", () => {
    expect(confidenceLevel(0.95)).toBe("high");
    expect(confidenceLevel(0.7)).toBe("medium");
    expect(confidenceLevel(0.3)).toBe("low");
    expect(confidenceLevel(null)).toBe("unknown");
  });
  it("значение для формы: валюта в верхнем регистре, суммы нормализованы", () => {
    expect(valueForForm("text", "currency", "uzs")).toBe("UZS");
    expect(valueForForm("decimal", "total_amount", "336.00")).toBe("336");
    expect(valueForForm("date", "date", "2026-10-01")).toBe("2026-10-01");
  });
  it("минимальная уверенность по строке", () => {
    const f = [hint({ path: "lines.1.amount", confidence: 0.9 }), hint({ path: "lines.1.quantity", confidence: 0.5 }), hint({ path: "lines.2.amount", confidence: 0.99 })];
    expect(lineConfidence(f, 1)).toBe(0.5);
    expect(lineConfidence(f, 3)).toBeNull();
  });
});
