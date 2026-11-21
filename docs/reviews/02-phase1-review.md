# Review 02: Codex code review of Phase 1

- Date: 2026-11-20
- Reviewer: OpenAI Codex CLI 0.171.0-alpha.13 (`codex exec`, model `gpt-6.1-sol`, reasoning effort
  high, sandbox `read-only`; the CLI bundled
  with the Codex desktop app, as in review 01), run once
- Scope: branch `phase-1` at commit `246787f` against `main`
- Brief: [`02-phase1-review-prompt.md`](02-phase1-review-prompt.md)

The raw output is reproduced verbatim below, followed by the triage.

---

## Raw review output

The branch has a coherent service structure and useful safeguards around deterministic rating, scoped tokens, webhook signatures, and buffered model output. However, several safety guarantees fail under ordinary refusals, retries, crashes, or concurrent requests. The most serious defects allow model-written premiums through validation, accept negated consent, expose contact details to graph state and models, and leave reviews or WhatsApp processing unrecoverable. This was a read-only static review of committed `phase-1` (`246787f`) against `main`; no tests, containers, installations, or network calls were run.

### 1. Price validation accepts model-written premiums
- Severity: High
- File(s): `apps/api/src/policypilot/agents/guardrails.py:94`; `apps/api/src/policypilot/agents/nodes.py:479`
- Finding: Literal amounts pass whenever their numeric value matches an allowed amount, regardless of meaning. A customer budget of $80 therefore permits the model to say “Your premium is $80 per month” without an $80 quote. The money regex also misses written amounts such as “eighty dollars.” Quote placeholders validate only quote membership, so a valid quote can be attributed to another product in surrounding model text.
- Recommendation: Render premium statements, including product identity, exclusively from structured quote references. Keep budget and attribute statements separate from premium statements, and reject unsupported monetary prose.

### 2. Negated consent and submission are accepted
- Severity: High
- File(s): `apps/api/src/policypilot/agents/application.py:29`, `:163`, `:205`
- Finding: Consent uses positive keyword matching before considering negatives. “No, do not submit” matches `submit` in `YES`, records contact consent, and creates a lead. At `confirm_submit`, “do not submit” also sets `submit=True` and submits an application whose summary was shown.
- Recommendation: Accept buttons or a small set of unambiguous affirmative responses. Treat negative or mixed responses as refusal or a clarification request, and test negation at both consent and submission.

### 3. Review decisions put contact details into checkpoints and model history
- Severity: High
- File(s): `apps/api/src/policypilot/services/reviews.py:107`; `apps/api/src/policypilot/agents/runner.py:327`; `apps/api/src/policypilot/agents/review_nodes.py:148`, `:268`; `apps/api/src/policypilot/agents/nodes.py:454`
- Finding: Review notes use `stored_text`, which retains emails and phone numbers, and clerical edits are passed unchanged in `Command(resume=decision)`. `human_review` writes that entire decision into graph state. Notes are subsequently included in the assistant reply and appended to model-visible history, so contact details can reach later model calls as well as historical checkpoints.
- Recommendation: Keep clerical values and customer-visible notes in the service database. Resume with a decision identifier, and provide only sanitized `model_text` to graph state and model history.

### 4. Review recovery does not cover the actual commit/checkpoint windows
- Severity: High
- File(s): `apps/api/src/policypilot/agents/review_nodes.py:71`, `:193`; `apps/api/src/policypilot/services/reviews.py:134`; `apps/api/src/policypilot/services/maintenance.py:139`
- Finding: `prepare_review` commits the paused conversation and task before LangGraph checkpoints the interrupt. A crash in between leaves customer turns taking `_paused`, without completing graph preparation. Conversely, `finalize_review` commits `resolved` before checkpointing and storing the customer reply; failure afterward leaves no supported recovery because reconciliation selects only `resolving` tasks, and `retry_resume` refuses threads without interrupts.
- Recommendation: Reconcile database state, pending graph tasks, checkpoints, and reply delivery for every incomplete review lifecycle. Make recovery work both before the interrupt checkpoint and after the resolution commit, with failure-injection tests at each boundary.

