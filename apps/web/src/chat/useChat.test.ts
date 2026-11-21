import { describe, expect, it } from "vitest";
import type { ChatMessage, QuoteArtifact } from "../lib/api-types";
import { ApiError } from "../lib/http";
import { chatReducer, initialChatState, toTurnError, type PendingTurn } from "./useChat";

const message = (id: number, content: string): ChatMessage => ({
  id,
  role: "assistant",
  content,
  createdAt: "2026-11-20T10:00:00Z",
  citations: [],
  artifacts: [],
  actions: [],
});

const turn: PendingTurn = {
  clientMessageId: "c-1",
  text: "hi",
  statusLabel: null,
  draft: "",
  artifacts: [],
  error: null,
};

describe("chatReducer", () => {
  it("streams a pending turn, then hands over to the stored transcript", () => {
    let state = chatReducer(initialChatState, { type: "started", demoMode: true });
    state = chatReducer(state, {
      type: "transcript",
      messages: [message(1, "disclosure")],
      status: "active",
      replace: true,
    });
    state = chatReducer(state, { type: "send", turn });
    state = chatReducer(state, { type: "status", label: "Calculating quotes…" });
    state = chatReducer(state, { type: "artifact", artifact: { type: "quote", quoteId: "q1" } as QuoteArtifact });
    state = chatReducer(state, { type: "delta", text: "Here " });
    state = chatReducer(state, { type: "delta", text: "you go" });
    expect(state.pending).toMatchObject({ statusLabel: "Calculating quotes…", draft: "Here you go" });
    expect(state.pending?.artifacts).toHaveLength(1);

    // The re-read transcript may overlap what the client already has: no duplicates.
    state = chatReducer(state, {
      type: "transcript",
      messages: [message(1, "disclosure"), message(2, "hi"), message(3, "Here you go")],
      status: "active",
      replace: false,
    });
    state = chatReducer(state, { type: "turnDone", status: "awaiting_review" });
    expect(state.messages.map((m) => m.id)).toEqual([1, 2, 3]);
    expect(state.pending).toBeNull();
    expect(state.status).toBe("awaiting_review");
  });

  it("keeps a failed turn so it can be retried with the same clientMessageId", () => {
    let state = chatReducer(initialChatState, { type: "send", turn });
    state = chatReducer(state, { type: "turnFailed", error: { code: "network", message: "", retryable: true } });
    expect(state.pending?.clientMessageId).toBe("c-1");
    expect(state.pending?.error?.code).toBe("network");
  });
});

describe("toTurnError", () => {
  it("maps HTTP answers to customer-facing error codes", () => {
    expect(toTurnError(new ApiError(409, { code: "turn_in_progress" })).code).toBe("turn_in_progress");
    expect(toTurnError(new ApiError(413, null)).retryable).toBe(false);
    expect(toTurnError(new ApiError(429, null)).code).toBe("rate_limited");
    expect(toTurnError(new TypeError("Failed to fetch")).code).toBe("network");
  });
});
