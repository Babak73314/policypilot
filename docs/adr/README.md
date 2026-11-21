# Architecture decision records

Each ADR records one decision: its context, the decision itself, its consequences and the
alternatives considered. Once a decision has been implemented, its ADR is immutable. To change
it, write a new ADR that supersedes the old one and set the old one's status to
`Superseded by ADR-NNNN`.

All ADRs below were revised once on 2026-11-20, before implementation, to apply the
independent design review ([review 01](../reviews/01-docs-review.md)). The revisions reference
the finding numbers.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-monorepo-and-toolchain.md) | Monorepo with uv (Python) and npm (web) | Accepted |
| [0002](0002-agency-copilot-with-human-approval.md) | Position the product as an agency copilot; a human approves anything binding | Accepted |
| [0003](0003-deterministic-quote-engine.md) | Prices come only from a deterministic rating engine | Accepted |
| [0004](0004-single-postgres-with-pgvector.md) | One PostgreSQL database with pgvector for data, vectors and checkpoints | Accepted |
| [0005](0005-langgraph-supervisor-and-interrupts.md) | LangGraph supervisor graph with specialist nodes and interrupts for human review | Accepted |
| [0006](0006-claude-models-and-structured-output.md) | Claude models, routing and structured output | Accepted |
| [0007](0007-fallback-provider.md) | OpenAI as the fallback provider, Bedrock optional | Accepted |
| [0008](0008-local-embeddings.md) | Local embeddings with fastembed | Accepted |
| [0009](0009-whatsapp-channel-adapter.md) | WhatsApp Cloud API behind a channel adapter, with a local simulator | Accepted |
| [0010](0010-mcp-server.md) | MCP server with the official SDK, mounted in the API | Accepted |
| [0011](0011-observability.md) | Built-in run traces, optional Langfuse | Accepted |
| [0012](0012-n8n-via-outbox.md) | n8n automations fed by a transactional outbox | Accepted |
| [0013](0013-internationalisation-groundwork.md) | Internationalisation groundwork for a second left-to-right language | Accepted |
| [0014](0014-authentication.md) | Authentication for customers, agents and machine clients | Accepted |
| [0015](0015-shared-aws-infrastructure.md) | Start on a shared Aurora cluster, keep the move-out cheap | Accepted |
| [0016](0016-frontend-stack.md) | One React app for the chat, the WhatsApp simulator and the agent dashboard | Accepted |
| [0017](0017-scripted-fake-model.md) | A scripted fake model for tests and keyless demos | Accepted |
