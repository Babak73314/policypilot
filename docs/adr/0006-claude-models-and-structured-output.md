# ADR-0006: Claude models, routing and structured output

- Status: Accepted
- Date: 2026-11-20

## Context

Claude, called directly through the Claude API, is a fixed choice. Different tasks need different
amounts of capability:

- classifying a turn and extracting profile fields: cheap, fast, high volume;
- advising, comparing and writing customer-facing replies with tool use: quality matters.

Two API facts constrain the design (`docs/reference/verified-facts.md`):

- On Claude Opus 5.5 and Sonnet 5.5, forced `tool_choice` (`any` or `tool`) returns HTTP 400.
  LangChain's default `with_structured_output` method (`function_calling`) depends on forced tool
  choice and silently degrades on these models.
- Citations cannot be combined with `output_config.format`.

## Decision

- The model is a setting for each role, not a constant in code:

  | Role | Default model | Effort | Used for |
  |---|---|---|---|
  | `router` | `claude-haiku-5-5` | default | Turn intent classification |
  | `extractor` | `claude-haiku-5-5` | default | Needs-analysis field extraction |
  | `advisor` | `claude-opus-5-5` | `medium` | Tool-calling advice, comparison, policy Q&A |
  | `writer` | `claude-opus-5-5` | `low` | Short conversational replies (questions, confirmations) |

  The research report proposed Sonnet 5.5 for the main roles. This project defaults to Opus 5.5,
  the current flagship, at a modest `effort`, because demo volume is small. Switching to
  `claude-sonnet-5-5` takes one environment variable (`LLM_ADVISOR_MODEL`, `LLM_WRITER_MODEL`), and
  the live eval suite measures the difference.
- **All structured output uses `with_structured_output(schema, method="json_schema")`**, which maps
  to native `output_config.format`. Tools are bound with `strict=True` and `tool_choice="auto"`.
  The code never sends forced tool choice. A unit test fails the build if `tool_choice="any"` or a
  tool name appears in model bindings.
- Policy Q&A does **not** use the API's citations feature. Retrieved chunks go into the prompt with
  stable IDs, and the model cites them as `[doc:<chunk_id>]` markers. The server resolves the markers
  to document, section and page. A marker that points to a chunk not retrieved in this turn, or to
  another product's document, fails validation. A policy answer with no valid citation is replaced
  by an "insufficient evidence" reply. This keeps citations working alongside tools and structured
  output.
- Premiums are never written by the model. It uses `{{quote:<id>}}` placeholders, which the server
  renders (ADR-0003).
- Model text is **not** streamed to clients token by token. Replies are validated first and then
  delivered, while progress events stream during the run (agent-design.md; review 01, finding 3).
- **Prompt caching:** the system prompt and tool definitions are stable and come first. They are
  marked with `cache_control`. Volatile data (profile, retrieved chunks) goes after the cached prefix.
- **Refusals:** the Claude API's server-side fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`) is enabled for Opus and Sonnet roles when the setting
  `LLM_SERVER_SIDE_FALLBACK=true` is on (the default). It covers refusals. Outages and errors are
  covered by the provider fallback (ADR-0007).
- **Reply language:** the reply language is a prompt parameter taken from the conversation locale
  (ADR-0013). It is never assumed.

## Consequences

- The code depends on `langchain-anthropic >= 1.7` for `method="json_schema"` and the `effort` field.
- Small models handle most calls. One advisor turn usually means one or two Opus calls.
- Marker-based citations are this project's own convention. They are tested end to end.

## Alternatives considered

- **Sonnet 5.5 for the main roles:** cheaper, and kept as a one-line switch.
- **The API's native citations:** they are incompatible with structured output, and LangChain support
  for them is awkward inside tool loops.
