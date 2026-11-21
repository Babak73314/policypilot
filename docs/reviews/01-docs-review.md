# Review 01: Codex review of the Phase 1 design docs

- Date: 2026-11-20
- Reviewer: OpenAI Codex CLI 0.169.1 (`codex exec`, model `gpt-6.1-sol`, reasoning effort high, sandbox `read-only`), run once
- Scope: the design docs at commit `10af9d6` on `phase-1`
- Brief: [`01-docs-review-prompt.md`](01-docs-review-prompt.md)

The raw output is reproduced verbatim below, followed by the triage.

> **Note on file names.** The review mentions `docs/deployment/shared-infrastructure.md`, an
> account-specific analysis kept in the private working repository. Its public counterpart is
> [`docs/deployment/aws.md`](../deployment/aws.md), and the findings that cite it (31, 33, 34)
> were applied to both.

---

## Raw review output

The documentation leaves several important Phase 1 implementation contracts unresolved, especially recovery after partial failure, review resumption, and customer-visible output validation. The largest risks come from interactions between otherwise well-described components: database transactions, LangGraph checkpoints, HTTP streams, and external delivery do not share one atomic boundary. Several privacy and compliance claims are stronger than the documented controls support. This review treats the dated external facts as supplied and identifies problems in the design’s internal consistency and reasoning.

### 1. Review-node replay can duplicate committed side effects
- Severity: High
- File(s): `docs/design/agent-design.md` — Human review, Interrupts and resume; `docs/design/data-model.md` — Human review; `docs/architecture/overview.md` — Flow 3
- Finding: `human_review` creates a review task and outbox event before calling `interrupt()`. An interrupted node restarts on resume, so these writes can run again. The partial uniqueness rule covers “open” tasks, although tasks become `claimed` before resolution. Application changes, review-task status, conversation status and checkpoints also commit separately, leaving crash windows where the dashboard and graph disagree.
- Recommendation: Create review records in a preceding node using a stable operation ID and replay-safe writes. Make the interrupt node free of non-idempotent side effects. Enforce one unresolved task across both `open` and `claimed`, and define recovery for every application/checkpoint/status mismatch.

### 2. WhatsApp acknowledges messages before durable acceptance
- Severity: High
- File(s): `docs/design/whatsapp.md` — Inbound flow; `docs/adr/0009-whatsapp-channel-adapter.md`; `docs/architecture/overview.md` — Flow 2
- Finding: The detailed design returns `200` before any database work and relies on an in-process background task. A process exit after acknowledgement loses the message permanently. Conversely, inserting a receipt before processing can suppress retries after processing fails. ADR-0009 instead says deduplication and message storage happen before acknowledgement. Outbound replies likewise lack a durable send/retry contract.
- Recommendation: For Phase 1, durably accept a validated, sanitized inbox item before returning `200`; return a retryable failure when acceptance fails. Track processing separately from receipt deduplication, recover unfinished items at startup, and persist outbound delivery intentions before sending.

### 3. Output guardrails run after rejected content reaches customers
- Severity: High
- File(s): `docs/architecture/overview.md` — Flow 1; `docs/design/agent-design.md` — Streaming; `docs/design/api.md` — SSE events
- Finding: Unvalidated tokens reach the browser before price, citation and banned-claim checks. `message.replaced` cannot retract content already read, announced by assistive technology or copied. The streaming rules also select `writer` as a node, although writer calls occur inside nodes such as `needs_analysis` and `application`; the stated metadata filter does not match the topology.
- Recommendation: Buffer customer-facing model text until validation succeeds, then stream the validated response. Continue streaming deterministic progress and validated artifacts during generation. Mark customer-facing generations explicitly rather than inferring visibility from model roles or broad node names.

### 4. Guardrail rewriting can introduce unchecked prices and citations
- Severity: High
- File(s): `docs/design/agent-design.md` — Output guardrails
- Finding: Price integrity and citation resolution run before the banned-claim filter invokes the writer to rewrite the reply. That rewrite can introduce a new price, remove or fabricate citations, or otherwise invalidate earlier checks. Only the banned-claim check is explicitly repeated.
- Recommendation: Run every model rewrite before a final deterministic validation pass covering prices, citations, claims, disclaimers and channel limits. Bound repair attempts and use a deterministic safe template when final validation fails.

