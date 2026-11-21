import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import type { ReviewTaskDetail } from "../lib/api-types";
import { jsonResponse, mockFetch, renderWithProviders } from "../test/utils";
import { AuthProvider } from "./auth";
import { ReviewDetailPage } from "./ReviewDetailPage";

const task: ReviewTaskDetail = {
  id: "task-1",
  kind: "application_review",
  status: "open",
  priority: 0,
  summary: "Application for Kestrel Term Life 20 (snapshot v1)",
  conversationId: "conv-1",
  channel: "web",
  applicationId: "app-1",
  createdAt: "2026-11-20T10:00:00Z",
  claimedBy: null,
  snapshot: {
    version: 1,
    data: { name: "Ana Lima", email: "ana@example.test" },
    needsProfile: { age: 40, coverage_goal_minor: 50_000_000 },
    quote: null,
  },
  messagesSinceOpened: [],
  decision: null,
  resolution: null,
  resolvedAt: null,
  whatsappWindowOpen: null,
};

function signIn() {
  sessionStorage.setItem(
    "policypilot.agent.session",
    JSON.stringify({
      token: "agent-jwt",
      expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
      agent: { id: "a1", email: "agent@policypilot.test", displayName: "Demo Agent", role: "admin" },
    }),
  );
}

function renderPage() {
  return renderWithProviders(
    <AuthProvider>
      <Routes>
        <Route path="/agent/reviews/:taskId" element={<ReviewDetailPage />} />
      </Routes>
    </AuthProvider>,
    { route: "/agent/reviews/task-1" },
  );
}

describe("ReviewDetailPage", () => {
  beforeEach(() => {
    sessionStorage.clear();
    signIn();
  });

  it("shows the snapshot with money formatted, and approves with the agent token", async () => {
    let resolveBody: unknown = null;
    const fetchMock = mockFetch([
      ["GET", "/api/v1/agent/review-tasks/task-1", () => jsonResponse(task)],
      [
        "POST",
        "/api/v1/agent/review-tasks/task-1/resolve",
        (_url, init) => {
          resolveBody = typeof init.body === "string" ? JSON.parse(init.body) : null;
          return jsonResponse({
            task: { ...task, status: "resolved", resolution: "approved", resolvedAt: "2026-11-20T10:05:00Z" },
            customerMessage: { content: "Good news" },
          });
        },
      ],
    ]);

    renderPage();
    expect(await screen.findByText("Ana Lima")).toBeInTheDocument();
    expect(screen.getByText("$500,000.00")).toBeInTheDocument(); // coverage_goal_minor as money
    expect(screen.getByText("Coverage goal")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/Note to the customer/), "All in order.");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Decision saved and sent to the customer.")).toBeInTheDocument();
    expect(resolveBody).toEqual({ action: "approve", notes: "All in order." });
    const resolveCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(new Headers(resolveCall?.[1]?.headers).get("Authorization")).toBe("Bearer agent-jwt");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull(); // resolved: no actions
  });

  it("explains a conflict instead of failing silently", async () => {
    mockFetch([
      ["GET", "/api/v1/agent/review-tasks/task-1", () => jsonResponse(task)],
      [
        "POST",
        "/api/v1/agent/review-tasks/task-1/claim",
        () =>
          jsonResponse(
            { status: 409, code: "review_not_claimable", title: "Conflict" },
            { status: 409, headers: { "Content-Type": "application/problem+json" } },
          ),
      ],
    ]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Claim this review" }));
    expect(await screen.findByText("Someone else already claimed or resolved this review.")).toBeInTheDocument();
  });

  it("signs the agent out when the API rejects the token", async () => {
    mockFetch([
      [
        "GET",
        "/api/v1/agent/review-tasks/task-1",
        () => jsonResponse({ status: 401, code: "invalid_token" }, { status: 401 }),
      ],
    ]);
    renderPage();
    await waitFor(() => {
      expect(sessionStorage.getItem("policypilot.agent.session")).toBeNull();
    });
  });
});
