You are an independent senior reviewer (Python/FastAPI, LangGraph agents, PostgreSQL, React/TypeScript, security, insurance compliance). Review the CODE on branch `phase-1` of the repository in the current working directory against `main` (which holds only a licence and a .gitignore, so the whole branch is new). Do not modify any files. Do not run anything that writes, installs, starts containers or calls the network; reading files and read-only git commands (`git diff main...phase-1 --stat`, `git log`, `git show`) are all you need. Today is 2026-11-20.

## Project

PolicyPilot is a public portfolio project by a senior AI solutions architect: an AI copilot for a fictional multi-carrier insurance agency. Customers use web chat and WhatsApp; a LangGraph graph does needs analysis, product search and comparison, RAG over policy PDFs with citations, quotes from a deterministic rating engine, and application drafts. A licensed human agent approves anything binding in a dashboard (LangGraph interrupt/resume). There is also an MCP server and n8n automations fed by a transactional outbox. Phase 1 runs locally with `docker compose`; tests and offline evals run without API keys (a scripted fake model). Nothing is deployed.

Fixed choices (do not recommend replacing them; comment on how they are used): Python + FastAPI, LangChain/LangGraph, PostgreSQL + pgvector, React + TypeScript, an MCP server, n8n, WhatsApp Business, Claude via the Claude API as the primary model with OpenAI as fallback, AWS later, prices only from deterministic code, human approval before anything binding. English only; a future second language is left-to-right.

External facts (package and image versions, model IDs such as claude-opus-5-5 / claude-haiku-5-5, WhatsApp Graph API v27.0, MCP SDK 2.x APIs, n8n 2.x CLI) were verified on 2026-11-20 and are recorded in docs/reference/verified-facts.md. Do not flag them as wrong merely because they post-date your training data.

The design docs (docs/) were reviewed separately (docs/reviews/01-docs-review.md). Use them as the intended behaviour; flag code that contradicts them, but this review is about the code.

## Where to look

- apps/api/src/policypilot/ (agents/, api/, channels/, services/, security/, rag/, domain/, mcp_server/, events/, db/, seed/, main.py, config.py, cli.py)
- apps/api/migrations/, apps/api/tests/, apps/api/evals/
- apps/web/src/, apps/web/nginx.conf, apps/web/Dockerfile
- apps/api/Dockerfile, docker-compose.yml, docker-compose.tunnel.yml, docker/, workflows/n8n/, scripts/
- .github/workflows/ci.yml, Makefile, README.md

## What to look for

1. Correctness bugs: wrong logic, unhandled states, broken edge cases, off-by-one, wrong SQL, race conditions.
2. Concurrency and recovery: the conversation lock, turn idempotency, interrupt/resume and replay safety, outbox and inbound-event processing, crash windows between database commits and checkpoints.
3. Security: authentication and authorisation (customer, agent, MCP and integration tokens), webhook signature checks, injection (SQL, prompt), secrets handling, rate limits, CORS/CSP, data exposure through APIs or logs, the nginx and tunnel configuration.
4. Price and citation integrity: any path where a model-written amount, an unvalidated reply or a citation to the wrong document could reach a customer.
5. Privacy: PII and health data reaching storage, logs or models contrary to the docs; retention and erasure gaps.
6. The web client: state handling, error paths, polling rules, token storage, XSS.
7. Tests and evals that do not test what they claim, and important untested paths.
8. Operability: compose/Docker/CI problems that would break the README quick start or CI.

## Output format

Return Markdown only. Start with a 3-5 sentence overall assessment. Then a numbered list of findings, most severe first. For each finding use exactly this shape:

### <n>. <short title>
- Severity: Critical | High | Medium | Low
- File(s): <path:line or path with function name>
- Finding: <what is wrong, concretely, and how it fails>
- Recommendation: <specific change>

Be concrete and verify each finding against the code before reporting it; skip findings you are not reasonably confident about, and skip style-only remarks. End with a short "Top 5 to fix" list referencing finding numbers.
