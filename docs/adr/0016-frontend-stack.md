# ADR-0016: One React app for the chat, the WhatsApp simulator and the agent dashboard

- Status: Accepted
- Date: 2026-11-20

## Context

Phase 1 needs three web surfaces:

- the customer chat with streaming, quote cards, comparison tables and citations;
- a WhatsApp simulator for local demos;
- the agent dashboard: review queue, conversations with traces, leads and catalog.

React and TypeScript are fixed choices.

## Decision

- Build **one Vite app** (`apps/web`) with React 19 and React Router. Its routes are `/` (chat),
  `/simulator` (WhatsApp simulator), `/privacy` (demo privacy notice) and `/agent/*` (dashboard,
  behind login). The dashboard routes are lazily loaded.
- The app uses these libraries:
  - **TanStack Query** for server state. Polling (dashboard every 15 seconds; chat every 3 seconds
    while a review or takeover is pending; simulator every second) runs **only while the tab is
    visible**, and stops after 15 minutes without user activity (review 01, finding 34);
  - **Tailwind CSS 4** for styling;
  - **@microsoft/fetch-event-source**, which consumes SSE from `POST` endpoints with an
    `Authorization` header. It carries progress, artifacts and the already-validated reply, never
    unvalidated model tokens;
  - **i18next** (ADR-0013).
  The project uses no UI kit and no shadcn generator: a few small components are enough and keep
  the bundle small.
- Production containers serve the static build with nginx (`nginx:1.31-alpine`), which also
  reverse-proxies `/api`, `/webhooks`, `/mcp` and `/dev` to the API. The browser therefore sees one
  origin, and CORS is only needed for the Vite dev server.
- Tests use Vitest and Testing Library, with MSW to mock the API.
- Accessibility: chat messages are in an ARIA live region, everything is keyboard-reachable, and
  colours meet WCAG AA contrast.

## Consequences

- One build, one container and one origin.
- If the customer widget later has to be embedded in third-party sites, it can be split out as a
  separate entry point, because the chat components do not import dashboard code.

## Alternatives considered

- **Next.js:** server rendering adds little to an authenticated dashboard and a chat widget.
- **Two separate apps:** duplicated tooling for little gain.
