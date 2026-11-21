import type { Money } from "./api-types";

/**
 * Locale-aware formatting (ADR-0013). Every function takes the BCP 47 locale explicitly, which
 * the UI takes from the active i18next language (see `useFormatters`). Formatters are cached,
 * because constructing `Intl` objects is comparatively expensive and lists format many values.
 */

const cache = new Map<string, Intl.NumberFormat | Intl.DateTimeFormat>();

function numberFormat(locale: string, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `n|${locale}|${JSON.stringify(options)}`;
  let format = cache.get(key);
  if (!(format instanceof Intl.NumberFormat)) {
    format = new Intl.NumberFormat(locale, options);
    cache.set(key, format);
  }
  return format;
}

function dateFormat(locale: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `d|${locale}|${JSON.stringify(options)}`;
  let format = cache.get(key);
  if (!(format instanceof Intl.DateTimeFormat)) {
    format = new Intl.DateTimeFormat(locale, options);
    cache.set(key, format);
  }
  return format;
}

/**
 * The number of minor units in one major unit of a currency: 100 for USD and EUR, 1 for JPY.
 * Taken from the currency's ISO 4217 exponent as reported by `Intl`.
 */
export function minorUnitsPerMajor(currency: string): number {
  const digits = numberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  return 10 ** digits;
}

/** Formats `{amountMinor, currency}` as a currency string, e.g. 7500 USD → "$75.00". */
export function formatMoney(money: Money, locale: string): string {
  const amount = money.amountMinor / minorUnitsPerMajor(money.currency);
  return numberFormat(locale, { style: "currency", currency: money.currency }).format(amount);
}

export function formatNumber(value: number, locale: string): string {
  return numberFormat(locale, {}).format(value);
}

/** Formats a ratio (0.042) as a percentage ("4.2%"). */
export function formatPercent(ratio: number, locale: string): string {
  return numberFormat(locale, { style: "percent", maximumFractionDigits: 1 }).format(ratio);
}

/** Formats a duration in milliseconds as "350 ms" or "2.4 s". */
export function formatDuration(ms: number, locale: string): string {
  if (ms < 1000) {
    return numberFormat(locale, { style: "unit", unit: "millisecond", unitDisplay: "short" }).format(Math.round(ms));
  }
  return numberFormat(locale, {
    style: "unit",
    unit: "second",
    unitDisplay: "short",
    maximumFractionDigits: 1,
  }).format(ms / 1000);
}

/** Formats a cost in micro-units of a currency (the API's `costEstimateMicros`). */
export function formatMicros(micros: number, currency: string, locale: string): string {
  return numberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(micros / 1_000_000);
}

function toDate(value: string | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "Nov 20, 2026". Invalid input is returned unchanged rather than shown as "Invalid Date". */
export function formatDate(value: string | Date, locale: string): string {
  const date = toDate(value);
  return date ? dateFormat(locale, { dateStyle: "medium" }).format(date) : String(value);
}

/** "Nov 20, 2026, 3:42 PM". */
export function formatDateTime(value: string | Date, locale: string): string {
  const date = toDate(value);
  return date ? dateFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date) : String(value);
}

/** "3:42 PM". */
export function formatTime(value: string | Date, locale: string): string {
  const date = toDate(value);
  return date ? dateFormat(locale, { timeStyle: "short" }).format(date) : String(value);
}
