# Observability

The decision is ADR-0011.

## Run traces (always on)

Each graph invocation writes one row to `app.agent_runs` and ordered rows to
`app.agent_run_steps` (see [data-model.md](data-model.md#events-audit-and-traces)).

`RunTracer` is a LangChain `AsyncCallbackHandler` passed in `config["callbacks"]`. It records:

| Step | Data |
|---|---|
| Node start and end | Node name from `metadata.langgraph_node`, duration |
| Model call | Model, provider (`anthropic` \| `openai` \| `fake`), input, output and cache-read tokens from `usage_metadata`, latency, fallback flag |
| Tool call | Tool name, validated input (redacted), result summary, error |
| Guardrail action | Emitted by `guard_output` through `get_stream_writer` and the tracer |

The cost estimate uses a price table in `observability/pricing.py`, taken from
`docs/reference/verified-facts.md` and kept in one place. The dashboard shows it as an estimate.

Steps are buffered in memory during the run and written in one transaction at the end, including
when the run fails. This avoids database round trips during the run. The traces are **not** used
for enforcing limits: the model-call budget is reserved atomically in `llm_usage_counters`
before each attempt (security-and-privacy.md).

### Dashboard trace view

`/agent/conversations/{id}` lists the runs. Each run expands to a timeline:

- `route` → `advisor` (1.8 s)
  - `llm claude-opus-5-5`: 3,104 in (2,048 cached) / 212 out
  - `tool search_products` (12 ms)
  - `tool get_quote` (8 ms)
  - `llm claude-opus-5-5`: 3,880 in / 310 out
- `guard_output` (price integrity: ok)

## Logs

- structlog emits JSON to stdout. Uvicorn access logs are disabled and replaced by one structured
  line per request.
- Every line carries `request_id` (also returned as `X-Request-Id`), plus `conversation_id` and
  `run_id` when known.
- The PII redaction processor runs last (security-and-privacy.md).
- Levels: `INFO` by default, `DEBUG` locally when `LOG_LEVEL=debug`. Debug output still never
  includes prompt text.

## Metrics (Phase 1: from the database)

`GET /api/v1/agent/metrics/summary` computes these from tables, with no metrics stack:

- open reviews and average time to resolve;
- leads and applications by status for today and the last 7 days;
- runs today, error rate, fallback rate, guardrail actions by type;
- model calls against `LLM_DAILY_CALL_LIMIT`;
- WhatsApp outbound messages this month (Meta billing awareness);
- dead-lettered outbox events.

A CloudWatch EMF or OpenTelemetry metrics exporter is planned for the AWS deployment phase.

## Langfuse (deferred to Phase 2)

Phase 1 ships only the built-in tracing. Langfuse export would add a third-party processor and a
compatibility surface, and the docs review recommended deferring it (review 01, finding 35).

The extension point stays: `observability.tracing.callbacks_for(run)` returns the list of
callbacks for a run. In Phase 2 it can add `langfuse.langchain.CallbackHandler` with
`session_id = conversation_id` and a `mask_otel_spans` hook. The Langfuse 5.x SDK needs a Langfuse
v4 server, version 4.59 or later. Self-hosting needs Postgres, ClickHouse, Redis and S3, so it will
never be part of this repository's compose file.

## Health

| Endpoint | Checks |
|---|---|
| `/healthz` | Process only |
| `/readyz` | `SELECT 1`, Alembic head, and the embedding model loaded |
