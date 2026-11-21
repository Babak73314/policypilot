import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { Lead, LeadStatus, Page } from "../lib/api-types";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useAuth } from "./auth";
import { PageHeader, PausedNote, QueryState, usePolledQuery } from "./ui";

const LEAD_STATUSES: LeadStatus[] = ["new", "contacted", "qualified", "won", "lost"];

export function LeadsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.nav.leads"));
  const fmt = useFormatters();
  const { call } = useAuth();
  const queryClient = useQueryClient();
  const { data, isPending, error, polling } = usePolledQuery<Page<Lead>>(["leads"], "/api/v1/agent/leads");

  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: LeadStatus }) =>
      call<Lead>(`/api/v1/agent/leads/${id}`, { method: "PATCH", body: { status } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["leads"] }),
  });

  return (
    <>
      <PageHeader title={t("agent.leads.heading")} />
      <PausedNote idle={polling.idle} onResume={polling.resume} />
      <QueryState isPending={isPending} error={error} />
      {update.isError && (
        <p className="mb-3 text-sm text-rose-700" role="alert">
          {t("agent.leads.updateFailed")}
        </p>
      )}
      {data?.items.length === 0 && <p className="text-sm text-slate-500">{t("agent.leads.empty")}</p>}
      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("agent.leads.customer")}</th>
                <th>{t("agent.leads.kind")}</th>
                <th>{t("agent.leads.interest")}</th>
                <th>{t("agent.leads.source")}</th>
                <th>{t("agent.leads.nextFollowUp")}</th>
                <th>{t("agent.status")}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    {lead.customerDisplayName ?? t("agent.conversations.anonymous")}
                    {lead.contact.email && <span className="block text-xs">{lead.contact.email}</span>}
                    {lead.contact.phone && <span className="block text-xs">{lead.contact.phone}</span>}
                    <span className="block text-xs text-slate-400">
                      {lead.consent.at
                        ? t("agent.leads.consentGiven", { date: fmt.dateTime(lead.consent.at) })
                        : t("agent.leads.noConsent")}
                    </span>
                  </td>
                  <td>{t(`agent.leads.kinds.${lead.kind}`, { defaultValue: lead.kind })}</td>
                  <td>
                    {lead.lineInterest.map((line) => t(`lines.${line}`, { defaultValue: line })).join(", ") || "—"}
                  </td>
                  <td>{lead.source}</td>
                  <td className="whitespace-nowrap">{lead.nextFollowUpAt ? fmt.dateTime(lead.nextFollowUpAt) : "—"}</td>
                  <td>
                    <select
                      className="input w-auto py-1"
                      aria-label={t("agent.leads.changeStatus")}
                      value={lead.status}
                      disabled={update.isPending}
                      onChange={(event) => {
                        update.mutate({ id: lead.id, status: event.target.value as LeadStatus });
                      }}
                    >
                      {LEAD_STATUSES.map((status) => (
                        <option key={status} value={status}>
                          {t(`status.${status}`)}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
