import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { Money } from "./api-types";
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatMicros,
  formatMoney,
  formatNumber,
  formatPercent,
  formatTime,
} from "./format";

/** The formatting helpers bound to the active i18next language. */
export function useFormatters() {
  const { i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;

  return useMemo(
    () => ({
      locale,
      money: (money: Money) => formatMoney(money, locale),
      number: (value: number) => formatNumber(value, locale),
      percent: (ratio: number) => formatPercent(ratio, locale),
      duration: (ms: number) => formatDuration(ms, locale),
      micros: (micros: number, currency = "USD") => formatMicros(micros, currency, locale),
      date: (value: string) => formatDate(value, locale),
      dateTime: (value: string) => formatDateTime(value, locale),
      time: (value: string) => formatTime(value, locale),
    }),
    [locale],
  );
}

export type Formatters = ReturnType<typeof useFormatters>;