### 5. Crashed turns remain permanently “running”
- Severity: High
- File(s): `apps/api/src/policypilot/agents/runner.py:108`, `:144`, `:534`; `apps/api/src/policypilot/services/maintenance.py:169`
- Finding: Turn registration commits `status="running"` before graph execution. Process termination or cancellation does not execute `_fail`, and there is no abandoned-turn recovery job. Reusing that `clientMessageId` then returns `turn_in_progress` indefinitely, even after PostgreSQL has released the conversation lock.
- Recommendation: Under the conversation lock, reconcile abandoned running turns against checkpoints and stored replies. Recover completed results or mark safely retryable failures using a durable ownership or lease mechanism.

### 6. Recovered WhatsApp events cannot replay completed turns
- Severity: High
- File(s): `apps/api/src/policypilot/channels/whatsapp/service.py:247`, `:288`; `apps/api/src/policypilot/agents/runner.py:151`
- Finding: A turn may complete before its inbound event is marked `done`. After recovery, `_process` calls `begin` again without checking `runner.replay`. A completed turn makes `begin` raise `TurnInProgressError`; `drain` returns the event to `pending` forever. The same failure occurs when delivery raises after the turn was committed.
- Recommendation: Replay completed turns before beginning execution, reconcile their delivery intents, and mark the inbound event done. Distinguish completed, abandoned, and genuinely executing turns.

### 7. Assistant replies and WhatsApp delivery intents are committed separately
- Severity: High
- File(s): `apps/api/src/policypilot/agents/runner.py:451`; `apps/api/src/policypilot/channels/whatsapp/service.py:334`; `apps/api/src/policypilot/services/maintenance.py:121`
- Finding: `_store_reply` commits the assistant message and completed turn, then persists the trace, then calls a hook that opens another transaction to create delivery intents. A crash or trace failure between those steps permanently loses the WhatsApp reply. Maintenance runners never configure that hook, so their review resumes also omit WhatsApp delivery.
- Recommendation: Insert delivery intents in the same transaction as the stored reply and turn completion, with stable message/part uniqueness keys. Make this a service invariant shared by API and maintenance runners.

### 8. Queued turns can exhaust the database pool
- Severity: High
- File(s): `apps/api/src/policypilot/agents/runner.py:139`, `:173`; `apps/api/src/policypilot/services/locks.py:41`; `apps/api/src/policypilot/db/session.py:27`
- Finding: `begin` acquires a dedicated pooled connection before `execute` waits for the eight-turn semaphore. With the default ten-connection pool, eight executing turns plus two queued turns can occupy every connection with advisory locks. Executing turns then cannot obtain connections for model-budget reservations, tools, or reply storage and fail on pool timeouts.
- Recommendation: Apply bounded admission before acquiring conversation locks, return the documented overload response, and reserve sufficient database capacity for executing turns.

### 9. Conversation writers do not consistently use the lock protocol
- Severity: High
- File(s): `apps/api/src/policypilot/api/agent.py:316`; `apps/api/src/policypilot/services/reviews.py:95`; `apps/api/src/policypilot/services/maintenance.py:38`
- Finding: Human replies and inactivity closure do not acquire the conversation lock. Review resolution records its decision and collects intervening messages before `runner.resume` acquires it. Consequently, a human reply can commit after erasure has scrubbed messages, and customer messages arriving while resolution waits can be omitted from the resumed history.
- Recommendation: Acquire the conversation lock before checking state, collecting messages, or performing any conversation mutation. Hold it through the complete reply or resolution operation.

### 10. Erasure never matches customer outbox events
- Severity: High
- File(s): `apps/api/src/policypilot/services/privacy.py:168`; `apps/api/src/policypilot/services/events.py:55`
- Finding: Erasure filters `payload["customerId"]`, but events store the identifier at `payload["data"]["customerId"]`. Undelivered events therefore survive erasure and can subsequently send notifications containing the erased customer’s first name and record links.
- Recommendation: Correct the JSON path, coordinate erasure with event delivery, and test erasure with pending and dead-lettered events containing customer data.

### 11. Customer corrections can leave quotes based on stale inputs
- Severity: High
- File(s): `apps/api/src/policypilot/agents/nodes.py:582`; `apps/api/src/policypilot/agents/application.py:113`, `:204`
- Finding: Once a profile is complete, `quote` and `compare` route directly to the advisor without extraction. “Actually I’m 50; quote again” can therefore quote the existing age of 40. After `request_changes`, the application flow only updates contact details and retains the original quote; it cannot perform the documented re-rating of an expired quote or changed rating inputs.
- Recommendation: Process and validate profile corrections before quoting. Add a re-rating transition that invalidates the previous selection, obtains a fresh quote, and requires a new customer-confirmed summary.

