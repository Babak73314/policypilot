# ADR-0017: A scripted fake model for tests and keyless demos

- Status: Accepted
- Date: 2026-11-20

## Context

Tests and CI must pass with no API keys. The quick start should also show the whole flow
(chat, quotes, review, WhatsApp simulator) to someone who has not set up a Claude key.
Generic fake models in LangChain replay a fixed list of messages. That breaks as soon as
routing or tool order changes, and it does not support `with_structured_output(method="json_schema")`.

## Decision

- Implement a `ScriptedChatModel`, a `BaseChatModel` subclass in `policypilot.agents.llm.fake`.
  - It answers **by rules, not by position**. It reads the latest user message and the bound
    tools or schema and returns a plausible result:
    - a router classification from keywords;
    - extraction of fields such as age, budget and household from simple patterns;
    - tool calls for search, compare and quote when the advisor asks;
    - a templated reply that copies amounts exactly from tool results.
  - It implements `bind_tools` and overrides `with_structured_output` to return validated Pydantic
    objects, so node code uses exactly the same calls as with Claude.
  - It streams token-sized chunks, so the SSE path is exercised.
- `LLM_PROVIDER=fake` selects it, and it is the default when `ANTHROPIC_API_KEY` is empty. The UI
  then shows a "demo mode (scripted model)" badge, so nobody mistakes it for Claude.
- Tests can inject a **per-test script** (a list of expected responses) where exact control matters.
- Live-model evals are a separate, opt-in command (`make eval-live`) that needs `ANTHROPIC_API_KEY`.

## Consequences

- CI is deterministic and free. The quick start works with no keys.
- The scripted model is not a quality signal. Only the live eval suite measures real model behaviour,
  and the docs say so plainly.
- Keeping the fake in step with prompt and schema changes is ongoing work. Its rules stay simple and
  are unit-tested.

## Alternatives considered

- **Recorded cassettes of real API calls:** brittle when prompts change, and they leak real model
  output into fixtures.
- **A local open-weights model:** heavy, slow on CI runners, and still not deterministic enough.
