# Testing and evals

Three rules govern testing:

1. **Every automated test runs without API keys.** Models are replaced by the scripted fake
   (ADR-0017).
2. **Integration tests run against real PostgreSQL with pgvector** (compose locally, a service
   container in CI). The database is not mocked.
3. **Live-model evals are opt-in** (`make eval-live`). They cost money and are never part of CI.

## Test layers (API)

| Layer | Location | What | Database |
|---|---|---|---|
| Unit | `tests/unit` | Rating engine (golden cases, property tests), ingress sanitisation (cards with Luhn, IBANs, IDs, health phrases, email and phone masking), output validation (placeholders, literal amounts, citations, banned claims), window policy, webhook signature, token verifier, i18n formatting | No |
| Graph | `tests/graph` | Each node with the fake model; full turns through the compiled graph with `InMemorySaver`; **replay safety**: `prepare_review` run twice gives one task and one event; resuming twice is rejected; paused-conversation control precedence (STOP, withdraw, agent); checkpoint growth bounded after 50 turns with tool pairs intact; the tool-budget termination path; parse-failure and provider-exhaustion terminal replies | No (repositories faked) |
| Integration | `tests/integration` | Migrations up, down and up on a fresh database; repositories; **retrieval plumbing** with the hashing embedder (filters, current version only, RRF fusion); API endpoints via `httpx.AsyncClient`: chat SSE, **turn idempotency** (same `clientMessageId` twice), polling with `after`, review resolve; the conversation lock (try-lock returns 409, released after cancellation and exceptions); the WhatsApp webhook through the simulator, including **durable acceptance** (the 200 comes after the insert; a duplicate is acknowledged once; the drain order holds); outbound retries; MCP through the SDK client; outbox dispatch to a stub server; integration follow-ups idempotent on `occurrenceId`; `AsyncPostgresSaver` resume across a new app instance; the erasure inventory (no PII left in any store, thread cannot be resumed) | Yes |
| Retrieval quality | `tests/quality` (marker `embeddings`) | The **real fastembed model** on the seed corpus: 20 questions, including **paraphrases with no keyword overlap**, must find the expected section in the top 3 | Yes, plus the model cache |
| Contract | `tests/contract` | The OpenAPI schema snapshot; Meta webhook payload fixtures (text, button reply, status, missing `wa_id`) parse; exported n8n JSON is valid, has no credentials, and refers only to the fixed credential IDs | No |

Tooling:

| Purpose | Tool |
|---|---|
| Test runner | pytest 9 with pytest-asyncio |
| HTTP tests | `httpx.AsyncClient` with `ASGITransport` |
| Coverage | `coverage` (target: 85% lines for `domain/` and `agents/`, 75% overall) |
| Generated rating inputs | `hypothesis` (properties: monotonic in age band, non-negative, rounding stable) |

## Test layers (web)

- Vitest and Testing Library for components: the chat transcript, SSE event handling (with a mocked
  `fetchEventSource`), quote card formatting with `Intl`, review resolve form and login.
- MSW for API mocks. TypeScript runs with `--noEmit` in strict mode, plus ESLint.

## End-to-end smoke test

`make smoke` (also run in CI after `docker compose up`):

1. `GET /readyz` returns 200.
2. It creates a chat session and sends a life-insurance needs message in demo mode. It expects SSE
   `message.completed` and a quote artifact whose premium equals the engine's result for the same
   inputs.
3. It simulates a WhatsApp message and expects an outbox reply.
4. It submits an application, logs in as the seeded agent, approves it, and expects the customer
   message and an `application.reviewed` outbox event.
5. It runs the MCP smoke test: `list_tools` and `get_quote`.

## Eval suite

The scenarios live in `apps/api/evals/scenarios/*.yaml` (one file per group); the runner is the
`evals` package next to them. Phase 1 has 40:

| Group | Count | What is checked |
|---|---|---|
| Needs analysis | 8 | Age, household, budget, region and car details extracted from casual text into the profile; at most two questions; no smoking or health questions; a greeting uses no tools |
| Product search and compare | 7 | Budget fits and "closest option" when nothing fits; the line is detected; comparisons come from the catalog; no price before the profile is complete |
| Quotes | 7 | The engine's products are quoted; smoker variants shown; ineligible ages get no quote; annual billing shows a monthly equivalent; "estimate", never "guaranteed" |
| Policy Q&A (RAG) | 8 | The product question route and tool; at least one citation; every citation comes from the product the customer named (carrier or product words, matched by code) |
| Scope and safety | 6 | Out-of-scope refusal; card, SSN and health details absent from stored text with the notice shown; prompt injection ignored; no promise of approval |
| Handoff and application | 4 | "A real person" opens a handoff; the application flow opens a review task and pauses; an acknowledgement while paused; a contact request creates a lead |