### 5. Redaction must precede graph entry, not merely be a graph node
- Severity: High
- File(s): `docs/design/agent-design.md` — `guard_input`; `docs/design/security-and-privacy.md` — Input handling; `docs/architecture/overview.md` — Flow 1
- Finding: The guarantee that original sensitive identifiers never reach the database depends primarily on `guard_input`, which runs inside a checkpointed graph. Raw graph input can therefore enter checkpoint persistence before that node redacts it. Paused-thread messages and dashboard replies bypass this graph node entirely, and their equivalent sanitization contract is not specified.
- Recommendation: Define one ingress sanitization service that runs before message storage, checkpointed invocation, callbacks or logging. Apply it to customer messages, agent replies, webhook fields, MCP free text and review notes. Let `guard_input` consume sanitized input and redaction metadata.

### 6. Erasure leaves recoverable personal information in other stores
- Severity: High
- File(s): `docs/design/data-model.md` — Retention; `docs/design/security-and-privacy.md`; `docs/compliance.md` — Data protection
- Finding: Erasure clears customer PII columns and replaces message text, but personal information can remain in checkpoints, application data, lead profiles, channel identities, review summaries, simulator payloads and traces. Resuming an old checkpoint could restore erased information. Retention also depends on `closed_at`, without an ordinary inactivity closure policy, so abandoned conversations can persist indefinitely.
- Recommendation: Specify an erasure/export inventory covering every persisted representation. Serialize erasure against active turns, invalidate access, remove checkpoint history and pending deliveries, and prevent subsequent resume. Add inactivity closure and explicit retention for leads, applications and unresolved conversations.

### 7. Volunteered health data contradicts the minimization claim
- Severity: High
- File(s): `docs/design/security-and-privacy.md` — Input handling; `docs/compliance.md` — Special-category data; `docs/design/whatsapp.md` — Policy guardrails
- Finding: Security explicitly retains volunteered health conditions “for context,” which makes them available to models and checkpoints. Compliance says health data is not collected, while WhatsApp says volunteered health information is redacted. Flagging retained text and referring to a human does not resolve these contradictory processing policies.
- Recommendation: Adopt one explicit policy for volunteered health information. For this demo, suppress those details from persisted conversation/model context and offer a private human channel. Make the compliance wording accurately describe any information that remains processed.

### 8. The real-number tunnel exposes a webhook-signing bypass
- Severity: High
- File(s): `docs/design/api.md` — Local-only routes; `docs/design/whatsapp.md` — Connecting a real Meta test number; `docs/adr/0016-frontend-stack.md`
- Finding: The documented tunnel exposes port 8080 while nginx forwards `/dev`. In `APP_ENV=local`, `/dev/whatsapp/simulate` can manufacture correctly signed messages for a caller-selected identity. No authentication is specified for these routes. With cloud outbound enabled, a remote caller could impersonate a customer and trigger real processing or messages without knowing the Meta secret.
- Recommendation: Disable simulator mutation routes whenever `WHATSAPP_MODE=cloud`, and exclude `/dev` from the public tunnel path. Specify authentication for local simulator operations and bind local administrative surfaces to loopback.

### 9. Approval is not bound to an immutable customer-confirmed application
- Severity: High
- File(s): `docs/design/agent-design.md` — Application and resume; `docs/design/api.md` — Review resolution; `docs/design/data-model.md` — Sales
- Finding: The application flow does not define the immutable draft/version that the customer confirms. Reviewers can supply `edits` and approve in the same operation, but there is no rule for changes that invalidate the quote or require renewed customer confirmation. Ownership checks among customer, conversation, lead, product and quote are not specified, nor is approval behavior for expired quotes. The demands-and-needs record remains in mutable `leads.profile`.
- Recommendation: Bind submission and approval to a versioned application snapshot containing the needs assessment and quote basis. Enforce ownership and product/quote consistency in services. Define which edits require re-rating or customer reconfirmation, and check quote validity before approval.

### 10. The locking protocol does not guarantee the documented concurrency behavior
- Severity: High
- File(s): `docs/design/agent-design.md` — Concurrency; `docs/architecture/overview.md` — Flow 1
- Finding: The design first takes a transaction lock in a short transaction, then holds a session lock during the graph run, without defining an atomic handover. Blocking lock acquisition also does not implement immediate web `409` responses. Concurrent WhatsApp background tasks acquiring locks do not establish a durable arrival order, and pooled session locks require explicit connection ownership and cleanup.
- Recommendation: Specify one lock protocol using a dedicated checked-out connection and nonblocking acquisition for web requests. Serialize turns, review resolution, human replies and closure through it. Use a durable inbound sequence for WhatsApp ordering, and test lock release after cancellation, timeout and process failure.

