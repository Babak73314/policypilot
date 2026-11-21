import { describe, expect, it } from "vitest";
import { formatDate, formatMoney, formatPercent, minorUnitsPerMajor } from "./format";

describe("format", () => {
  it("formats minor units with the currency's exponent", () => {
    expect(minorUnitsPerMajor("USD")).toBe(100);
    expect(minorUnitsPerMajor("JPY")).toBe(1);
    expect(formatMoney({ amountMinor: 7300, currency: "USD" }, "en")).toBe("$73.00");
    expect(formatMoney({ amountMinor: 50_000_000, currency: "USD" }, "en")).toBe("$500,000.00");
    expect(formatMoney({ amountMinor: 1200, currency: "JPY" }, "en")).toBe("¥1,200");
  });

  it("follows the locale (ADR-0013: a second left-to-right language is a resource file away)", () => {
    expect(formatMoney({ amountMinor: 123456, currency: "EUR" }, "de")).toBe("1.234,56 €");
    expect(formatPercent(0.042, "en")).toBe("4.2%");
  });

  it("leaves invalid dates readable instead of printing Invalid Date", () => {
    expect(formatDate("not-a-date", "en")).toBe("not-a-date");
    expect(formatDate("2026-11-20T10:00:00Z", "en")).toBe("Nov 20, 2026");
  });
});
