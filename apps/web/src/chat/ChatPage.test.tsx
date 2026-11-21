import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { ChatMessage, QuoteArtifact } from "../lib/api-types";
import { jsonResponse, mockFetch, renderWithProviders, sseResponse } from "../test/utils";
import { ChatPage } from "./ChatPage";

const quote: QuoteArtifact = {
  type: "quote",
  quoteId: "q1",
  productId: "p1",
  productName: "Evergreen Term Life 20 Flex",
  carrierName: "Evergreen Mutual",
  premium: { amountMinor: 7300, currency: "USD" },
  billingPeriod: "monthly",
  monthlyEquivalent: { amountMinor: 7300, currency: "USD" },
  variants: [{ key: "non_smoker", label: "Non-smoker", premium: { amountMinor: 7300, currency: "USD" } }],
  breakdown: [],
  disclaimer: "Estimate only, not an offer.",
  expiresAt: "2026-12-20T00:00:00Z",
  overBudget: false,
  rendered: "$73.00 per month (estimate)",
};

const msg = (
  id: number,
  role: ChatMessage["role"],
  content: string,
  extra: Partial<ChatMessage> = {},
): ChatMessage => ({
  id,
  role,
  content,
  createdAt: "2026-11-20T10:00:00Z",
  citations: [],
  artifacts: [],
  actions: [],
  ...extra,
});

describe("ChatPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts a session, sends a turn and shows the stored reply with its quote card", async () => {
    let transcriptCalls = 0;
    const fetchMock = mockFetch([
      [
        "POST",
        "/api/v1/chat/sessions/conv-1/messages",
        () =>
          sseResponse([
            ["status", { key: "quoting", label: "Calculating quotes…" }],
            ["message.completed", { messageId: 3, text: "Here you go", conversationStatus: "active", token: "t2" }],
          ]),
      ],
      [
        "POST",
        "/api/v1/chat/sessions",
        () =>
          jsonResponse(
            {
              conversationId: "conv-1",
              token: "t1",
              locale: "en",
              disclosure: "Hi! I'm an AI assistant.",
              privacyUrl: "/privacy",
              demoMode: true,
            },
            { status: 201 },
          ),
      ],
      [
        "GET",
        "/api/v1/chat/sessions/conv-1/messages",
        (url) => {
          transcriptCalls += 1;
          if (!url.searchParams.has("after")) {
            return jsonResponse({
              status: "active",
              messages: [msg(1, "assistant", "Hi! I'm an AI assistant.")],
              nextAfter: 1,
            });
          }
          expect(url.searchParams.get("after")).toBe("1");
          return jsonResponse(
            {
              status: "active",
              messages: [
                msg(2, "customer", "life cover for a 40-year-old"),
                msg(3, "assistant", "Here you go", { artifacts: [quote], actions: [{ id: "apply", label: "Apply" }] }),
              ],
              nextAfter: 3,
            },
            { headers: { "X-Conversation-Token": "t3" } },
          );
        },
      ],
    ]);

    renderWithProviders(<ChatPage />);
    expect(await screen.findByText("Hi! I'm an AI assistant.")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/Demo mode/);

    await userEvent.type(screen.getByLabelText("Your message"), "life cover for a 40-year-old{Enter}");

    const card = await screen.findByRole("article", { name: "Quote for Evergreen Term Life 20 Flex" });
    expect(within(card).getByTestId("quote-price")).toHaveTextContent("$73.00 per month (estimate)");
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
    expect(transcriptCalls).toBe(2);
    // The renewed token is kept for the next visit.
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem("policypilot.chat.session") ?? "{}")).toMatchObject({ token: "t3" });
    });
    const turnCall = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST" && typeof init.body === "string" && init.body.includes("clientMessageId"),
    );
    expect(turnCall).toBeDefined();
  });

  it("shows the review note and no quick replies while an agent reviews the application", async () => {
    localStorage.setItem(
      "policypilot.chat.session",
      JSON.stringify({ conversationId: "conv-2", token: "t1", demoMode: false }),
    );
    mockFetch([
      [
        "GET",
        "/api/v1/chat/sessions/conv-2/messages",
        () =>
          jsonResponse({
            status: "awaiting_review",
            messages: [msg(5, "assistant", "Sent for review.", { actions: [{ id: "x", label: "Should not show" }] })],
            nextAfter: 5,
          }),
      ],
    ]);
    renderWithProviders(<ChatPage />);
    expect(await screen.findByText(/A licensed agent is reviewing your application/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Should not show" })).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });
});
