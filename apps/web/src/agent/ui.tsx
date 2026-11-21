import { useQuery, type QueryKey } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cx } from "../lib/cx";
import { humanizeKey } from "../lib/humanize";
import { useFormatters } from "../lib/useFormatters";
import { useVisibilityPolling } from "../lib/useVisibilityPolling";
import { useAuth } from "./auth";

/** api.md: the dashboard polls every 15 s, only while visible, and pauses after 15 min idle. */
export const DASHBOARD_POLL_MS = 15_000;

/** An authenticated GET that refreshes on the dashboard's polling rules. */
export function usePolledQuery<T>(key: QueryKey, path: string, query?: Record<string, string | undefined>) {
  const { call } = useAuth();
  const polling = useVisibilityPolling({ intervalMs: DASHBOARD_POLL_MS });
  const result = useQuery({
    queryKey: [...key, query],
    queryFn: () => call<T>(path, query ? { query } : {}),
    refetchInterval: polling.refetchInterval,
  });
  return { ...result, polling };
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-2xl font-semibold text-brand-900">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

export function PausedNote({ idle, onResume }: { idle: boolean; onResume: () => void }) {
  const { t } = useTranslation();
  if (!idle) return null;
  return (
    <p className="mb-4 rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-600">
      {t("agent.pollingPaused")}{" "}
      <button type="button" className="text-brand-700 underline" onClick={onResume}>
        {t("agent.refreshNow")}
      </button>
    </p>
  );
}

const TONES: Record<string, string> = {
  open: "bg-amber-100 text-amber-800",
  claimed: "bg-sky-100 text-sky-800",
  resolving: "bg-violet-100 text-violet-800",
  resolved: "bg-slate-100 text-slate-700",
  active: "bg-emerald-100 text-emerald-800",
  awaiting_review: "bg-amber-100 text-amber-800",
  human_takeover: "bg-rose-100 text-rose-800",
  closed: "bg-slate-100 text-slate-700",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-rose-100 text-rose-800",
  pending_review: "bg-amber-100 text-amber-800",
  ok: "bg-emerald-100 text-emerald-800",
  error: "bg-rose-100 text-rose-800",
};

/** A coloured status pill; the label comes from `status.<value>` with a readable fallback. */
export function StatusBadge({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <span
      className={cx(
        "inline-block rounded-full px-2 py-0.5 text-xs font-medium",
        TONES[value] ?? "bg-slate-100 text-slate-700",
      )}
    >
      {t(`status.${value}`, { defaultValue: humanizeKey(value) })}
    </span>
  );
}

/**
 * Server-provided records (snapshot data, profiles) rendered as a definition list. Keys ending in
 * `_minor` / `Minor` hold money in minor units (the API's convention) and are shown as money.
 */
export function KeyValues({ data, currency = "USD" }: { data: Record<string, unknown>; currency?: string }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const entries = Object.entries(data).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (entries.length === 0) return <p className="text-sm text-slate-500">{t("agent.nothingRecorded")}</p>;
  return (
    <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {entries.map(([key, value]) => {
        const money = MINOR_SUFFIX.test(key) && typeof value === "number";
        return (
          <div key={key} className="contents">
            <dt className="text-slate-500">{humanizeKey(money ? key.replace(MINOR_SUFFIX, "") : key)}</dt>
            <dd className="break-words text-slate-900">
              {money ? fmt.money({ amountMinor: value, currency }) : formatValue(value)}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

const MINOR_SUFFIX = /_?[mM]inor$/;

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

export function QueryState({ isPending, error }: { isPending: boolean; error: unknown }) {
  const { t } = useTranslation();
  if (isPending) return <p className="text-sm text-slate-500">{t("common.loading")}</p>;
  if (error) {
    return (
      <p className="text-sm text-rose-700" role="alert">
        {t("agent.loadFailed")}
      </p>
    );
  }
  return null;
}

export function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cx("card p-4", className)}>
      <h2 className="mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase">{title}</h2>
      {children}
    </section>
  );
}
