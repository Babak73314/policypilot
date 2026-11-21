import { EventStreamContentType, fetchEventSource } from "@microsoft/fetch-event-source";
import type { Artifact, ChatSession, MessageCompleted, PublicProduct, Transcript, TurnError } from "../lib/api-types";
import { request, toApiError } from "../lib/http";
import { isRecord } from "../lib/guards";
import { readJson, removeItem, writeJson } from "../lib/storage";

const SESSION_KEY = "policypilot.chat.session";

/** What the browser keeps between visits (api.md: the token lives in localStorage). */
export interface StoredSession {
  conversationId: string;
  token: string;
  demoMode: boolean;
}

function isStoredSession(value: unknown): value is StoredSession {
  return (
    isRecord(value) &&
    typeof value.conversationId === "string" &&
    typeof value.token === "string" &&
    typeof value.demoMode === "boolean"
  );
}

export const sessionStore = {
  read: () => readJson("local", SESSION_KEY, isStoredSession),
  write: (session: StoredSession) => writeJson("local", SESSION_KEY, session),
  clear: () => {
    removeItem("local", SESSION_KEY);
  },
};

export function createSession(locale: string): Promise<ChatSession> {
  return request<ChatSession>("/api/v1/chat/sessions", { method: "POST", body: { locale } });
}

/** Reads the transcript; the response renews the conversation token (`X-Conversation-Token`). */
export async function getTranscript(
  session: StoredSession,
  after: number | null,
  onToken: (token: string) => void,
): Promise<Transcript> {
  return request<Transcript>(`/api/v1/chat/sessions/${session.conversationId}/messages`, {
    token: session.token,
    query: { after: after ?? undefined },
    onResponse: (response) => {
      const renewed = response.headers.get("X-Conversation-Token");
      if (response.ok && renewed) onToken(renewed);
    },
  });
}

export function listProducts(): Promise<{ items: PublicProduct[] }> {
  return request("/api/v1/catalog/products");
}

export interface TurnHandlers {
  onStatus: (label: string) => void;
  onArtifact: (artifact: Artifact) => void;
  onDelta: (text: string) => void;
  onCompleted: (event: MessageCompleted) => void;
  onError: (error: TurnError) => void;
}

/** Thrown to stop fetch-event-source from reconnecting: a turn is never re-sent automatically. */
class StopStream extends Error {}

/**
 * Sends one turn and relays its SSE events. Retrying is the caller's choice and reuses the same
 * `clientMessageId`, which the API makes idempotent. Resolves when the stream ends; rejects with
 * an `ApiError` for non-2xx answers (409 busy, 413, 429) or a `TypeError` for network failures.
 */
export async function sendTurn(
  session: StoredSession,
  body: { text: string; clientMessageId: string; action?: string },
  handlers: TurnHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let failure: unknown = null;
  await fetchEventSource(`/api/v1/chat/sessions/${session.conversationId}/messages`, {
    method: "POST",
    headers: {
      Accept: EventStreamContentType,
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.token}`,
    },
    body: JSON.stringify(body),
    signal,
    // The turn keeps running on the server either way; do not re-POST when the tab is hidden.
    openWhenHidden: true,
    async onopen(response) {
      const type = response.headers.get("Content-Type") ?? "";
      if (response.ok && type.startsWith(EventStreamContentType)) return;
      failure = await toApiError(response);
      throw new StopStream();
    },
    onmessage(message) {
      if (!message.event || !message.data) return;
      const data: unknown = JSON.parse(message.data);
      if (!isRecord(data)) return;
      switch (message.event) {
        case "status":
          if (typeof data.label === "string") handlers.onStatus(data.label);
          break;
        case "artifact.quote":
        case "artifact.comparison":
          handlers.onArtifact(data as unknown as Artifact);
          break;
        case "message.delta":
          if (typeof data.text === "string") handlers.onDelta(data.text);
          break;
        case "message.completed":
          handlers.onCompleted(data as unknown as MessageCompleted);
          break;
        case "error":
          handlers.onError(data as unknown as TurnError);
          break;
        default:
          break; // turn.started and future events
      }
    },
    onerror(error: unknown) {
      if (!(error instanceof StopStream)) failure = error;
      throw error; // never retry
    },
  }).catch((error: unknown) => {
    if (!(error instanceof StopStream) && failure === null) failure = error;
  });
  if (failure instanceof Error) throw failure;
  if (failure !== null) throw new Error("the turn stream failed");
}
