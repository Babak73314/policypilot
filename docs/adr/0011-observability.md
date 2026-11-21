# ADR-0011: Built-in run traces, optional Langfuse

- Status: Accepted
- Date: 2026-11-20

## Context

The agent dashboard should show, for each conversation, what the agent did: route,
nodes, tool calls, models, tokens, latency, fallbacks and guardrail actions. The report
proposed self-hosted Langfuse. Langfuse v4 self-hosting needs Postgres, ClickHouse,
Redis and S3, and its docs recommend at least 4 cores and 16 GiB of RAM. That is too heavy
for the quick start and for a shared low-cost AWS footprint.

## Decision

- **Built-in tracing, always on.** A LangChain callback handler records each graph run into
  `app.agent_runs` and `app.agent_run_steps`. A step is a node, model call, tool call or guardrail.
  Each step records timings, model, provider, token usage, an estimated cost and the outcome.
  Inputs and outputs are stored only as redacted summaries. The dashboard's conversation view
  renders this as a trace.
- **Structured JSON logs** via structlog. Every line carries a request ID, conversation ID and
  run ID. A redaction processor masks emails, phone numbers and card-like or ID-like numbers before
  anything is written.
- **Langfuse is deferred to Phase 2** (review 01, finding 35). A callbacks extension point
  (`callbacks_for(run)`) is kept so that the Langfuse `CallbackHandler`, with conversation session
  IDs and a `mask_otel_spans` redaction hook, can be added without touching the graph. Phase 1 has no
  third-party trace processor.
- Health endpoints: `/healthz` (process) and `/readyz` (database and migrations).

## Consequences

- The quick start shows full traces with no extra services.
- Langfuse, which adds evaluation datasets and prompt management, remains a Phase 2 option.
- Trace tables grow with traffic. A retention command (`policypilot maintenance purge-runs --days 30`)
  trims them.

## Alternatives considered

- **Self-hosted Langfuse in compose:** four extra services and about 16 GiB of RAM recommended.
- **LangSmith:** hosted only, with a smaller free tier.
- **OpenTelemetry collector plus Jaeger:** generic spans with no LLM-specific views.