### 11. Turn rollback and retry semantics conflict with durable checkpoints
- Severity: High
- File(s): `docs/design/api.md` — Streaming turn, `error`; `docs/design/agent-design.md` — Tools and checkpointing; `docs/design/data-model.md` — Messages and quotes
- Finding: SSE errors promise that “the turn is rolled back,” although quote tools persist independently and LangGraph can already have committed checkpoints. `clientMessageId` is accepted but has no documented storage, uniqueness or replay behavior. Browser reconnects or retries can therefore duplicate messages and domain effects, or continue from state inconsistent with the visible transcript.
- Recommendation: Introduce a durable turn identity and lifecycle keyed by conversation and `clientMessageId`. Define whether disconnects cancel or continue execution, how results are recovered, and which effects survive failure. Replace the blanket rollback promise with explicit recovery semantics and idempotent domain operations.

### 12. Monetary integrity checks lose the meaning of an amount
- Severity: High
- File(s): `docs/design/agent-design.md` — Price integrity; `docs/design/whatsapp.md` — Quote-card rendering; `docs/design/catalog-and-rating.md` — Auto
- Finding: Matching an amount against any quote, variant or breakdown line does not prove the amount belongs to the stated product, billing period or coverage. A reply can assign one product’s premium to another and pass. WhatsApp quote rendering explicitly says “per month,” although auto premiums are annual; the displayed monthly equivalent is not represented clearly in the quote contract.
- Recommendation: Render critical monetary claims from structured fields bound to a quote ID, product and period. Represent annual premiums and rounded monthly equivalents separately, with explicit labels. Validate text claims against those bindings rather than a set of permitted numbers.

### 13. AI Act classification is asserted from insufficient premises
- Severity: Medium
- File(s): `docs/adr/0002-agency-copilot-with-human-approval.md`; `docs/compliance.md` — Annex III 5(c); `docs/reference/verified-facts.md` — EU AI Act
- Finding: The documents treat deterministic pricing and human approval as sufficient to establish that the overall system falls outside the high-risk category. Those controls do not, by themselves, establish the classification of a combined system whose model extracts rating inputs, selects products and influences recommendations. The “verified facts” document also mixes verified legal text with this project-specific interpretation.
- Recommendation: Separate the verified provisions and dates from the classification hypothesis. Describe the intended-purpose boundary and model influence explicitly, and mark classification as unresolved for a real deployment rather than established by the presence of deterministic code or a human reviewer.

### 14. Paused conversations have no complete control-message lifecycle
- Severity: Medium
- File(s): `docs/design/agent-design.md` — While paused, Resume; `docs/design/whatsapp.md` — Escalation and STOP; `docs/design/api.md` — Resolve
- Finding: While paused, every customer message receives an acknowledgement without invoking routing. This leaves explicit human requests during application review, withdrawals, corrections and STOP handling without a unified precedence rule. Application-review resume does not describe incorporating intervening customer messages. Handoff resume expects a `transcript`, but the API body omits it.
- Recommendation: Define deterministic control handling before the paused-thread shortcut, including escalation, opt-out and withdrawal. Reconstruct intervening messages server-side from an ordered transcript boundary and attach them on every applicable resume. Specify the resulting task, application and conversation transitions.

### 15. Human replies have no delivery mechanism for web customers
- Severity: Medium
- File(s): `docs/design/api.md` — Customer chat and human replies; `docs/architecture/overview.md` — Human approval; `docs/design/agent-design.md` — Channel delivery
- Finding: Customer SSE connections last for one POST turn. A later review decision or dashboard reply cannot reach that completed connection, yet the approval flow promises the customer receives it. The chat design does not specify polling, a persistent event subscription, unread-message handling or reconnect behavior.
- Recommendation: Choose a Phase 1 delivery mechanism, such as authenticated incremental transcript polling while review is pending. Define message cursors, deduplication and delivery of review decisions and human messages after reload.

### 16. Citation validity is mistaken for answer support
- Severity: Medium
- File(s): `docs/design/agent-design.md` — Citation resolution; `docs/design/testing-and-evals.md` — Policy Q&A
- Finding: A marker is accepted if its chunk was retrieved, regardless of whether that chunk supports the associated claim. A policy answer with no valid citation is still delivered with a disclaimer appended, leaving any invented exclusion or coverage statement intact. The deterministic eval checks cannot establish support merely from citation presence.
- Recommendation: Replace unsupported policy answers with an explicit insufficient-evidence response. Require citations for material policy claims, and add live evaluation cases checking claim-to-source support, wrong-product citations and contradictions with retrieved wording.

