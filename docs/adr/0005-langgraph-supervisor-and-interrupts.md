# ADR-0005: LangGraph supervisor graph with specialist nodes and interrupts for human review

- Status: Accepted
- Date: 2026-11-20

## Context

A conversation moves through several jobs: understanding the request, needs analysis,
product advice with retrieval, quoting, drafting an application and handing over to a human.
Each job needs different prompts, tools and models. Human review has to pause a conversation
for minutes or days and survive restarts.

## Decision

- Build one LangGraph `StateGraph` per conversation thread, with `thread_id` set to the conversation ID.
  - A **supervisor** (`route`) classifies each turn. It applies deterministic rules first (an explicit
    "talk to a human", an application awaiting changes) and then a small-model classifier with
    structured output.
  - **Specialist nodes** each own one job: `needs_analysis`, `advisor` (a bounded tool-calling loop
    over catalog search, profile-aware quoting, comparison and policy retrieval), `application` and
    `handoff`.
  - **`guard_output`** runs on every turn: at most one model repair, then a final deterministic
    validation, then a template fallback. Input sanitisation happens **before** the graph, at
    ingress, so raw identifiers never reach checkpoints.
- Human review uses **`interrupt()`** in a node that has **no other side effects**. The review task,
  outbox event and status changes are created in a preceding `prepare_review` node, idempotently by a
  stable `review_key`, because an interrupted node runs again from the start on resume. The dashboard
  resumes with `Command(resume=decision)`, and the server adds the customer messages that arrived
  while the thread was paused.
- Persist state with **`AsyncPostgresSaver`** (ADR-0004), and compact history every turn so
  checkpoints stay bounded.
- **One lock protocol** serialises everything that writes to a conversation: a session advisory
  lock taken with try-lock on a dedicated pooled connection. WhatsApp events are drained in their
  durable arrival order (agent-design.md; review 01, findings 1, 10 and 26).
- Keep the state small and serialisable: a TypedDict with messages, locale, channel, the customer
  profile, selected products, quote IDs and pending-review metadata. Large objects (documents, quote
  details) stay in the database and are referenced by ID.

## Consequences

- Each specialist can be tested on its own with a fake model (ADR-0017).
- A thread paused for review does not run the graph when new customer messages arrive. The API
  applies deterministic control rules (STOP, withdraw, escalate) and otherwise stores the message and
  sends an acknowledgement, until a human resumes the thread (`docs/design/agent-design.md`).
- The graph topology lives in code. It is documented as a Mermaid diagram in the agent design doc.

## Alternatives considered

- **One large ReAct agent with every tool:** harder to test, harder to guarantee a pause before
  anything binding, and harder to explain.
- **A `create_agent` prebuilt with middleware:** good for a single agent, but the routing and
  review flow are clearer as an explicit graph.
- **Separate LangGraph deployments per agent:** more operational overhead with no benefit at this size.
