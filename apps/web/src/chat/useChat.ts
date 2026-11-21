import { useCallback, useEffect, useReducer, useRef } from "react";
import type { Artifact, ChatMessage, ConversationStatus, TurnError } from "../lib/api-types";
import { isApiError, isNetworkError } from "../lib/http";
import { useVisibilityPolling } from "../lib/useVisibilityPolling";
import { createSession, getTranscript, sendTurn, sessionStore, type StoredSession } from "./api";

/** While a human holds the conversation, poll for their reply (api.md: every 3 s, visible only). */
export const PAUSED_POLL_MS = 3000;
/** Upper bound on pages read in one refresh (200 messages each). */
const MAX_TRANSCRIPT_PAGES = 50;

export interface PendingTurn {
  clientMessageId: string;
  text: string;
  action?: string;
  statusLabel: string | null;
  draft: string;
  artifacts: Artifact[];
  error: TurnError | null;
}

export interface ChatState {
  phase: "starting" | "ready" | "failed";
  demoMode: boolean;
  messages: ChatMessage[];
  status: ConversationStatus;
  pending: PendingTurn | null;
}

type Action =
  | { type: "reset" }
  | { type: "started"; demoMode: boolean }
  | { type: "failed" }
  | { type: "transcript"; messages: ChatMessage[]; status: ConversationStatus; replace: boolean }
  | { type: "send"; turn: PendingTurn }
  | { type: "status"; label: string }
  | { type: "artifact"; artifact: Artifact }
  | { type: "delta"; text: string }
  | { type: "turnFailed"; error: TurnError }
  | { type: "turnDone"; status: ConversationStatus | null }
  | { type: "dismiss" };

export const initialChatState: ChatState = {
  phase: "starting",
  demoMode: false,
  messages: [],
  status: "active",
  pending: null,
};

function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const known = new Set(current.map((m) => m.id));
  return [...current, ...incoming.filter((m) => !known.has(m.id))];
}

function updatePending(state: ChatState, patch: (turn: PendingTurn) => PendingTurn): ChatState {
  return state.pending ? { ...state, pending: patch(state.pending) } : state;
}

export function chatReducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "reset":
      return initialChatState;
    case "started":
      return { ...state, phase: "ready", demoMode: action.demoMode };
    case "failed":
      return { ...state, phase: "failed" };
    case "transcript":
      return {
        ...state,
        messages: action.replace ? action.messages : mergeMessages(state.messages, action.messages),
        status: action.status,
      };
    case "send":
      return { ...state, pending: action.turn };
    case "status":
      return updatePending(state, (turn) => ({ ...turn, statusLabel: action.label }));
    case "artifact":
      return updatePending(state, (turn) => ({ ...turn, artifacts: [...turn.artifacts, action.artifact] }));
    case "delta":
      return updatePending(state, (turn) => ({ ...turn, draft: turn.draft + action.text }));
    case "turnFailed":
      return updatePending(state, (turn) => ({ ...turn, error: action.error, statusLabel: null }));
    case "turnDone":
      return { ...state, pending: null, status: action.status ?? state.status };
    case "dismiss":
      return { ...state, pending: null };
  }
}

/** Maps a failed request to the error code shown under the customer's message. */
export function toTurnError(error: unknown): TurnError {
  if (isApiError(error, 409)) return { code: "turn_in_progress", message: "", retryable: true };
  if (isApiError(error, 413)) return { code: "message_too_long", message: "", retryable: false };
  if (isApiError(error, 429)) return { code: "rate_limited", message: "", retryable: true };
  if (isApiError(error)) return { code: error.code ?? "server_error", message: "", retryable: true };
  if (isNetworkError(error)) return { code: "network", message: "", retryable: true };
  return { code: "unknown", message: "", retryable: true };
}

/**
 * The customer chat: starts or resumes a conversation, streams turns and polls while a human
 * holds the conversation. The server stays the source of truth: after each turn the transcript
 * is re-read from the last known message, which also recovers a turn whose stream was cut off.
 */
