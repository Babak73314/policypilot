import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, mockFetch, renderWithProviders } from "../test/utils";
import { AuthProvider } from "./auth";
import { LeadsPage } from "./LeadsPage";

describe("LeadsPage", () => {
  it("shows the contact details and consent an agent needs (review 02, finding 27)", async () => {
    sessionStorage.setItem(
      "policypilot.agent.session",
      JSON.stringify({
        token: "agent-jwt",
        expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
        agent: { id: "a1", email: "agent@policypilot.test", displayName: "Demo Agent", role: "admin" },
      }),
    );
    mockFetch([
      [
        "GET",
        "/api/v1/agent/leads",
        () =>
          jsonResponse({
            items: [
              {
                id: "l1",
                kind: "contact_request",
                status: "new",
                lineInterest: ["auto"],
                source: "mcp",
                customerDisplayName: "Lee",
                assignedAgent: null,
                nextFollowUpAt: null,
                createdAt: "2026-11-20T10:00:00Z",
                contact: { email: "lee@example.test", phone: "+14155550100" },
                consent: { at: "2026-11-20T10:00:00Z", source: "mcp", textVersion: "v1" },
                conversationId: null,
              },
            ],
          }),
      ],
    ]);
    renderWithProviders(
      <AuthProvider>
        <LeadsPage />
      </AuthProvider>,
    );
    expect(await screen.findByText("lee@example.test")).toBeInTheDocument();
    expect(screen.getByText("+14155550100")).toBeInTheDocument();
    expect(screen.getByText(/Consent to contact:/)).toBeInTheDocument();
  });
});
