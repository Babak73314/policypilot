import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { QuoteCard } from "../components/QuoteCard";
import { RichText } from "../components/RichText";
import type { ResolveResponse, ReviewAction, ReviewTaskDetail } from "../lib/api-types";
import { isApiError } from "../lib/http";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useFormatters } from "../lib/useFormatters";
import { useAuth } from "./auth";
import { KeyValues, PageHeader, PausedNote, QueryState, Section, StatusBadge, usePolledQuery } from "./ui";

const APPLICATION_ACTIONS: ReviewAction[] = ["approve", "request_changes", "reject"];
const HANDOFF_ACTIONS: ReviewAction[] = ["return_to_assistant", "close"];
const CLERICAL_FIELDS = ["name", "email", "phone"] as const;

export function ReviewDetailPage() {
  const { t } = useTranslation();
  useDocumentTitle(t("agent.review.pageTitle"));
  const fmt = useFormatters();
  const { taskId = "" } = useParams();
  const { call } = useAuth();
  const queryClient = useQueryClient();
  const query = usePolledQuery<ReviewTaskDetail>(["review", taskId], `/api/v1/agent/review-tasks/${taskId}`);
  const task = query.data;
  const [notes, setNotes] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [reply, setReply] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const done = (next: ReviewTaskDetail) => {
    queryClient.setQueryData(["review", taskId, undefined], next);
    void queryClient.invalidateQueries({ queryKey: ["reviews"] });
  };

  const fail = (error: unknown) => {
    const code = isApiError(error) ? (error.code ?? "") : "";
    setMessage({
      tone: "error",
      text: t(`agent.review.errors.${code}`, { defaultValue: t("agent.review.errors.generic") }),
    });
  };

  const claim = useMutation({
    mutationFn: () => call<ReviewTaskDetail>(`/api/v1/agent/review-tasks/${taskId}/claim`, { method: "POST" }),
    onSuccess: done,
    onError: fail,
  });

  const resolve = useMutation({
    mutationFn: (action: ReviewAction) => {
      const clericalEdits = Object.fromEntries(Object.entries(edits).filter(([, value]) => value.trim()));
      return call<ResolveResponse>(`/api/v1/agent/review-tasks/${taskId}/resolve`, {
        method: "POST",
        body: {
          action,
          notes: notes.trim() || undefined,
          ...(action === "edit_and_approve" ? { clericalEdits } : {}),
        },
      });
    },
    onSuccess: (response) => {
      done(response.task);
      setMessage({ tone: "ok", text: t("agent.review.resolved") });
    },
    onError: fail,
  });

  const retry = useMutation({
    mutationFn: () => call<ResolveResponse>(`/api/v1/agent/review-tasks/${taskId}/retry-resume`, { method: "POST" }),
    onSuccess: (response) => {
      done(response.task);
    },
    onError: fail,
  });

  const sendReply = useMutation({
    mutationFn: () =>
      call(`/api/v1/agent/conversations/${task?.conversationId ?? ""}/messages`, {
        method: "POST",
        body: { text: reply },
      }),
    onSuccess: () => {
      setReply("");
      setMessage({ tone: "ok", text: t("agent.review.replySent") });
      void query.refetch();
    },
    onError: fail,
  });

  if (!task) return <QueryState isPending={query.isPending} error={query.error} />;

  const actionable = task.status === "open" || task.status === "claimed";
  const busy = claim.isPending || resolve.isPending || retry.isPending;
  const actions = task.kind === "application_review" ? APPLICATION_ACTIONS : HANDOFF_ACTIONS;
  const hasEdits = Object.values(edits).some((value) => value.trim());

  return (
    <>
      <Link to="/agent/reviews" className="text-sm text-brand-700 hover:underline">
        {t("agent.review.back")}
      </Link>
      <PageHeader title={t(`agent.kind.${task.kind}`)}>
        <StatusBadge value={task.status} />
        <Link className="btn-secondary" to={`/agent/conversations/${task.conversationId}`}>
          {t("agent.review.openConversation")}
        </Link>
      </PageHeader>
      <PausedNote idle={query.polling.idle} onResume={query.polling.resume} />

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <Section title={t("agent.review.summary")}>
            <p className="text-slate-800">{task.summary ?? "—"}</p>
            <p className="mt-2 text-xs text-slate-500">
              {t("agent.review.meta", {
                channel: t(`agent.channels.${task.channel}`),
                created: fmt.dateTime(task.createdAt),
                claimedBy: task.claimedBy?.displayName ?? t("agent.nobody"),
              })}
            </p>
          </Section>

          {task.snapshot && (
            <Section title={t("agent.review.snapshot", { version: task.snapshot.version })}>
              <div className="grid gap-6 lg:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-sm font-medium">{t("agent.review.applicant")}</h3>
                  <KeyValues data={task.snapshot.data} />
                  <h3 className="mt-4 mb-2 text-sm font-medium">{t("agent.review.needs")}</h3>
                  <KeyValues data={task.snapshot.needsProfile} currency={task.snapshot.quote?.premium.currency} />
                </div>
                {task.snapshot.quote && <QuoteCard quote={task.snapshot.quote} />}
              </div>
            </Section>
          )}

          <Section title={t("agent.review.messages")}>
            {task.messagesSinceOpened.length === 0 ? (
              <p className="text-sm text-slate-500">{t("agent.none")}</p>
            ) : (
              <ol className="space-y-3">
                {task.messagesSinceOpened.map((m) => (
                  <li key={m.id} className="text-sm">
                    <span className="mr-2 text-xs font-semibold text-slate-500 uppercase">
                      {t(`agent.roles.${m.role}`)}
                    </span>
                    <span className="text-xs text-slate-400">{fmt.time(m.createdAt)}</span>
                    <div className="mt-1 text-slate-800">
                      <RichText text={m.content} />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Section>
        </div>

        <div className="space-y-4">
          {message && (
            <p className={message.tone === "ok" ? "text-sm text-emerald-700" : "text-sm text-rose-700"} role="status">
              {message.text}
            </p>
          )}

          {task.status === "open" && (
            <button
              type="button"
              className="btn-secondary w-full"
              disabled={busy}
              onClick={() => {
                claim.mutate();
              }}
            >
              {t("agent.review.claim")}
            </button>
          )}

          {actionable && (
            <Section title={t("agent.review.decide")}>
              <label className="field-label" htmlFor="notes">
                {t("agent.review.notes")}
              </label>
              <textarea
                id="notes"
                className="input min-h-20"
                maxLength={1000}
                value={notes}
                onChange={(event) => {
                  setNotes(event.target.value);
                }}
              />
              {task.kind === "application_review" && (
                <details className="mt-3 text-sm">
                  <summary className="cursor-pointer text-brand-700">{t("agent.review.clerical")}</summary>
                  <p className="mt-1 text-xs text-slate-500">{t("agent.review.clericalHint")}</p>
                  {CLERICAL_FIELDS.map((field) => (
                    <div key={field} className="mt-2">
                      <label className="field-label" htmlFor={`edit-${field}`}>
                        {t(`agent.review.fields.${field}`)}
                      </label>
                      <input
                        id={`edit-${field}`}
                        className="input"
                        value={edits[field] ?? ""}
                        onChange={(event) => {
                          setEdits({ ...edits, [field]: event.target.value });
                        }}
                      />
                    </div>
                  ))}
                </details>
              )}
              <div className="mt-4 flex flex-col gap-2">
                {task.kind === "application_review" && hasEdits ? (
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busy}
                    onClick={() => {
                      resolve.mutate("edit_and_approve");
                    }}
                  >
                    {t("agent.review.actions.edit_and_approve")}
                  </button>
                ) : null}
                {actions.map((action) => (
                  <button
                    key={action}
                    type="button"
                    className={
                      action === "reject" || action === "close"
                        ? "btn-danger"
                        : action === "approve"
                          ? "btn-primary"
                          : "btn-secondary"
                    }
                    disabled={busy || (action === "approve" && hasEdits)}
                    onClick={() => {
                      resolve.mutate(action);
                    }}
                  >
                    {t(`agent.review.actions.${action}`)}
                  </button>
                ))}
              </div>
              {task.kind === "application_review" && (
                <p className="mt-3 text-xs text-slate-500">{t("agent.review.binding")}</p>
              )}
            </Section>
          )}

          {task.kind === "handoff" && actionable && (
            <Section title={t("agent.review.reply")}>
              {task.whatsappWindowOpen === false && (
                <p className="mb-2 text-sm text-amber-700">{t("agent.review.windowClosed")}</p>
              )}
              <textarea
                className="input min-h-24"
                aria-label={t("agent.review.reply")}
                maxLength={2000}
                value={reply}
                onChange={(event) => {
                  setReply(event.target.value);
                }}
              />
              <button
                type="button"
                className="btn-primary mt-2 w-full"
                disabled={!reply.trim() || sendReply.isPending}
                onClick={() => {
                  sendReply.mutate();
                }}
              >
                {t("agent.review.sendReply")}
              </button>
            </Section>
          )}

          {task.status === "resolving" && (
            <Section title={t("agent.review.stuckTitle")}>
              <p className="text-sm text-slate-600">{t("agent.review.stuckBody")}</p>
              <button
                type="button"
                className="btn-secondary mt-3 w-full"
                disabled={busy}
                onClick={() => {
                  retry.mutate();
                }}
              >
                {t("agent.review.retryResume")}
              </button>
            </Section>
          )}

          {task.status === "resolved" && (
            <Section title={t("agent.review.outcome")}>
              <KeyValues
                data={{
                  resolution: task.resolution,
                  resolvedAt: task.resolvedAt ? fmt.dateTime(task.resolvedAt) : null,
                  notes: task.decision?.notes,
                  by: task.decision?.agent_email,
                }}
              />
            </Section>
          )}
        </div>
      </div>
    </>
  );
}