export function useChat(locale: string) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  // The session and cursor live in refs: async flows read and renew them between renders.
  const sessionRef = useRef<StoredSession | null>(null);
  const lastIdRef = useRef<number | null>(null);
  const startingRef = useRef<Promise<void> | null>(null);
  // Bumped by every (re)start: results of requests from an earlier conversation are dropped.
  const generationRef = useRef(0);

  /** Renews the token, but only for the conversation that issued it. */
  const saveToken = useCallback((conversationId: string, token: string) => {
    const current = sessionRef.current;
    if (current?.conversationId !== conversationId || current.token === token) return;
    sessionRef.current = { ...current, token };
    sessionStore.write(sessionRef.current);
  }, []);

  /** Reads the transcript to the end (pages of 200), returning the new messages. */
  const refresh = useCallback(
    async (replace = false): Promise<ChatMessage[]> => {
      const session = sessionRef.current;
      if (!session) return [];
      const generation = generationRef.current;
      const onToken = (token: string) => {
        saveToken(session.conversationId, token);
      };
      let after = replace ? null : lastIdRef.current;
      const messages: ChatMessage[] = [];
      let status: ConversationStatus = "active";
      for (let page = 0; page < MAX_TRANSCRIPT_PAGES; page += 1) {
        const transcript = await getTranscript(session, after, onToken);
        if (generation !== generationRef.current) return []; // the customer started over
        messages.push(...transcript.messages);
        status = transcript.status;
        after = transcript.nextAfter ?? after;
        if (!transcript.hasMore) break;
      }
      const last = messages.at(-1)?.id;
      if (last !== undefined) lastIdRef.current = Math.max(last, replace ? 0 : (lastIdRef.current ?? 0));
      dispatch({ type: "transcript", messages, status, replace });
      return messages;
    },
    [saveToken],
  );

  const startOnce = useCallback(async () => {
    generationRef.current += 1;
    dispatch({ type: "reset" });
    lastIdRef.current = null;
    try {
      const stored = sessionStore.read();
      if (stored) {
        sessionRef.current = stored;
        try {
          await refresh(true);
          dispatch({ type: "started", demoMode: stored.demoMode });
          return;
        } catch (error) {
          // A revoked or expired token, or a conversation that no longer exists: start over.
          if (!isApiError(error, 401) && !isApiError(error, 403) && !isApiError(error, 404)) throw error;
          sessionStore.clear();
          sessionRef.current = null;
        }
      }
      const created = await createSession(locale);
      sessionRef.current = {
        conversationId: created.conversationId,
        token: created.token,
        demoMode: created.demoMode,
      };
      sessionStore.write(sessionRef.current);
      await refresh(true);
      dispatch({ type: "started", demoMode: created.demoMode });
    } catch {
      dispatch({ type: "failed" });
    }
  }, [locale, refresh]);

  /** One start at a time: StrictMode runs effects twice in development, and a session is server state. */
  const start = useCallback(() => {
    startingRef.current ??= startOnce().finally(() => {
      startingRef.current = null;
    });
    return startingRef.current;
  }, [startOnce]);

  useEffect(() => {
    void start();
  }, [start]);

  const runTurn = useCallback(
    async (turn: PendingTurn) => {
      const session = sessionRef.current;
      if (!session) return;
      const generation = generationRef.current;
      const current = () => generation === generationRef.current;
      dispatch({ type: "send", turn: { ...turn, statusLabel: null, draft: "", artifacts: [], error: null } });
      // Set from stream callbacks; an object so the checks below are not narrowed away.
      const outcome: { completed: ConversationStatus | null; failed: TurnError | null } = {
        completed: null,
        failed: null,
      };
      try {
        await sendTurn(
          session,
          {
            text: turn.text,
            clientMessageId: turn.clientMessageId,
            ...(turn.action ? { action: turn.action } : {}),
          },
          {
            onStatus: (label) => {
              if (current()) dispatch({ type: "status", label });
            },
            onArtifact: (artifact) => {
              if (current()) dispatch({ type: "artifact", artifact });
            },
            onDelta: (text) => {
              if (current()) dispatch({ type: "delta", text });
            },
            onCompleted: (event) => {
              outcome.completed = event.conversationStatus;
              saveToken(session.conversationId, event.token);
            },
            onError: (error) => {
              outcome.failed = error;
            },
          },
        );
      } catch (error) {
        outcome.failed = toTurnError(error);
      }
      if (!current()) return; // the customer started a new conversation meanwhile
      if (outcome.failed) {
        dispatch({ type: "turnFailed", error: outcome.failed });
        return;
      }
      try {
        const fresh = await refresh();
        if (!current()) return;
        // A stream that closed without `message.completed` only counts as done when the stored
        // transcript shows the reply; otherwise the same clientMessageId stays retryable.
        if (outcome.completed === null && !fresh.some((m) => m.role !== "customer")) {
          dispatch({ type: "turnFailed", error: { code: "network", message: "", retryable: true } });
          return;
        }
        dispatch({ type: "turnDone", status: outcome.completed });
      } catch (error) {
        if (current()) dispatch({ type: "turnFailed", error: toTurnError(error) });
      }
    },
    [refresh, saveToken],
  );

  const send = useCallback(
    (text: string, action?: string) => {
      const trimmed = text.trim();
      if (!trimmed || state.pending) return;
      void runTurn({
        clientMessageId: `c-${crypto.randomUUID()}`,
        text: trimmed,
        ...(action ? { action } : {}),
        statusLabel: null,
        draft: "",
        artifacts: [],
        error: null,
      });
    },
    [runTurn, state.pending],
  );

  /** Resends the failed turn with the same clientMessageId, which the API makes idempotent. */
  const retry = useCallback(() => {
    if (state.pending?.error) void runTurn(state.pending);
  }, [runTurn, state.pending]);

  const dismiss = useCallback(() => {
    dispatch({ type: "dismiss" });
    void refresh().catch(() => undefined);
  }, [refresh]);

  const startOver = useCallback(() => {
    sessionStore.clear();
    sessionRef.current = null;
    void start();
  }, [start]);

  const paused = state.status === "awaiting_review" || state.status === "human_takeover";
  const polling = useVisibilityPolling({ enabled: paused && state.phase === "ready", intervalMs: PAUSED_POLL_MS });
  const busy = state.pending !== null;

  useEffect(() => {
    if (!polling.active || busy) return;
    const timer = setInterval(() => {
      void refresh().catch(() => undefined);
    }, PAUSED_POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [polling.active, busy, refresh]);

  return { state, send, retry, dismiss, startOver, polling };
}
