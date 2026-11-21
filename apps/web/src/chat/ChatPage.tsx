import { useEffect, useRef, useState, type KeyboardEvent, type SubmitEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { ComparisonTable } from "../components/ComparisonTable";
import { QuoteCard } from "../components/QuoteCard";
import { RichText } from "../components/RichText";
import type { Artifact, ChatMessage, Citation, MessageAction } from "../lib/api-types";
import { cx } from "../lib/cx";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useChat, type PendingTurn } from "./useChat";

export const MAX_MESSAGE_CHARS = 2000;

export function ChatPage() {
  const { t, i18n } = useTranslation();
  useDocumentTitle(t("chat.pageTitle"));
  const { state, send, retry, dismiss, startOver, polling } = useChat(i18n.resolvedLanguage ?? "en");
  const bottomRef = useRef<HTMLDivElement>(null);

  const messageCount = state.messages.length;
  const draftLength = state.pending?.draft.length ?? 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messageCount, draftLength, state.pending?.artifacts.length]);

  const lastAssistant = [...state.messages].reverse().find((m) => m.role !== "customer");
  const quickReplies = !state.pending && state.status === "active" ? (lastAssistant?.actions ?? []) : [];

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="h-8 w-8" />
            <div>
              <h1 className="font-semibold text-brand-900">{t("app.name")}</h1>
              <p className="text-xs text-slate-500">{t("chat.subtitle")}</p>
            </div>
          </div>
          <nav className="flex items-center gap-2 text-sm">
            <Link className="text-slate-600 hover:text-brand-700" to="/privacy">
              {t("nav.privacy")}
            </Link>
            <button type="button" className="btn-secondary" onClick={startOver} disabled={state.pending !== null}>
              {t("chat.startOver")}
            </button>
          </nav>
        </div>
      </header>

      {state.demoMode && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900" role="note">
          {t("chat.demoBanner")}
        </div>
      )}

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4">
        {state.phase === "starting" && <p className="py-10 text-center text-slate-500">{t("chat.starting")}</p>}
        {state.phase === "failed" && (
          <div className="py-10 text-center">
            <p className="text-slate-700">{t("chat.startFailed")}</p>
            <button type="button" className="btn-primary mt-3" onClick={startOver}>
              {t("common.tryAgain")}
            </button>
          </div>
        )}

        <ol className="flex flex-col gap-4 py-6" aria-live="polite" aria-label={t("chat.transcript")}>
          {state.messages.map((message) => (
            <MessageItem key={message.id} message={message} />
          ))}
          {state.pending && <PendingItem turn={state.pending} onRetry={retry} onDismiss={dismiss} />}
        </ol>

        {state.status === "awaiting_review" && (
          <StatusNote text={t("chat.awaitingReview")} idle={polling.idle} onResume={polling.resume} />
        )}
        {state.status === "human_takeover" && (
          <StatusNote text={t("chat.humanTakeover")} idle={polling.idle} onResume={polling.resume} />
        )}
        {state.status === "closed" && <StatusNote text={t("chat.closed")} />}

        {quickReplies.length > 0 && (
          <QuickReplies
            actions={quickReplies}
            onPick={(action) => {
              send(action.label, action.id);
            }}
          />
        )}
        <div ref={bottomRef} className="scroll-mb-36" />
      </main>

      <footer className="sticky bottom-0 border-t border-slate-200 bg-white">
        <Composer
          disabled={state.phase !== "ready" || state.pending !== null || state.status === "closed"}
          onSend={(text) => {
            send(text);
          }}
        />
        <p className="pb-2 text-center text-xs text-slate-400">{t("chat.footerNote")}</p>
      </footer>
    </div>
  );
}

function StatusNote({ text, idle, onResume }: { text: string; idle?: boolean; onResume?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="mb-4 rounded-lg border border-brand-100 bg-brand-50 px-4 py-3 text-sm text-brand-900" role="status">
      <p>{text}</p>
      {idle && onResume && (
        <button type="button" className="mt-2 text-brand-700 underline" onClick={onResume}>
          {t("chat.checkForUpdates")}
        </button>
      )}
    </div>
  );
}