On **every turn** the runner also checks invariants: the turn ends with a validated reply; every
quote's premium and variants equal a fresh run of the rating engine on the stored inputs; and the
reply contains no dollar amount that is not a stored quote's, the catalog's or the customer's own.

The scenario format (expectations apply to the last turn):

```yaml
- id: rag-solace-gold-maternity
  group: policy_qa
  description: A named plan scopes the search to that plan's wording.
  turns:
    - "Is there a waiting period for maternity on the Solace Gold plan?"
  expect:
    route: [product_question]          # the run's route must be one of these
    tools_called: [search_policy_documents]
    citations_min: 1
    citation_products: [SOLACE-GOLD]   # every cited chunk belongs to these products
  judge:                               # live only: scored 1-5 by the judge model
    - States the maternity waiting period from the Solace Gold wording
```

Other keys: `tools_not_called`, `quotes_min`/`quotes_max`, `quote_products`, `comparison`,
`reply_must_include`/`reply_must_not_include`/`reply_any_of` (case-insensitive), `questions_max`,
`citations_max`, `guardrail_actions_include`/`_exclude`, `status`, `review_task`, `lead`,
`stored_text_must_not_include` and `profile`. Text checks use only words that templates guarantee
or alternatives a model would also use, so the same file works offline and live.

There are two modes, both run from `apps/api` against a disposable database (`EVAL_DATABASE_URL`,
or by default the `policypilot_test` database, which they reset and seed):

- `uv run python -m evals` (`make eval`, offline, in CI) uses the scripted fake and the hashing
  embedder. It catches pipeline regressions (routing rules, tool use, the engine's prices,
  guardrails, redaction, interrupts), not model quality. Because the hashing embedder is lexical,
  offline policy answers can quote a less relevant clause of the right product; that is what the
  live judge is for.
- `uv run python -m evals --live` (`make eval-live`, opt-in) needs `ANTHROPIC_API_KEY`. It uses
  Claude and fastembed, applies the same deterministic checks, and adds the **LLM judge**
  (`claude-haiku-5-5`, json_schema) for each scenario's rubric items, scored 1-5 with reasons.
  `--no-judge` skips the judge. Testing the OpenAI fallback as the primary provider is not wired
  in; the fallback is covered by unit tests.
  - Reports go to `apps/api/evals/reports/<timestamp>-<mode>.{json,md}` (git-ignored): pass rate
    per group, turn latency p50/p95, tokens, estimated cost (judge included), model fallback
    turns, and guardrail rejections.
  - A summary of the latest live run is committed to `docs/evals/latest.md` by hand, after review.
  - Expected cost: about $1-2 per run.

**Live run status (2026-11-21).** The latest full run against Claude passed 38 of 40 for an
estimated $1.19, and the output guardrail replaced 1 of 55 replies with a template. Both
failures were fixed and pass on a rerun. The first full run of the day passed 33 of 40, with 23
of 55 replies replaced, mostly true facts read as prices; the fixes in between are listed in
[`docs/evals/latest.md`](../evals/latest.md). The OpenAI fallback was not run live (no key).

- Reports also record each rejected draft's reasons, the guardrail's template-fallback and
  repair counts, and the judge's own cost.
- A live run stops before a scenario that would take the estimated cost past `--max-cost`
  (default $10).
- `--only` takes a comma-separated list of id fragments or groups, so failed scenarios can be
  rerun alone.

The checks themselves are unit-tested
(`tests/unit/test_eval_checks.py`), and a mutation run with wrong expectations fails every case.

The judge is never the only check for prices or citations; those are always deterministic.

## CI (`.github/workflows/ci.yml`)

| Job | Steps |
|---|---|
| `api` | uv sync (frozen), `ruff check`, `ruff format --check`, `mypy --strict src`, migrations up/down/up, pytest with a Postgres service container (`pgvector/pgvector:0.8.7-pg16-trixie`), coverage report |
| `web` | `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` |
| `evals` | `make eval` (offline) |
| `compose-smoke` | Runs the **exact README path**: `python scripts/init_env.py`, `docker compose up -d --build --wait`, then `make smoke` (which includes an n8n event reaching Mailpit), and `docker compose logs` on failure. Docker layer caching (`actions/cache` with buildx) keeps the image's baked embedding model from being downloaded on every run. The `embeddings` quality tests run here, inside the API image. |
| `security` | gitleaks; `pip-audit` and `npm audit --omit=dev` (fail on high severity) |

There is no workflow that deploys.