### 17. Required profile fields and budget search are undefined
- Severity: Medium
- File(s): `docs/design/agent-design.md` — CustomerProfile and tools; `docs/design/mcp-server.md` — `search_products`; `docs/roadmap.md` — M3
- Finding: The profile says each line declares required fields, but those declarations are absent. `search_products` promises eligible, budget-filtered products using only line, budget, region and attribute keys; it lacks the age, coverage, term or vehicle inputs needed to determine eligibility and price. The MCP example applies a budget before supplying those inputs.
- Recommendation: Publish required-field and validation tables per line. Define search as either non-priced catalog discovery or a profile-aware quote search. Make unknown eligibility explicit and define annual-to-monthly budget comparison consistently across graph and MCP services.

### 18. Rating inputs and factor units are not implementation-ready
- Severity: Medium
- File(s): `docs/design/catalog-and-rating.md` — Rating engine; `docs/design/data-model.md` — Rate tables; `docs/design/mcp-server.md` — Quote inputs
- Finding: The three input models are named but not defined. The life formula uses `coverage / 1000` without stating whether coverage and rates are major or minor units. `max_issue_age` appears both as a scalar and a term-dependent function. Health age bands, auto driver-age bands, and bounds for several inputs are unspecified. Decimal precision in JSON factors and unrounded breakdown representation are also undefined.
- Recommendation: Document complete input and factor schemas, units, bounds and band boundaries. Define canonical decimal encoding, breakdown arithmetic and monthly-equivalent rounding. Include at least one fully worked example per line before implementing golden tests.

### 19. Active rate-table selection can choose expired or unpublished data
- Severity: Medium
- File(s): `docs/design/data-model.md` — `rate_tables`; `docs/adr/0003-deterministic-quote-engine.md`
- Finding: The active table is defined only as the latest `effective_from <= now()`, ignoring `effective_to` and publication status. Overlapping periods and equal effective dates have no deterministic resolution. Published immutability is described as an UPDATE trigger, without a deletion policy, although existing quotes depend on retaining the referenced table.
- Recommendation: Define a published, effective-at-time selection rule with unambiguous boundaries and overlap prevention. Preserve referenced published tables against deletion, define supersession behavior, and specify how existing unexpired quotes interact with later rate versions.

### 20. Consent and standalone lead capture have no end-to-end contract
- Severity: Medium
- File(s): `docs/adr/0005-langgraph-supervisor-and-interrupts.md`; `docs/design/agent-design.md`; `docs/design/mcp-server.md` — `create_lead`; `docs/compliance.md` — Lawful basis
- Finding: Routing mentions “missing consent,” but state, storage and collection rules contain no corresponding consent model. MCP requires `consent_contact: true` without defining its recorded evidence or purpose. The n8n design supports an explicit contact request, but the graph exposes no clear standalone lead-capture path. A privacy-notice link is promised without a route or template contract.
- Recommendation: Define application/contact-request flows separately, including notice delivery and minimal evidence of the customer’s request. Store purpose, source and timestamp where needed; distinguish service contact from marketing opt-in. Define the local privacy-notice destination.

### 21. Customer access expires before long-running reviews necessarily finish
- Severity: Medium
- File(s): `docs/adr/0014-authentication.md`; `docs/design/api.md` — Customer sessions; `docs/design/agent-design.md` — Interrupts
- Finding: Customer JWTs expire after seven days, while review can remain paused indefinitely and no renewal or recovery mechanism exists. `sessionStorage` also loses access when the browser session ends. Customers can therefore lose access to their own pending application and eventual decision.
- Recommendation: Define the intended access lifetime and recovery behavior. Either bound and expire pending demo reviews within that lifetime or add a secure recovery/renewal mechanism. Document expired-session UI behavior and how customers retrieve an outstanding decision.

### 22. Contact details remain in model history after collection
- Severity: Medium
- File(s): `docs/design/security-and-privacy.md` — Prompts; `docs/design/agent-design.md` — Messages and history windowing
- Finding: Security says email and phone enter prompts only during `collect_contact` and are never echoed into model context afterward. However, ingress redaction preserves ordinary contact details, and state retains customer messages through `add_messages`. Keeping the last 12 messages reintroduces the original contact message on subsequent turns.
- Recommendation: Separate the stored dashboard transcript from the model-visible transcript. Extract contact details into domain storage, replace them with references or masks in model history, and ensure summaries and tool messages follow the same policy.

