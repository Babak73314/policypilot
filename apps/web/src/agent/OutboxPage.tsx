import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { OutboxItem, Page } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useAuth } from "./auth";
import { PageHeader, PausedNote, QueryState, usePolledQuery } from "./ui";

/** Events for n8n that are still pending, or that failed every retry (dead letters). */
export function OutboxPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.outbox"));
  const fmt = useFormatters();
  const { call } = useAuth();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<"dead" | "pending">("dead");
  const { data, isPending, error, polling } = usePolledQuery<Page<OutboxItem>>(["outbox"], "/api/v1/agent/outbox", {
    status,
  });

  const retry = useMutation({
    mutationFn: (id: string) => call(`/api/v1/agent/outbox/${id}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["outbox"] }),
  });

  return (
    <>
      <PageHeader title={t("agent.outbox.heading")}>
        <select
          className="input w-auto"
          aria-label={t("agent.status")}
          value={status}
          onChange={(event) => {
            setStatus(event.target.value === "pending" ? "pending" : "dead");
          }}
        >
          <option value="dead">{t("agent.outbox.dead")}</option>
          <option value="pending">{t("agent.outbox.pending")}</option>
        </select>
      </PageHeader>
      <p className="mb-4 max-w-2xl text-sm text-slate-600">{t("agent.outbox.intro")}</p>
      <PausedNote idle={polling.idle} onResume={polling.resume} />
      <QueryState isPending={isPending} error={error} />
      {data?.items.length === 0 && <p className="text-sm text-slate-500">{t("agent.outbox.empty")}</p>}
      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("agent.outbox.event")}</th>
                <th>{t("agent.outbox.attempts")}</th>
                <th>{t("agent.outbox.lastError")}</th>
                <th>{t("agent.created")}</th>
                {status === "dead" && <th />}
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td className="font-mono text-xs">{item.eventType}</td>
                  <td className="tabular-nums">{fmt.number(item.attempts)}</td>
                  <td className="max-w-md truncate text-slate-600" title={item.lastError ?? undefined}>
                    {item.lastError ?? "—"}
                  </td>
                  <td className="whitespace-nowrap text-slate-500">{fmt.dateTime(item.createdAt)}</td>
                  {status === "dead" && (
                    <td>
                      <button
                        type="button"
                        className="btn-secondary py-1"
                        disabled={retry.isPending}
                        onClick={() => {
                          retry.mutate(item.id);
                        }}
                      >
                        {t("common.retry")}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