### 12. The advisor can invent a coverage override
- Severity: High
- File(s): `apps/api/src/policypilot/agents/tools.py:208`; `apps/api/src/policypilot/domain/profile.py:81`
- Finding: `coverage_override` is accepted directly from the model and passed into rating inputs. Nothing checks the schema’s claim that the customer stated that amount in the current message. The resulting deterministic quote can therefore price an invented coverage amount while the application’s needs profile still contains the customer’s original amount.
- Recommendation: Derive permitted overrides from validated customer input, or require explicit confirmation before using them. Verify consistency between quote inputs, needs, and the submitted snapshot.

### 13. STOP does not prevent subsequent proactive sends
- Severity: High
- File(s): `apps/api/src/policypilot/channels/whatsapp/service.py:322`, `:334`, `:425`
- Finding: STOP sets `whatsapp_opted_out_at`, but no delivery or sending path reads it. Pending messages and later review notifications continue to send, including templates outside the customer-service window, despite the confirmation promising no more messages unless the customer writes again.
- Recommendation: Enforce opt-out at enqueue and transmission time, suppress pending proactive messages, and define how a later inbound message reopens permitted service communication.

### 14. Model failures promise or offer a handoff that cannot happen
- Severity: High
- File(s): `apps/api/src/policypilot/agents/runner.py:242`, `:518`; `apps/api/src/policypilot/agents/review_nodes.py:34`; `data/seed/message_templates.yaml:163`
- Finding: Budget exhaustion only stores the `rate_limited` template, which says an agent has already been asked; no review task or notification is created. Following the instruction to type “agent” also fails when the budget is exhausted or models are unavailable, because `handoff` first calls the writer model.
- Recommendation: Provide a deterministic handoff path that creates the task and event without any model call. Use it for budget exhaustion, provider exhaustion, and explicit human requests.

### 15. Application side effects are not replay-safe
- Severity: Medium
- File(s): `apps/api/src/policypilot/agents/application.py:177`, `:219`; `apps/api/src/policypilot/services/applications.py:81`, `:157`
- Finding: Consent creates a new lead and application with generated IDs, and submission always allocates another snapshot version. These transactions commit independently of node checkpoints. Replaying a node after a checkpoint failure can therefore create duplicate leads, applications, notifications, or snapshot versions.
- Recommendation: Give each application operation a stable, database-enforced idempotency key derived from its conversation, flow, and turn. Return existing committed results on replay.

### 16. Tool-budget handling produces duplicate tool results
- Severity: Medium
- File(s): `apps/api/src/policypilot/agents/nodes.py:335`
- Finding: The second slice uses `tools.calls` after execution has incremented it. For example, with seven calls already used, the eighth call executes, then the error loop starts at index zero and emits another result for that same tool-call ID. This creates an invalid tool exchange for live providers.
- Recommendation: Compute the remaining budget once before executing the batch, then partition calls into executed and rejected lists. Test batches that cross and exactly reach the limit.

### 17. WhatsApp retries have no scheduled wake-up
- Severity: Medium
- File(s): `apps/api/src/policypilot/channels/whatsapp/service.py:395`, `:399`, `:454`
- Finding: `_backoff` schedules a future `next_attempt_at`, but `send_pending` immediately queries again, finds no currently due messages, and exits. No timer wakes it when the retry becomes due. A transient failure can leave a message pending indefinitely until another send or process restart.
- Recommendation: Schedule a wake-up for the earliest pending retry, retaining the event-driven behavior when the queue is empty. Test eventual retry without additional inbound activity.

### 18. Integration notes leave permanent PII in the audit log
- Severity: Medium
- File(s): `apps/api/src/policypilot/api/integrations.py:106`; `apps/api/migrations/versions/0001_initial_schema.py:1115`; `apps/api/src/policypilot/services/privacy.py:125`
- Finding: Follow-up notes are copied into audit details using `stored_text`, which preserves emails and phone numbers. The audit table is append-only, and erasure does not scrub these entries. A callback note containing contact details consequently survives customer erasure indefinitely, contrary to the documented IDs-only audit policy.
- Recommendation: Keep free-text notes in an erasable customer-related store. Restrict audit entries to identifiers and non-sensitive action metadata.

