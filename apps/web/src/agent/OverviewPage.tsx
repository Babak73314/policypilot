import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import type { MetricsSummary } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { PageHeader, PausedNote, QueryState, Section, StatusBadge, usePolledQuery } from "./ui";

export function OverviewPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.overview"));
  const fmt = useFormatters();
  const { data, isPending, error, polling } = usePolledQuery<MetricsSummary>(
    ["metrics"],
    "/api/v1/agent/metrics/summary",
  );

  return (
    <>
      <PageHeader title={t("agent.overview.heading")} />
      <PausedNote idle={polling.idle} onResume={polling.resume} />
      <QueryState isPending={isPending} error={error} />
      {data && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label={t("agent.overview.openReviews")} value={fmt.number(data.openReviews)} to="/agent/reviews" />
            <Metric
              label={t("agent.overview.avgReview")}
              value={t("agent.overview.minutes", { value: fmt.number(data.avgReviewMinutes) })}
            />
            <Metric label={t("agent.overview.leadsToday")} value={fmt.number(data.leadsToday)} to="/agent/leads" />
            <Metric label={t("agent.overview.runsToday")} value={fmt.number(data.runsToday)} />
            <Metric label={t("agent.overview.errorRate")} value={fmt.percent(data.errorRate)} />
            <Metric label={t("agent.overview.fallbackRate")} value={fmt.percent(data.fallbackRate)} />
            <Metric
              label={t("agent.overview.modelCalls")}
              value={t("agent.overview.ofLimit", {
                value: fmt.number(data.modelCallsToday),
                limit: fmt.number(data.modelCallLimit),
              })}
            />
            <Metric
              label={t("agent.overview.deadLetters")}
              value={fmt.number(data.deadLetteredEvents)}
              to="/agent/outbox"
              warn={data.deadLetteredEvents > 0}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Section title={t("agent.overview.applications")}>
              <Counts counts={data.applicationsByStatus} badge />
            </Section>
            <Section title={t("agent.overview.guardrails")}>
              <Counts counts={data.guardrailActions} />
            </Section>
            <Section title={t("agent.overview.whatsapp")}>
              <p className="text-3xl font-semibold">{fmt.number(data.whatsappOutboundThisMonth)}</p>
              <p className="mt-1 text-sm text-slate-500">{t("agent.overview.whatsappHint")}</p>
            </Section>
          </div>
        </div>
      )}
    </>
  );
}

function Metric({ label, value, to, warn }: { label: string; value: string; to?: string; warn?: boolean }) {
  const body = (
    <div className={warn ? "card border-rose-300 p-4" : "card p-4"}>
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
  return to ? (
    <Link to={to} className="block hover:opacity-90">
      {body}
    </Link>
  ) : (
    body
  );
}

function Counts({ counts, badge }: { counts: Record<string, number>; badge?: boolean }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const entries = Object.entries(counts);
  if (entries.length === 0) return <p className="text-sm text-slate-500">{t("agent.none")}</p>;
  return (
    <ul className="space-y-1.5 text-sm">
      {entries.map(([key, count]) => (
        <li key={key} className="flex items-center justify-between gap-2">
          {badge ? <StatusBadge value={key} /> : <span className="font-mono text-xs">{key}</span>}
          <span className="tabular-nums">{fmt.number(count)}</span>
        </li>
      ))}
    </ul>
  );
}
