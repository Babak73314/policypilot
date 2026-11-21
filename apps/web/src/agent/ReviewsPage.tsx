import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import type { Page, ReviewTaskSummary } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { PageHeader, PausedNote, QueryState, StatusBadge, usePolledQuery } from "./ui";

const STATUSES = ["", "open", "claimed", "resolving", "resolved"] as const;
const KINDS = ["", "application_review", "handoff"] as const;

export function ReviewsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.reviews"));
  const fmt = useFormatters();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const kind = params.get("kind") ?? "";
  const { data, isPending, error, polling } = usePolledQuery<Page<ReviewTaskSummary>>(
    ["reviews"],
    "/api/v1/agent/review-tasks",
    { status: status || undefined, kind: kind || undefined },
  );

  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader title={t("agent.reviews.heading")}>
        <select
          className="input w-auto"
          aria-label={t("agent.reviews.filterStatus")}
          value={status}
          onChange={(event) => {
            setFilter("status", event.target.value);
          }}
        >
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {value ? t(`status.${value}`) : t("agent.allStatuses")}
            </option>
          ))}
        </select>
        <select
          className="input w-auto"
          aria-label={t("agent.reviews.filterKind")}
          value={kind}
          onChange={(event) => {
            setFilter("kind", event.target.value);
          }}
        >
          {KINDS.map((value) => (
            <option key={value} value={value}>
              {value ? t(`agent.kind.${value}`) : t("agent.allKinds")}
            </option>
          ))}
        </select>
      </PageHeader>
      <PausedNote idle={polling.idle} onResume={polling.resume} />
      <QueryState isPending={isPending} error={error} />
      {data?.items.length === 0 && <p className="text-sm text-slate-500">{t("agent.reviews.empty")}</p>}
      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("agent.reviews.kind")}</th>
                <th>{t("agent.reviews.summary")}</th>
                <th>{t("agent.channel")}</th>
                <th>{t("agent.status")}</th>
                <th>{t("agent.reviews.claimedBy")}</th>
                <th>{t("agent.created")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((task) => (
                <tr key={task.id} className={task.priority > 0 ? "bg-rose-50/60" : undefined}>
                  <td className="whitespace-nowrap">
                    <Link className="font-medium text-brand-700 hover:underline" to={`/agent/reviews/${task.id}`}>
                      {t(`agent.kind.${task.kind}`)}
                    </Link>
                    {task.priority > 0 && (
                      <span className="ml-2 text-xs font-semibold text-rose-700">{t("agent.reviews.priority")}</span>
                    )}
                  </td>
                  <td className="max-w-md text-slate-700">{task.summary ?? "—"}</td>
                  <td>{t(`agent.channels.${task.channel}`)}</td>
                  <td>
                    <StatusBadge value={task.status} />
                  </td>
                  <td>{task.claimedBy?.displayName ?? "—"}</td>
                  <td className="whitespace-nowrap text-slate-500">{fmt.dateTime(task.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
