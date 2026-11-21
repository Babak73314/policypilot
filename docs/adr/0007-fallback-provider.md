# ADR-0007: OpenAI as the fallback provider, Bedrock optional

- Status: Accepted
- Date: 2026-11-20

## Context

The brief requires a second model provider as a fallback when the Claude API is
unavailable, rate-limited or failing. The research report proposed Claude on Amazon
Bedrock or OpenAI.

Claude on Amazon Bedrock needs model access to be enabled for the AWS account, and that access
was not available for the planned target account when this was written. A fallback that depends on
it could fail exactly when it is needed.

## Decision

- **The default fallback is OpenAI** through `langchain-openai`, using the Responses API.
  The role mapping is:

  | Role | Primary | Fallback |
  |---|---|---|
  | `router`, `extractor` | `claude-haiku-5-5` | `gpt-6-luna` |
  | `advisor`, `writer` | `claude-opus-5-5` | `gpt-6.2-sol` |

- Every role builds `primary.with_fallbacks([fallback], exceptions_to_handle=...)`. The handled
  exceptions are connection errors, timeouts, 429 and 5xx, after the primary's single retry.
  400-class errors are **not** handled: they are bugs and must surface. Each attempt reserves from
  the daily model-call budget, and fallback calls count too. If both providers fail, the turn ends
  with a deterministic template and a handoff (agent-design.md). Replies are validated before
  delivery, so a partial primary answer is never shown.
- When a request falls back, the run trace records it (`model_provider`, `fallback_used`), and a
  counter feeds the dashboard.
- **Bedrock is deferred.** It is not implemented in Phase 1, because the target account cannot use it
  (review 01, finding 35). The provider factory keeps the extension point. `langchain-aws`'s
  `ChatAnthropicBedrock` (model IDs `anthropic.claude-…`, IAM role auth) can be added once the
  account has access to the models.
- `LLM_FALLBACK_PROVIDER=none` disables fallback. This is the setting for local development
  without an OpenAI key.
- The prompts stay provider-neutral: no Claude-only XML tricks, and structured output is defined by
  Pydantic schemas. The live eval suite runs against both providers (`make eval-live PROVIDER=openai`).

## Consequences

- A true multi-vendor fallback: one vendor's outage does not take the assistant down.
- One more secret (`OPENAI_API_KEY`) and one more vendor in the data-processing inventory
  (`docs/compliance.md`).
- Behaviour differs a little between providers. The eval suite is the guard against that, not
  identical prompts.

## Alternatives considered

- **Bedrock as the default:** not available on the target account (see context).
- **Server-side fallback only:** it covers refusals, not outages.
