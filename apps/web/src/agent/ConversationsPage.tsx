import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import type { ConversationSummary, Page } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { PageHeader, PausedNote, QueryState, StatusBadge, usePolledQuery } from "./ui";

const STATUSES = ["", "active", "awaiting_review", "human_takeover", "closed"] as const;
const CHANNELS = ["", "web", "whatsapp"] as const;

export function ConversationsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.conversations"));
  const fmt = useFormatters();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const channel = params.get("channel") ?? "";
  const { data, isPending, error, polling } = usePolledQuery<Page<ConversationSummary>>(
    ["conversations"],
    "/api/v1/agent/conversations",
    { status: status || undefined, channel: channel || undefined },
  );

  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader title={t("agent.conversations.heading")}>
        <select
          className="input w-auto"
          aria-label={t("agent.status")}
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
          aria-label={t("agent.channel")}
          value={channel}
          onChange={(event) => {
            setFilter("channel", event.target.value);
          }}
        >
          {CHANNELS.map((value) => (
            <option key={value} value={value}>
              {value ? t(`agent.channels.${value}`) : t("agent.allChannels")}
            </option>
          ))}
        </select>
      </PageHeader>
      <PausedNote idle={polling.idle} onResume={polling.resume} />
      <QueryState isPending={isPending} error={error} />
      {data?.items.length === 0 && <p className="text-sm text-slate-500">{t("agent.conversations.empty")}</p>}
      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("agent.conversations.customer")}</th>
                <th>{t("agent.channel")}</th>
                <th>{t("agent.status")}</th>
                <th>{t("agent.conversations.messages")}</th>
                <th>{t("agent.conversations.lastActivity")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link className="font-medium text-brand-700 hover:underline" to={`/agent/conversations/${c.id}`}>
                      {c.customerDisplayName ?? t("agent.conversations.anonymous")}
                    </Link>
                    <span className="block font-mono text-xs text-slate-400">{c.id.slice(0, 8)}</span>
                  </td>
                  <td>{t(`agent.channels.${c.channel}`)}</td>
                  <td>
                    <StatusBadge value={c.status} />
                  </td>
                  <td className="tabular-nums">{fmt.number(c.messageCount)}</td>
                  <td className="whitespace-nowrap text-slate-500">{fmt.dateTime(c.lastActivityAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
