import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { QuoteCard } from "../components/QuoteCard";
import { RichText } from "../components/RichText";
import type { ConversationDetail, RunDetail, RunSummary } from "../lib/api-types";
import { cx } from "../lib/cx";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useAuth } from "./auth";
import { KeyValues, PageHeader, PausedNote, QueryState, Section, StatusBadge, usePolledQuery } from "./ui";

export function ConversationDetailPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.conversation.pageTitle"));
  const fmt = useFormatters();
  const { conversationId = "" } = useParams();
  const query = usePolledQuery<ConversationDetail>(
    ["conversation", conversationId],
    `/api/v1/agent/conversations/${conversationId}`,
  );
  const detail = query.data;
  if (!detail) return <QueryState isPending={query.isPending} error={query.error} />;
  const { conversation } = detail;

  return (
    <>
      <Link to="/agent/conversations" className="text-sm text-brand-700 hover:underline">
        {t("agent.conversation.back")}
      </Link>
      <PageHeader title={conversation.customerDisplayName ?? t("agent.conversations.anonymous")}>
        <span className="text-sm text-slate-500">{t(`agent.channels.${conversation.channel}`)}</span>
        <StatusBadge value={conversation.status} />
      </PageHeader>
      <PausedNote idle={query.polling.idle} onResume={query.polling.resume} />

      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        <Section title={t("agent.conversation.transcript")}>
          <ol className="space-y-4">
            {detail.messages.map((m) => (
              <li
                key={m.id}
                className={cx("rounded-lg p-3 text-sm", m.role === "customer" ? "bg-brand-50" : "bg-slate-50")}
              >
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <span className="font-semibold text-slate-600 uppercase">{t(`agent.roles.${m.role}`)}</span>
                  <span className="text-slate-400">{fmt.dateTime(m.createdAt)}</span>
                </div>
                <RichText text={m.content} />
                {m.citations.length > 0 && (
                  <p className="mt-2 text-xs text-slate-500">
                    {m.citations.map((c) => `${c.documentTitle} ${c.section}`).join(" · ")}
                  </p>
                )}
              </li>
            ))}
          </ol>
        </Section>

        <div className="space-y-4">
          <Section title={t("agent.conversation.profile")}>
            <KeyValues data={detail.profile} />
          </Section>
          {detail.applications.length > 0 && (
            <Section title={t("agent.conversation.applications")}>
              <ul className="space-y-2 text-sm">
                {detail.applications.map((a) => (
                  <li key={a.id} className="flex items-center justify-between gap-2">
                    <span>{a.productName}</span>
                    <StatusBadge value={a.status} />
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {detail.quotes.length > 0 && (
            <Section title={t("agent.conversation.quotes", { count: detail.quotes.length })}>
              <div className="space-y-3">
                {detail.quotes.map((quote) => (
                  <QuoteCard key={quote.quoteId} quote={quote} />
                ))}
              </div>
            </Section>
          )}
          <Section title={t("agent.conversation.runs")}>
            {detail.runs.length === 0 ? (
              <p className="text-sm text-slate-500">{t("agent.none")}</p>
            ) : (
              <ol className="space-y-2">
                {detail.runs.map((run) => (
                  <RunRow key={run.id} run={run} />
                ))}
              </ol>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

/** One agent run; expanding it loads the step trace (nodes, model calls, tools, guardrails). */
function RunRow({ run }: { run: RunSummary }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const { call } = useAuth();
  const [open, setOpen] = useState(false);
  const trace = useQuery({
    queryKey: ["run", run.id],
    queryFn: () => call<RunDetail>(`/api/v1/agent/runs/${run.id}`),
    enabled: open,
    staleTime: Infinity,
  });

  return (
    <li className="rounded-lg border border-slate-200 text-sm">
      <button
        type="button"
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
        }}
      >
        <span className="font-medium">{run.route ?? "—"}</span>
        <StatusBadge value={run.status} />
        {run.fallbackUsed && <span className="text-xs font-medium text-amber-700">{t("agent.run.fallback")}</span>}
        <span className="text-xs text-slate-500">{fmt.time(run.startedAt)}</span>
        {run.durationMs !== null && <span className="text-xs text-slate-500">{fmt.duration(run.durationMs)}</span>}
        <span className="text-xs text-slate-500">
          {t("agent.run.tokens", { input: fmt.number(run.inputTokens), output: fmt.number(run.outputTokens) })}
        </span>
        {run.costEstimateMicros > 0 && (
          <span className="text-xs text-slate-500">{fmt.micros(run.costEstimateMicros)}</span>
        )}
      </button>
      {run.guardrailActions.length > 0 && (
        <p className="px-3 pb-2 text-xs text-violet-700">
          {t("agent.run.guardrails", { actions: run.guardrailActions.join(", ") })}
        </p>
      )}
      {open && (
        <div className="border-t border-slate-100 px-3 py-2">
          <QueryState isPending={trace.isPending} error={trace.error} />
          {trace.data && (
            <ol className="space-y-1">
              {trace.data.steps.map((step) => (
                <li key={step.seq} className="grid grid-cols-[3.5rem_5rem_1fr_auto] gap-2 text-xs">
                  <span className="text-slate-400 tabular-nums">{step.seq}</span>
                  <span className="font-mono text-slate-500">{step.kind}</span>
                  <span className="truncate" title={JSON.stringify(step.summary)}>
                    {step.name}
                    {step.model && <span className="ml-1 text-slate-400">{step.model}</span>}
                  </span>
                  <span className="text-slate-500 tabular-nums">{fmt.duration(step.durationMs)}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </li>
  );
}