### 19. Health removal is bypassed for several documented ingress sources
- Severity: Medium
- File(s): `apps/api/src/policypilot/security/ingress.py:87`; `apps/api/src/policypilot/mcp_server/server.py:255`; `apps/api/src/policypilot/services/reviews.py:107`
- Finding: Health removal runs only for `customer` and `webhook`. Recognized disclosures such as “I have diabetes” remain intact in MCP notes, integration notes, agent messages, and review notes. Some of those values are subsequently stored or incorporated into graph history, despite the intended policy that health details are not retained.
- Recommendation: Apply the documented health-removal policy consistently to free-text sources, including agent-entered customer information, and verify storage and model boundaries for each source.

### 20. WhatsApp processing discards ingress redaction metadata
- Severity: Medium
- File(s): `apps/api/src/policypilot/channels/whatsapp/service.py:167`, `:175`, `:288`
- Finding: Acceptance stores sanitized text and redaction counts, but processing sanitizes the already-redacted text again and ignores the stored counts. The runner receives empty redaction metadata, so sensitive-only routing and the required card/ID/health notices do not work as they do for web chat.
- Recommendation: Reconstruct the sanitized input using the persisted redaction metadata, or persist both safe text representations and counts. Test sensitive disclosures through the complete WhatsApp path.

### 21. Reindexing deletes stable citation identities
- Severity: Medium
- File(s): `apps/api/src/policypilot/seed/run.py:205`; `Makefile:57`
- Finding: Reindexing unchanged wording deletes all its chunks and inserts replacements with new identity values. Stored messages and checkpoints retain the old chunk IDs, which no longer resolve. This contradicts the stable-citation requirement even for the advertised `make reindex` operation.
- Recommendation: Update embeddings in place when chunk content is unchanged. When chunking changes, retain immutable prior chunks and create a separate version or generation.

### 22. The live judge cannot verify citation support or fail a scenario
- Severity: Medium
- File(s): `apps/api/evals/judge.py:29`, `:45`; `apps/api/evals/runner.py:348`; `apps/api/evals/scenarios/04-policy-qa.yaml:1`
- Finding: The judge receives only customer and assistant text, with no cited clauses or retrieved evidence. It therefore cannot establish whether claims follow from the fictional policy wording. Its scores also never change `result.passed` or the exit status, so unsupported answers can still produce a successful live evaluation.
- Recommendation: Supply the exact cited document versions and clauses, score claim support against them, and make defined failures affect scenario results and the process exit code.

### 23. Starting over races with the previous conversation
- Severity: Medium
- File(s): `apps/web/src/chat/useChat.ts:111`, `:174`, `:252`; `apps/web/src/chat/ChatPage.tsx:44`
- Finding: “Start over” remains available during an active turn, but old asynchronous callbacks continue using shared refs and dispatch. An old `message.completed` can save the old conversation’s token into the newly created session, while old refreshes can replace the new transcript or cursor.
- Recommendation: Associate requests and callbacks with a session generation. Ignore responses from previous generations, cancel obsolete client streams, and ensure token renewal targets the originating conversation.

### 24. A clean SSE close is treated as successful completion
- Severity: Medium
- File(s): `apps/web/src/chat/api.ts:73`; `apps/web/src/chat/useChat.ts:179`
- Finding: `sendTurn` resolves when the stream ends without requiring `message.completed`. `runTurn` then refreshes once and discards the pending turn even if `completed` is still null. An early clean close can therefore lose the retry identifier and leave a later assistant reply undisplayed because active conversations are not polled.
- Recommendation: Require an explicit completion event. Otherwise retain the pending identifier and reconcile or retry the same turn until its durable outcome is known.

