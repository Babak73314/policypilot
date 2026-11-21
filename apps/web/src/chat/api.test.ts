import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/http";
import { jsonResponse, mockFetch, sseResponse } from "../test/utils";
import { sendTurn, type TurnHandlers } from "./api";

const session = { conversationId: "c1", token: "t1", demoMode: true };

function handlers() {
  return {
    onStatus: vi.fn<TurnHandlers["onStatus"]>(),
    onArtifact: vi.fn<TurnHandlers["onArtifact"]>(),
    onDelta: vi.fn<TurnHandlers["onDelta"]>(),
    onCompleted: vi.fn<TurnHandlers["onCompleted"]>(),
    onError: vi.fn<TurnHandlers["onError"]>(),
  };
}

describe("sendTurn", () => {
  it("relays SSE events in order and sends the turn exactly once", async () => {
    const fetchMock = mockFetch([
      [
        "POST",
        "/api/v1/chat/sessions/c1/messages",
        () =>
          sseResponse([
            ["turn.started", { turnId: "x", runId: "y" }],
            ["status", { key: "quoting", label: "Calculating quotes…" }],
            ["artifact.quote", { type: "quote", quoteId: "q1" }],
            ["message.delta", { text: "Hello" }],
            ["message.completed", { messageId: 7, text: "Hello", conversationStatus: "active", token: "t2" }],
          ]),
      ],
    ]);
    const h = handlers();
    await sendTurn(session, { text: "hi", clientMessageId: "c-12345678" }, h);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer t1");
    expect(typeof init?.body === "string" ? JSON.parse(init.body) : null).toEqual({
      text: "hi",
      clientMessageId: "c-12345678",
    });
    expect(h.onStatus).toHaveBeenCalledWith("Calculating quotes…");
    expect(h.onArtifact).toHaveBeenCalledWith({ type: "quote", quoteId: "q1" });
    expect(h.onDelta).toHaveBeenCalledWith("Hello");
    expect(h.onCompleted).toHaveBeenCalledWith(expect.objectContaining({ messageId: 7, token: "t2" }));
  });

  it("throws the API's problem details on 409 and never re-posts", async () => {
    const fetchMock = mockFetch([
      [
        "POST",
        "/api/v1/chat/sessions/c1/messages",
        () =>
          jsonResponse(
            { status: 409, code: "turn_in_progress", title: "Conflict" },
            { status: 409, headers: { "Content-Type": "application/problem+json" } },
          ),
      ],
    ]);
    const error: unknown = await sendTurn(session, { text: "hi", clientMessageId: "c-12345678" }, handlers()).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("turn_in_progress");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces network failures without retrying", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    vi.stubGlobal("fetch", fetchMock);
    await expect(sendTurn(session, { text: "hi", clientMessageId: "c-12345678" }, handlers())).rejects.toBeInstanceOf(
      TypeError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
