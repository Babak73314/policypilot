import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import "../i18n";

export function renderWithProviders(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) },
  });
}

/** A text/event-stream response with the given events, as the chat API sends them. */
export function sseResponse(events: [string, unknown][], init: ResponseInit = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const [event, data] of events) {
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200, ...init, headers: { "Content-Type": "text/event-stream" } });
}

type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>;

/** Stubs fetch with a list of [method, path prefix, handler]; unmatched calls fail the test. */
export function mockFetch(routes: [string, string, Handler][]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const route = routes.find(([m, prefix]) => m === method && url.pathname.startsWith(prefix));
    if (!route) throw new Error(`unexpected ${method} ${url.pathname}`);
    return route[2](url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