### 25. Transcript pagination is not consumed
- Severity: Medium
- File(s): `apps/api/src/policypilot/services/conversations.py:53`; `apps/api/src/policypilot/api/agent.py:438`; `apps/web/src/chat/useChat.ts:118`
- Finding: Message reads return the first 200 rows, but the chat client fetches only one page and ignores `nextAfter`. Reloading a conversation longer than 200 messages omits its latest replies and actions. The agent conversation endpoint likewise returns only the first 200 messages, without a pagination mechanism.
- Recommendation: Fetch transcript pages until caught up, or load a recent page with explicit backward pagination. Ensure both customer and agent views can reach the latest messages.

### 26. Sales retention fails for reviewed applications
- Severity: Medium
- File(s): `apps/api/src/policypilot/services/maintenance.py:74`; `apps/api/src/policypilot/db/models.py:489`
- Finding: `purge_sales` deletes stale applications without handling `ReviewTask.application_id`, whose foreign key has no delete action. Any matching application with a review task causes a foreign-key failure and rolls back the purge, preventing the documented retention cleanup.
- Recommendation: Define retention for associated review records and delete, anonymize, or detach them in the correct order before deleting applications. Test retention with resolved and unresolved reviews.

### 27. MCP contact leads cannot be actioned in the dashboard
- Severity: Medium
- File(s): `apps/api/src/policypilot/mcp_server/server.py:255`; `apps/api/src/policypilot/api/agent.py:539`; `apps/web/src/agent/LeadsPage.tsx:50`; `apps/web/src/agent/AgentApp.tsx:28`
- Finding: MCP leads store contact details on a customer without a conversation or application. The agent API exposes neither those contact details nor a lead-detail endpoint, and `/agent/leads/:leadId` redirects to the list. Staff receive a notification but cannot obtain the requested contact information through the dashboard.
- Recommendation: Add an authenticated lead-detail endpoint and page exposing service contact details, consent evidence, and sanitized notes. Make notification links open that record.

### 28. Seeding silently reactivates revoked machine tokens
- Severity: Medium
- File(s): `apps/api/src/policypilot/seed/run.py:275`; `docker-compose.yml:69`
- Finding: The seed upsert unconditionally sets `revoked_at=None` for existing local MCP and integration tokens. Running seed again, including during stack recreation, restores access to a token that an operator explicitly revoked.
- Recommendation: Preserve revocation on ordinary seed runs. Require an explicit credential rotation or reset operation to reactivate machine access.

**Top 5 to fix**

1. **Finding 1:** Enforce quote provenance in customer-facing price statements.
2. **Finding 2:** Reject negated or ambiguous consent and submission.
3. **Finding 3:** Keep contact details outside checkpoints and model history.
4. **Finding 4:** Recover reviews across database/checkpoint failures.
5. **Finding 7:** Commit WhatsApp delivery intents atomically with replies.
---

## Triage

Every finding was checked against the code before it was classified; none was rejected. Status
values: **fixed** (with a regression test, commit noted; most tests are in
`apps/api/tests/integration/test_review_02_fixes.py` and `apps/web/src/chat/review02.test.tsx`),
**partly fixed** (the contained part fixed; the rest deferred), **accepted, deferred to Phase 2**
(needs a redesign; listed under "Known limitations" in the README and the roadmap). Result:
24 fixed, 2 partly fixed, 2 deferred, 0 rejected. Nothing that could let
the assistant state a wrong price, act without consent, message someone who opted out or expose
personal data is deferred.

