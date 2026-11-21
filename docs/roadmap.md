# Roadmap

PolicyPilot is delivered in three phases. Phase 1 is the public portfolio MVP: it runs
locally with Docker Compose and uses fictional data only. Phases 2 and 3 are plans, not
commitments.

## Phase 1: public MVP (local, about 8 weeks for one engineer)

The goal is to show the full agency-copilot loop end to end, with production-grade engineering
and a quick start that needs no API keys.

### Milestones

| # | Milestone | Scope | Done when |
|---|---|---|---|
| M1 | **Foundation** | Monorepo, `docker-compose.yml` (db, migrate, seed, api, web, n8n, mailpit), Alembic schema (catalog, conversations, sales, review, audit, outbox, traces), seed catalog and rate tables, deterministic rating engine with golden tests, CI skeleton | `docker compose up` brings up a healthy API, `policypilot seed` loads 13 products, rating golden tests pass in CI |
| M2 | **Agent core and web chat** | Ingress sanitisation, LLM factory (Claude roles, OpenAI fallback, scripted fake, budget reservation), LangGraph graph (`prepare_turn`, `route`, `needs_analysis`, `small_talk`, `guard_output`, `compact_history`) with `AsyncPostgresSaver`, turn identity and the conversation lock, run tracer, chat session, turn and polling endpoints, React chat page with SSE | A needs-analysis conversation runs in the browser in demo mode, retries are idempotent, and traces are stored |
| M3 | **Products, RAG and quotes** | Policy wordings rendered to PDF, ingestion, hybrid retrieval, advisor tool loop (search, compare, quote, documents), quote and comparison artifacts, price-integrity, citation, banned-claims and disclaimer guardrails | "Compare term life for a 40-year-old with a $60 budget" returns cards whose premiums match the engine, with cited answers to policy questions |
| M4 | **Applications and human review** | Application state machine with consent and snapshots, contact-request leads, `prepare_review` + `human_review` interrupt and resume, paused-thread control rules, handoff, review tasks and reconciliation, agent auth, dashboard (review queue, conversations with traces, leads, catalog and rate tables), web polling for decisions | An application submitted in chat appears in the queue, approval resumes the graph and the customer sees the decision, including after a reload |
| M5 | **Channels and integrations** | WhatsApp webhook (verification, signature, durable acceptance, ordered drain), outbound delivery intents, channel adapter, 24-hour window and templates, simulator page; MCP server (tools, resource, prompt, token auth); outbox dispatcher; n8n unattended bootstrap and workflows (events router, follow-up, digest) with Mailpit | The simulator flow and an MCP `quote_products` both work; n8n emails show up in Mailpit |
| M6 | **Quality and release** | Eval suite (40 scenarios; offline in CI, live opt-in), security pass (gitleaks, audits, rate limits), compose smoke test in CI, docs synced with the code, README with screenshots | Clean-checkout quick start, lint, type checks, tests and offline evals all pass; Codex code review triaged |

### Out of scope for Phase 1

Real carriers, payments, e-signature, claims, renewals, a second language, Cognito or OIDC,
AWS deployment, SQS, OAuth for MCP, the Bedrock fallback, Langfuse export, and an n8n
MCP-client demo. The last three were deferred after review 01 (finding 35).

### Known limitations (deferred to Phase 2)

These were found by the Phase 1 code review ([review 02](reviews/02-phase1-review.md)),
accepted, and deferred to Phase 2 because each needs a redesign rather than a local fix. None of
these deferred items touches price validation, consent, opt-out or personal data. Prices reach a
customer only through quotes the server renders from the rating engine, and every other number in
a reply must be accounted for by the turn's facts before it is sent; the residual limits of that
check are described below.

- **Review recovery across crash windows** (finding 4). A process crash between committing a
  review task, or its resolution, and the LangGraph checkpoint can leave a conversation paused or
  a review that cannot resume. The `reconcile-reviews` job only recovers tasks stuck in
  `resolving`.
- **Replay safety of the application flow** (finding 15). If a node is replayed after a
  checkpoint failure, it can create a duplicate lead, application or snapshot version.
- **Reply and WhatsApp delivery in one transaction** (finding 7, the remaining part). A crash
  between storing an assistant reply and recording its WhatsApp delivery intent can lose that
  WhatsApp message (the transcript still has it; web chat is unaffected).