function MessageItem({ message }: { message: ChatMessage }) {
  const { t } = useTranslation();
  const isCustomer = message.role === "customer";
  return (
    <li className={cx("flex flex-col gap-2", isCustomer ? "items-end" : "items-start")}>
      {message.role === "agent" && <span className="text-xs font-medium text-brand-700">{t("chat.fromAgent")}</span>}
      {message.content && (
        <div
          className={cx(
            "max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed",
            isCustomer ? "bg-brand-700 text-white" : "border border-slate-200 bg-white text-slate-800",
          )}
        >
          <RichText text={message.content} />
        </div>
      )}
      <Artifacts artifacts={message.artifacts} />
      {message.citations.length > 0 && <Citations citations={message.citations} />}
    </li>
  );
}

function PendingItem({ turn, onRetry, onDismiss }: { turn: PendingTurn; onRetry: () => void; onDismiss: () => void }) {
  const { t } = useTranslation();
  return (
    <>
      <li className="flex flex-col items-end gap-1">
        <div className="max-w-[85%] rounded-2xl bg-brand-700/80 px-4 py-2.5 text-[15px] text-white">{turn.text}</div>
        {turn.error && (
          <div className="flex items-center gap-3 text-sm text-rose-700" role="alert">
            <span>{t(`chat.errors.${turn.error.code}`, { defaultValue: t("chat.errors.unknown") })}</span>
            {turn.error.retryable && (
              <button type="button" className="font-medium underline" onClick={onRetry}>
                {t("common.retry")}
              </button>
            )}
            <button type="button" className="text-slate-500 underline" onClick={onDismiss}>
              {t("common.dismiss")}
            </button>
          </div>
        )}
      </li>
      {!turn.error && (
        <li className="flex flex-col items-start gap-2">
          <Artifacts artifacts={turn.artifacts} />
          {turn.draft ? (
            <div className="max-w-[85%] rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-[15px] text-slate-800">
              <RichText text={turn.draft} />
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-slate-500" role="status">
              <span className="h-2 w-2 animate-pulse rounded-full bg-brand-600" />
              {turn.statusLabel ?? t("chat.thinking")}
            </p>
          )}
        </li>
      )}
    </>
  );
}

function Artifacts({ artifacts }: { artifacts: Artifact[] }) {
  if (artifacts.length === 0) return null;
  const quotes = artifacts.filter((a) => a.type === "quote");
  const comparisons = artifacts.filter((a) => a.type === "comparison");
  return (
    <div className="flex w-full flex-col gap-3">
      {quotes.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {quotes.map((quote) => (
            <QuoteCard key={quote.quoteId} quote={quote} />
          ))}
        </div>
      )}
      {comparisons.map((comparison) => (
        <ComparisonTable key={comparison.productIds.join(",")} comparison={comparison} />
      ))}
    </div>
  );
}

function Citations({ citations }: { citations: Citation[] }) {
  const { t } = useTranslation();
  return (
    <details className="max-w-[85%] text-xs text-slate-500">
      <summary className="cursor-pointer">{t("chat.sources", { count: citations.length })}</summary>
      <ul className="mt-1 space-y-0.5">
        {citations.map((citation) => (
          <li key={citation.chunkId}>
            {t("chat.citation", { document: citation.documentTitle, section: citation.section, pages: citation.pages })}
          </li>
        ))}
      </ul>
    </details>
  );
}

function QuickReplies({ actions, onPick }: { actions: MessageAction[]; onPick: (action: MessageAction) => void }) {
  const { t } = useTranslation();
  return (
    <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label={t("chat.quickReplies")}>
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          className="rounded-full border border-brand-600 px-3 py-1.5 text-sm text-brand-700 hover:bg-brand-50"
          onClick={() => {
            onPick(action);
          }}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}

function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const tooLong = text.length > MAX_MESSAGE_CHARS;

  const submit = (event?: SubmitEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (disabled || tooLong || !text.trim()) return;
    onSend(text);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className="mx-auto flex max-w-3xl items-end gap-2 px-4 py-3" onSubmit={submit}>
      <label htmlFor="chat-input" className="sr-only">
        {t("chat.inputLabel")}
      </label>
      <textarea
        id="chat-input"
        className="input max-h-40 min-h-[44px] resize-y"
        rows={1}
        value={text}
        placeholder={t("chat.placeholder")}
        aria-invalid={tooLong}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      <button type="submit" className="btn-primary h-11" disabled={disabled || tooLong || !text.trim()}>
        {t("chat.send")}
      </button>
      {tooLong && (
        <p className="sr-only" role="alert">
          {t("chat.tooLong", { max: MAX_MESSAGE_CHARS })}
        </p>
      )}
    </form>
  );
}