| # | Finding | Severity | Status | Reason |
|---|---|---|---|---|
| 1 | Model-written premiums pass price validation | High | fixed (`cd94d2f`, `0e5ea3c`) | First round: cue words around currency amounts, and placeholders beside another product's name. An independent check then showed rewordings without a currency sign still passed, so the second round accounts for every number in a reply (with or without a currency sign, in digits or words) by kind and by the turn's facts; the eight rewordings are now rejection tests. |
| 2 | Negated consent and submission accepted | High | fixed (`45bda2c`) | Confirmed; any negation now blocks consent and submission. |
| 3 | Contact details in checkpoints and model history | High | fixed (`4fbea4b`) | Notes are masked; the graph gets the action, the note and the edited field names; clerical values and the reviewer stay on the review task row. |
| 4 | Review recovery misses commit/checkpoint windows | High | accepted, deferred to Phase 2 | Confirmed; needs a reconciler for every review state plus failure-injection tests (a redesign). |
| 5 | Crashed turns stay `running` forever | High | fixed (`f095cb0`) | A `running` turn found while the conversation lock is free is marked abandoned and can be retried with the same id. |
| 6 | Recovered WhatsApp events cannot replay completed turns | High | fixed (`f095cb0`) | `_process` replays a completed turn instead of beginning it again. |
| 7 | Replies and WhatsApp delivery intents committed separately | High | partly fixed (`f095cb0`) | Maintenance runners now deliver WhatsApp replies; one transaction for the reply and its delivery intents is accepted, deferred to Phase 2. |
| 8 | Queued turns can exhaust the database pool | High | fixed (`f095cb0`) | Admission happens before the lock, capped at half the pool; a full runner answers 503 with `Retry-After`. |
| 9 | Conversation writers bypass the lock protocol | High | partly fixed (`f095cb0`) | Agent replies and inactivity closure take the lock, and erased conversations refuse replies; taking the lock before review resolution collects messages is accepted, deferred to Phase 2. |
| 10 | Erasure never matches outbox events | High | fixed (`45bda2c`) | Confirmed wrong JSON path; delivered events are scrubbed too. |
| 11 | Corrections leave quotes on stale inputs | High | fixed (`5c9dfed`) | Quote and compare go through extraction; at the application summary a correction updates the profile and changed inputs or an expired quote produce a fresh quote to confirm. |
| 12 | The advisor can invent a coverage override | High | fixed (`2d4e1db`) | The override must be an amount the customer wrote in the current message; otherwise the profile is quoted. |
| 13 | STOP is not enforced on sends | High | fixed (`d6dedec`, follow-up below) | Nothing is queued after STOP, messages queued before it are not sent, and writing again lifts it. CI later showed the STOP confirmation occasionally suppressed: the opt-out time came from Python while row times come from Postgres; both now use the database clock. |
| 14 | A promised handoff needs a model call | High | fixed (`1bdf696`) | The handoff falls back to a deterministic summary; the budget message no longer claims an agent was asked. |
| 15 | Application side effects are not replay-safe | Medium | accepted, deferred to Phase 2 | Confirmed; needs database-enforced operation keys across the application flow (a redesign). |
| 16 | Tool-budget handling duplicates tool results | Medium | fixed (`45bda2c`) | Confirmed slicing bug. |
| 17 | WhatsApp retries have no wake-up | Medium | fixed (`f095cb0`, `c64ecbe`) | The sender schedules itself for the earliest pending retry. The extra query widened an older race (a nudge during the loop's last check was lost), seen once in CI and fixed with a re-check flag. |
| 18 | Integration notes leave PII in the audit log | Medium | fixed (`45bda2c`) | Confirmed; the audit entry keeps only `has_note`. |
| 19 | Health removal bypassed for some sources | Medium | fixed (`45bda2c`) | Confirmed; every source is now filtered. |
| 20 | WhatsApp processing drops redaction metadata | Medium | fixed (`f095cb0`) | Counts stored at acceptance are merged back, so notices and sensitive-only routing work. |
| 21 | Reindexing changes citation IDs | Medium | fixed (`8534006`) | Unchanged chunks are re-embedded in place; ids change only when the chunking itself changes. |
| 22 | The live judge cannot check citations or fail a run | Medium | fixed (`8534006`) | The judge receives the cited clauses; a criterion scored below 3 fails the scenario. The live run itself has not happened yet (no API key). |
| 23 | "Start over" races with the previous conversation | Medium | fixed (`1955404`) | Results from an earlier conversation are dropped, tokens renew only their own conversation, and Start over is disabled during a turn. |
| 24 | A clean SSE close counts as success | Medium | fixed (`1955404`) | Without `message.completed` the turn is done only if the stored transcript shows the reply; otherwise it stays retryable. |
| 25 | Transcript pagination is not consumed | Medium | fixed (`1955404`) | The API reports `hasMore`, the client reads to the end, and the agent view returns the whole transcript. |
| 26 | Sales retention fails on reviewed applications | Medium | fixed (`45bda2c`) | Confirmed foreign-key failure. |
| 27 | MCP contact leads cannot be actioned | Medium | fixed (`1955404`) | The desk shows each lead's email, phone and consent evidence. |
| 28 | Re-seeding reactivates revoked tokens | Medium | fixed (`45bda2c`) | Confirmed. |
