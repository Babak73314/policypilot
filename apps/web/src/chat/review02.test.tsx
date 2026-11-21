import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { ChatMessage } from "../lib/api-types";
import { jsonResponse, mockFetch, renderWithProviders, sseResponse } from "../test/utils";
import { ChatPage } from "./ChatPage";

// Regression tests for docs/reviews/02-phase1-review.md, findings 23, 24 and 25.

const msg = (id: number, role: ChatMessage["role"], content: string): ChatMessage => ({
  id,
  role,
  content,
  createdAt: "2026-11-20T10:00:00Z",
  citations: [],
  artifacts: [],
  actions: [],
});

function storeSession() {
  localStorage.setItem(
    "policypilot.chat.session",
    JSON.stringify({ conversationId: "conv-9", token: "t1", demoMode: false }),
  );
}

describe("chat client after review 02", () => {
  beforeEach(() => {
    localStorage.clear();
    storeSession();
  });

  it("reads every transcript page, so the latest messages show (finding 25)", async () => {
    mockFetch([
      [
        "GET",
        "/api/v1/chat/sessions/conv-9/messages",
        (url) =>
          url.searchParams.get("after") === "2"
            ? jsonResponse({
                status: "active",
                messages: [msg(3, "assistant", "the latest reply")],
                nextAfter: 3,
                hasMore: false,
              })
            : jsonResponse({
                status: "active",
                messages: [msg(1, "assistant", "first"), msg(2, "customer", "second")],
                nextAfter: 2,
                hasMore: true,
              }),
      ],
    ]);
    renderWithProviders(<ChatPage />);
    expect(await screen.findByText("the latest reply")).toBeInTheDocument();
  });

  it("keeps a turn retryable when the stream closes without completing (finding 24)", async () => {
    mockFetch([
      [
        "POST",
        "/api/v1/chat/sessions/conv-9/messages",
        () => sseResponse([["status", { key: "x", label: "Working" }]]),
      ],
      [
        "GET",
        "/api/v1/chat/sessions/conv-9/messages",
        (url) =>
          url.searchParams.has("after")
            ? jsonResponse({ status: "active", messages: [msg(2, "customer", "hello there")], nextAfter: 2 })
            : jsonResponse({ status: "active", messages: [msg(1, "assistant", "Hi!")], nextAfter: 1 }),
      ],
    ]);
    renderWithProviders(<ChatPage />);
    await screen.findByText("Hi!");
    await userEvent.type(screen.getByLabelText("Your message"), "hello there{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Connection lost/);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("disables Start over while a turn is running (finding 23)", async () => {
    mockFetch([
      ["POST", "/api/v1/chat/sessions/conv-9/messages", () => new Promise<Response>(() => undefined)],
      [
        "GET",
        "/api/v1/chat/sessions/conv-9/messages",
        () => jsonResponse({ status: "active", messages: [msg(1, "assistant", "Hi!")], nextAfter: 1 }),
      ],
    ]);
    renderWithProviders(<ChatPage />);
    await screen.findByText("Hi!");
    const startOver = screen.getByRole("button", { name: "Start over" });
    expect(startOver).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Your message"), "hello{Enter}");
    expect(startOver).toBeDisabled();
  });
});