### 23. n8n deduplication is not a durable idempotency guarantee
- Severity: Medium
- File(s): `docs/adr/0012-n8n-via-outbox.md`; `docs/design/n8n.md` — Events router and follow-up
- Finding: A workflow static-data cache of 500 IDs does not define atomic deduplication under concurrent deliveries, failures or old-event replay. Marking an event before sending risks losing notification work; marking it afterward permits duplicates. Scheduled follow-ups likewise have no stable occurrence ID, and repeated callbacks can advance the follow-up date more than once.
- Recommendation: Define durable delivery/notification identities and idempotent API callbacks. Use a database-backed processing ledger or equivalent atomic claim protocol, retain deduplication records for the replay period, and document the remaining ambiguity after an external email send.

### 24. Unattended n8n startup is underspecified
- Severity: Medium
- File(s): `docs/design/n8n.md` — Credentials, Import and publish; `docs/development.md` — Quick start
- Finding: Import starts once n8n is healthy, but the owner account is created manually on first visit. The design does not establish ownership/project assignment for imported workflows and credentials, stable credential references, a shared encryption key between services, or an observable condition proving published webhooks are ready. AWS ephemeral SQLite also loses more than execution history unless these items are recreated.
- Recommendation: Specify and test a complete unattended bootstrap sequence, including ownership, credential IDs, encryption-key configuration and webhook readiness. Make repeated imports safe and define exactly what is restored after an AWS task replacement.

### 25. The advertised quick start bypasses secret generation
- Severity: Medium
- File(s): `README.md` — Quick start; `docs/development.md` — Quick start; `docs/design/security-and-privacy.md` — Secrets
- Finding: README instructs users to copy `.env.example`, while security says local credentials are randomly generated by `make env`. Development presents the two as interchangeable. Copying placeholders either produces insecure shared credentials or prevents the promised immediate startup. There is no equivalent secret-generation command for Windows users without make.
- Recommendation: Use one documented, cross-platform initialization command that generates secrets and refuses to overwrite existing ones. Reject blank/example credentials at startup and have CI exercise the exact README path.

### 26. Checkpoint-size and history-window claims do not match the state
- Severity: Medium
- File(s): `docs/design/agent-design.md` — State, Tools, Checkpointing
- Finding: `add_messages` accumulates the full conversation, including tool results containing retrieved text and quote details, despite the claim that large objects are referenced only by ID and checkpoints remain a few KB. Limiting the model view to 12 messages does not prune checkpoint state and can split tool-call/result pairs. `history_summary` is used but absent from the state definition.
- Recommendation: Define checkpoint compaction explicitly, including removal of old tool payloads, preservation of complete tool exchanges, summary state and recovery behavior. Set and test a bounded model-context and checkpoint-growth policy.

### 27. RAG ingestion lacks chunking and document-version rules
- Severity: Medium
- File(s): `docs/design/data-model.md` — Policy documents and retrieval; `docs/design/catalog-and-rating.md` — Policy PDFs
- Finding: Ingestion does not specify chunk size, overlap, section/page boundary handling or limits imposed by the embedder. Changed-document ingestion has no defined replacement/version policy, and retrieval has no current-document selector. Reindexing or replacing chunks could invalidate persisted citation IDs; retaining all versions could retrieve obsolete wording.
- Recommendation: Define a bounded, section-aware chunking algorithm and immutable document versions. Specify atomic ingestion, current-version selection, stable citation retention, and whether a quote/application references a particular wording version.

### 28. Model-call limits are not specified as enforceable budgets
- Severity: Medium
- File(s): `docs/design/security-and-privacy.md` — Abuse limits; `docs/design/observability.md` — Buffered traces; `docs/design/api.md` — Rate limits
- Finding: The daily model-call cap has no counter storage, atomic reservation or definition of what counts. Traces are written only after runs finish, so they cannot alone enforce the cap across concurrent calls or crashes. Retries, fallback calls, summarization and guardrail repair can add calls beyond the visible advisor loop. Anonymous session creation and persisted MCP quotes also lack storage-abuse limits.
- Recommendation: Reserve model-call budget before each attempt and count retries/fallbacks consistently. Define persistence/reset behavior, per-role token limits, total turn deadlines and bounded concurrency. Add limits for session creation and quote-producing machine calls.

