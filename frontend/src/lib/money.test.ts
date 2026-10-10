import { describe, expect, it } from "vitest";
import { formatMoney, formatQuantity, normalizeMoneyInput, quantityDiff } from "./money";

const flat = (s: string) => s.replace(/[\s  ]/g, " ");

describe("formatMoney", () => {
  it("группирует разряды и сохраняет знаки как есть, валюта всегда рядом", () => {
    expect(flat(formatMoney("1500.00", "UZS", "ru"))).toBe("1 500,00 UZS");
    expect(formatMoney("1500.00", "UZS", "en")).toBe("1,500.00 UZS");
    expect(formatMoney("1500.00", "UZS", "uz")).toBe("1\u00a0500,00\u00a0UZS");
    expect(formatMoney("1234567.5", "USD", "uz")).toBe("1\u00a0234\u00a0567,5\u00a0USD");
    expect(formatMoney("-1500.00", "UZS", "en")).toBe("-1,500.00\u00a0UZS");
    expect(formatMoney("999.99", "UZS", "en")).toBe("999.99\u00a0UZS");  // без группировки до 1000
    expect(formatMoney("1000", "UZS", "en")).toBe("1,000\u00a0UZS");
  });
  it("не теряет точность на больших значениях (нет float)", () => {
    expect(flat(formatMoney("123456789012345.99", "UZS", "en"))).toBe("123,456,789,012,345.99 UZS");
    expect(formatMoney("9007199254740993.01", "USD", "en")).toBe("9,007,199,254,740,993.01 USD");
  });
  it("целые суммы и нули", () => {
    expect(formatMoney("0.00", "USD", "en")).toBe("0.00 USD");
    expect(formatMoney("5", "USD", "en")).toBe("5 USD");
  });
  it("нераспознанное значение показывается как есть", () => {
    expect(formatMoney("abc", "UZS", "ru")).toBe("abc UZS");
  });
});

describe("normalizeMoneyInput", () => {
  it("принимает запятую и пробелы", () => {
    expect(normalizeMoneyInput("1 500,5")).toBe("1500.5");
    expect(normalizeMoneyInput(" 12.50 ")).toBe("12.50");
  });
});

describe("formatQuantity", () => {
  it("группирует и подставляет разделитель локали без потери знаков", () => {
    expect(flat(formatQuantity("1234567.123456", "ru"))).toBe("1 234 567,123456");
    expect(formatQuantity("1234.5", "en")).toBe("1,234.5");
    expect(flat(formatQuantity("-1000", "uz"))).toBe("-1 000");
    expect(formatQuantity("9007199254740993.000001", "en")).toBe("9,007,199,254,740,993.000001"); // больше 2^53
    expect(formatQuantity("abc", "ru")).toBe("abc");
  });
});

describe("quantityDiff", () => {
  it("точная разница без float", () => {
    expect(quantityDiff("8", "10")).toBe("-2");
    expect(quantityDiff("8", "5")).toBe("3");
    expect(quantityDiff("5", "5")).toBe("0");
    expect(quantityDiff("0.3", "0.1")).toBe("0.2"); // в float было бы 0.19999999999999998
    expect(quantityDiff("1000.000001", "1000")).toBe("0.000001");
    expect(quantityDiff("9007199254740993", "9007199254740992")).toBe("1"); // больше 2^53
    expect(quantityDiff("0", "2.5")).toBe("-2.5");
  });
  it("null для пустого и неверного ввода", () => {
    expect(quantityDiff("", "5")).toBeNull();
    expect(quantityDiff("abc", "5")).toBeNull();
    expect(quantityDiff("1.1234567", "5")).toBeNull();
    expect(quantityDiff("-1", "5")).toBeNull();
  });
});