- **Lock order during review resolution** (finding 9, the remaining part). Resolution collects
  the customer's intervening messages before it takes the conversation lock, so a message that
  arrives in that moment can be missing from the resumed history (it stays in the transcript).
- **Residual limit of number accounting.** Every number in a reply must be a fact of the turn,
  and the words next to it decide what kind of number it is. Two kinds of slip remain possible:
  - *A true figure in a misleading place.* A fact (an age, a count up to 12, a date, a policy
    figure, or a product attribute such as a deductible) passes when no price word sits right
    before it and no period ("a month") follows it. A model could therefore attach one
    product's deductible to another product, or write "Kestrel Term Life is $3,000." when
    $3,000 is a deductible in the turn. In a sentence that also has a price word, a fact still
    passes when the words next to it mark its kind ("31 days", "clause 4.1", "a $6,000
    deductible", "the two prices"), or when the sentence is about a deductible and has no price
    noun.
  - *The budget repeated.* The customer's own budget passes in any sentence about the budget
    that names no product, so "It fits your budget of $80, since it is $80." passes, because
    "it" is not a product name.

  An amount that is not a fact of the turn is always rejected. A fact is rejected when a period
  follows it ("80 a month"), or when a price word sits right before it ("costs 80", "the premium
  is about 80") and no unit or attribute noun is attached ("pay a $6,000 deductible").
- **Some correct replies still fall back to a template.** On the latest full live run (38 of 40
  passed), the guardrail replaced 1 of 55 replies with a fixed template reply, a correct answer
  whose wording it misread ([results](evals/latest.md)). That case is fixed, but other wordings
  can still trip the number check; the customer then gets a safe template instead of the
  model's answer.
- **The OpenAI fallback has not been run against its live API** (no key was set); it is covered
  by unit tests.

## Phase 2: production-shaped (proposal)

- **AWS deployment** starting on a shared Aurora cluster (ADR-0015,
  `docs/deployment/aws.md`): CDK in Python, ECS Express Mode, CloudFront, Secrets
  Manager and GitHub OIDC deploys. This needs the owner's go-ahead for each step.
- **Renewal reminders** over WhatsApp utility templates, plus email.
- **Claims intake (first notice of loss):** a guided flow with photo and document upload to S3,
  with a human handoff.
- **Identity:** Cognito, or another OIDC provider, for agents, and OAuth 2.1 for the MCP server.
- **CRM connector:** for example HubSpot, through n8n, driven by the existing events.
- **A second language (left-to-right),** using the ADR-0013 groundwork.
- **An advanced evaluation panel** in the dashboard (trends, failing scenarios) and optional
  Langfuse export.
- A **Bedrock fallback** once the AWS account has access to the Claude models, and an n8n
  MCP-client demo workflow.
- SQS between the API and its async work, if traffic warrants it.
- **Recovery and concurrency hardening** for the known limitations above (review 02,
  findings 4, 7, 9 and 15), with failure-injection tests at each commit/checkpoint boundary.
- **A live check of the OpenAI fallback**, and live evals in a scheduled CI job with a cost cap.

## Phase 3: with a real client (proposal)

- Carrier APIs and real raters behind the rating-engine port.
- E-signature and payments through providers, never by collecting card data in chat.
- Multi-tenancy for several agencies, with per-tenant catalogs, branding and data isolation.
- Security hardening for regulated production: penetration test, SSO, data residency, DPIAs.
- Standalone infrastructure, or a separate AWS account (`docs/deployment/aws.md`).

## Open decisions for the owner

| # | Decision | Default taken | Where |
|---|---|---|---|
| 1 | Main model for advice and writing: Claude Opus 5.5 or Sonnet 5.5 | Opus 5.5 at low and medium effort; Sonnet is a one-line switch | ADR-0006 |
| 2 | Fallback provider: OpenAI now, or no fallback until Bedrock opens the Claude models to the account | OpenAI (needs an OpenAI key and billing) | ADR-0007 |
| 3 | AWS compute: ECS Express Mode (about $60 a month) or a single EC2 host with compose (about $36) | Express Mode | `docs/deployment/aws.md` |
| 4 | The PolicyPilot subdomain | `policypilot.<apex>` | `docs/deployment/aws.md` |
| 5 | Whether to connect a real Meta test number for the demo video | Simulator only | whatsapp.md |