### 29. Fallback behavior is incomplete for streaming and exhausted retries
- Severity: Medium
- File(s): `docs/adr/0007-fallback-provider.md`; `docs/design/agent-design.md` — Advisor; `docs/design/api.md` — SSE errors
- Finding: Exception categories are specified, but retry counts, provider timeouts, overall deadlines and the behavior after both providers fail are not. Mid-stream failure can leave a partial primary answer before fallback output. There is also no defined safe termination when the final allowed advisor round still requests tools or structured output cannot be parsed.
- Recommendation: Specify bounded retries and deadlines for each role, a validated terminal response for exhaustion/refusal/parse failure, and explicit tool-budget termination. Buffer generation across provider attempts and ensure retries reuse stable domain-operation identities.

### 30. Retrieval tests and CI embedding assumptions are inconsistent
- Severity: Medium
- File(s): `docs/adr/0008-local-embeddings.md`; `docs/design/testing-and-evals.md`; `docs/development.md`
- Finding: Tests use a hashing embedder and claim CI never downloads models, but compose-smoke builds the normal API image, whose build downloads the real model. Retrieval-quality tests using hashing embeddings cannot establish semantic retrieval quality and may succeed only through full-text search.
- Recommendation: Separate deterministic retrieval-plumbing tests from real-embedding quality tests. Define how CI obtains or caches the production model, or provide a clearly identified test-image path. Include paraphrase cases that cannot pass solely through keyword matching.

### 31. Database bootstrap omits the extension required by agent email
- Severity: Medium
- File(s): `docs/design/data-model.md` — `agent_users`, Migrations; `docs/development.md` — Database initialization; `docs/deployment/shared-infrastructure.md` — Bootstrap SQL
- Finding: `agent_users.email` uses `citext`, but both local bootstrap documentation and AWS bootstrap SQL mention only the `vector` extension. A fresh database cannot create that column unless `citext` is provisioned; the non-DDL application role cannot repair it later.
- Recommendation: Include `citext` in the bootstrap and migration prerequisite checks, or specify a different explicit case-insensitive uniqueness implementation. Test schema creation against a genuinely fresh database.

### 32. Proxy routes do not cover documented local behavior
- Severity: Medium
- File(s): `docs/adr/0016-frontend-stack.md`; `docs/development.md` — Running outside Docker; `docs/design/api.md` — Health and simulator
- Finding: nginx forwards `/api`, `/webhooks`, `/mcp` and `/dev`, but not `/healthz` or `/readyz`; the API port is internal, leaving host-side smoke-test health access unclear. The Vite configuration forwards only `/api`, so its simulator cannot use `/dev`, despite being part of the same app.
- Recommendation: Publish a routing matrix for compose and Vite development, including health access and simulator endpoints. Define the smoke-test base URLs explicitly and preserve the production exclusion of development routes.

### 33. Private AWS n8n access has conflicting network requirements
- Severity: Medium
- File(s): `docs/deployment/shared-infrastructure.md` — Compute and edge; `docs/adr/0015-shared-aws-infrastructure.md`
- Finding: The plan uses two Express services behind a shared ALB, generally permits task ingress only from the ALB, and separately says n8n accepts only the API security group. It does not specify whether API-to-n8n traffic uses direct private discovery or the ALB, nor how the editor is excluded from public ALB routing. The proposed editor-access path is also not concretely defined.
- Recommendation: Specify the actual private service address, discovery mechanism, source security groups and ALB listener rules. Document a supported administrative access procedure and include any additional networking components in the deployment estimate.

### 34. AWS savings depend on activity that the cost model does not describe
- Severity: Medium
- File(s): `docs/deployment/shared-infrastructure.md` — Risks and monthly cost; `docs/adr/0016-frontend-stack.md`; `docs/design/whatsapp.md` — Simulator polling
- Finding: The $2–4 shared-database estimate assumes roughly 20 active hours plus infrequent scheduled wakeups. Dashboard polling every five seconds and simulator polling every second prevent pausing while tabs remain open; no hidden-tab or idle suspension is specified. Separately, a `project=policypilot` budget will not attribute PolicyPilot-induced Aurora compute billed to the shared cluster’s portfolio tag.
- Recommendation: State the active-browser assumptions and show an extended-open-dashboard cost case. Suspend unnecessary background polling. Retain an account/shared-cluster cost alarm alongside project-tag budgets, and distinguish directly attributable costs from shared incremental costs.

### 35. Phase 1 includes avoidable third-provider and observability work
- Severity: Low
- File(s): `docs/adr/0007-fallback-provider.md`; `docs/adr/0011-observability.md`; `docs/reference/verified-facts.md`; `docs/roadmap.md`
- Finding: Bedrock is described as supported despite being unavailable to the account, adding a third provider beyond the required Claude/OpenAI pair. Optional Langfuse export adds another privacy and compatibility surface alongside mandatory built-in traces. The reference document additionally describes a Langfuse compose profile that the accepted design explicitly excludes.
- Recommendation: Defer Bedrock implementation and optional Langfuse export until after the local MVP. Preserve their extension points, retain built-in tracing, and reconcile the compose-profile statements.

**Top 5 to fix before implementation**

1. **Finding 1:** Make review preparation and resume replay-safe.
2. **Finding 2:** Define durable WhatsApp acceptance, processing and delivery.
3. **Finding 3:** Validate customer-facing output before exposing it.
4. **Finding 5:** Sanitize all input before storage and checkpointing.
5. **Finding 9:** Bind approval to a consistent, customer-confirmed application snapshot.

---

## Triage

Every finding was checked against the docs. Each one was applied (**Accepted**) or applied in
part (**Partly accepted**), with the rejected part explained in one line. The changes were made
before any affected code was written. No second review was run.

| # | Finding | Decision | What changed |
|---|---|---|---|
| 1 | Review-node replay duplicates side effects | Accepted | A new `prepare_review` node creates the task and event idempotently by `review_key`; `human_review` only interrupts. One-unresolved-task index over open, claimed and resolving. A `resolving` state with reconciliation (agent-design.md, data-model.md, ADR-0005). |
| 2 | WhatsApp acknowledged before durable acceptance | Accepted | `inbound_events` is stored before the 200, a failed insert returns 503, processing state is separate from dedupe, and events are recovered at start-up. `outbound_messages` delivery intents with retries (whatsapp.md, data-model.md, ADR-0009). |
| 3 | Unvalidated tokens streamed before guardrails | Accepted | No model tokens are streamed. SSE carries progress and DB-built artifacts, then the validated reply. `message.replaced` was removed (agent-design.md, api.md, ADR-0006, ADR-0016). |
| 4 | Guardrail rewrite can reintroduce unchecked content | Accepted | One repair, then a final deterministic validation of everything, then a template fallback (agent-design.md). |
| 5 | Redaction must precede graph entry | Accepted | A single ingress `sanitize()` runs before storage, logs, checkpoints and models, for every text source. The graph sees only `model_text` (security-and-privacy.md, agent-design.md). |
| 6 | Erasure leaves recoverable PII | Accepted | A full erasure inventory, including checkpoints (`adelete_thread`), token revocation via `token_version`, inactivity closure, and retention for sales records (data-model.md). |
| 7 | Volunteered health data contradicts minimisation | Accepted | One policy: health sentences are removed at ingress and the customer is offered a private agent call; the detector's limits are documented (security-and-privacy.md, compliance.md, whatsapp.md). |
| 8 | Tunnel exposes a signing bypass via `/dev` | Accepted | `/dev` is mounted only when `APP_ENV=local` and `WHATSAPP_MODE=simulator`; there is a webhook-only tunnel config; host ports bind to 127.0.0.1 (api.md, whatsapp.md, ADR-0009). |
| 9 | Approval not bound to an immutable confirmed application | Accepted | Immutable `application_snapshots`; `edit_and_approve` is limited to clerical fields; re-rating or reconfirmation goes through `request_changes`; expired quotes block approval; ownership checks in services (data-model.md, agent-design.md, api.md). |
| 10 | Locking protocol does not deliver the documented behaviour | Accepted | One protocol: a session try-lock on a dedicated pooled connection, 409 for web, an ordered per-conversation drain for WhatsApp, the same lock for resolve, replies and erasure, and tests for release (agent-design.md). |
| 11 | Turn rollback and retry semantics | Accepted | A `turns` table keyed by `clientMessageId`; a disconnect does not cancel the turn; failure semantics stated explicitly; idempotent quote operations (agent-design.md, api.md, data-model.md). |
| 12 | Monetary checks lose meaning | Accepted | Premiums only through `{{quote:<id>}}` placeholders rendered by the server with product and period; annual and monthly-equivalent labels; literal amounts limited to product attributes (agent-design.md, whatsapp.md, ADR-0003). |
| 13 | AI Act classification asserted too strongly | Accepted | Verified provisions are separated from the classification hypothesis, which is marked unresolved, and the model's influence is described (compliance.md, ADR-0002, verified-facts.md). |
| 14 | Paused conversations lack control handling | Accepted | Deterministic precedence: STOP, withdraw, escalate, acknowledge. Intervening messages are attached server-side on resume. The client-supplied transcript was removed (agent-design.md, api.md). |
| 15 | No delivery of human replies to web customers | Accepted | Authenticated polling with an `after` cursor while paused, only while the tab is visible; works after a reload (api.md). |
| 16 | Citation validity mistaken for support | Partly accepted | Unsupported answers are replaced by `insufficient_evidence`; wrong-product citations fail; live judge cases check support. Rejected: deterministic claim-to-source verification, because it is not feasible without another model, so the live eval covers it instead. |
| 17 | Required fields and budget search undefined | Accepted | A required-field table per line; `search_products` is non-priced discovery and the new `quote_products` is profile-aware with budget filtering, in the graph and in MCP (agent-design.md, mcp-server.md). |
| 18 | Rating inputs and units not implementation-ready | Accepted | Input schemas, units, bands, decimal encoding, rounding and a worked example per line (catalog-and-rating.md). The engine and golden tests already follow them. |
| 19 | Active rate-table selection ambiguous | Accepted | Selection by publication and period, overlap prevention, no update or delete after publication, RESTRICT from quotes (data-model.md, ADR-0003). |
| 20 | Consent and standalone lead capture missing | Accepted | A contact-request flow, consent evidence fields on leads, marketing kept separate, a `/privacy` page, and MCP consent evidence (agent-design.md, data-model.md, mcp-server.md, compliance.md). |
| 21 | Customer tokens expire before reviews finish | Accepted | A 30-day sliding token in `localStorage` with revocation; pending reviews expire after 14 days (ADR-0014, api.md, data-model.md). |
| 22 | Contact details stay in model history | Accepted | `model_text` masks email and phone; contact details are parsed by the service and never put in graph state (security-and-privacy.md, agent-design.md). |
| 23 | n8n dedupe is not durable | Partly accepted | API callbacks are idempotent on `occurrenceId`, and at-least-once semantics are documented. Rejected: a database-backed processing ledger inside n8n, which is over-engineering for staff notification emails in Phase 1. |
| 24 | Unattended n8n start-up underspecified | Accepted | Import and publish before the server starts, a shared encryption key, fixed credential and workflow IDs, a readiness check, a smoke check, and AWS re-import at start (n8n.md, ADR-0012). |
| 25 | Quick start bypasses secret generation | Accepted | A cross-platform `scripts/init_env.py` (or the `init-env` container); the API refuses placeholder secrets; CI runs the exact README path (README, development.md, security-and-privacy.md). |
| 26 | Checkpoint size and history window inconsistent | Accepted | A `compact_history` node with stubbed tool results, pair-preserving windows, `history_summary` in state, and a growth test (agent-design.md). |
| 27 | RAG chunking and versioning missing | Accepted | Section-aware chunking rules, immutable document versions with an atomic `is_current` switch, stable chunk IDs, and the document version recorded in snapshots (catalog-and-rating.md, data-model.md). |
| 28 | Model-call limits not enforceable | Accepted | An atomic `llm_usage_counters` reservation per attempt, per-role token limits, a turn deadline, a concurrency cap, and session and MCP-quote limits (security-and-privacy.md, agent-design.md). |
| 29 | Fallback, streaming and exhaustion behaviour incomplete | Accepted | Timeouts and retries per role, a terminal template with handoff, a tool-budget termination round, a parse-failure default, and no partial streaming (agent-design.md, ADR-0007). |
| 30 | Retrieval tests and CI embeddings inconsistent | Accepted | Plumbing tests use the hashing embedder; quality tests with paraphrases run on the real model in the CI compose job with a layer cache (testing-and-evals.md, ADR-0008). |
| 31 | `citext` not provisioned | Accepted | `citext` dropped; uniqueness via a `lower(email)` index (data-model.md). |
| 32 | Proxy routes incomplete | Accepted | A routing matrix for compose, Vite and AWS; nginx and Vite forward health and `/dev` (api.md, development.md). |
| 33 | AWS n8n private access undefined | Accepted | n8n is a plain Fargate service without an ALB, with Cloud Map discovery, security groups allowing only the API, editor access through ECS Exec port forwarding, and the cost added (shared-infrastructure.md). |
| 34 | AWS savings depend on unmodelled activity | Accepted | Visibility-gated polling, a cost case for a dashboard open during business hours, and attributable versus shared-cost budgeting (shared-infrastructure.md, ADR-0016). |
| 35 | Avoidable third provider and Langfuse work | Accepted | Bedrock, Langfuse export and the n8n MCP demo deferred to Phase 2, with extension points kept and the compose-profile mention reconciled (ADR-0007, ADR-0011, roadmap.md, verified-facts.md). |
