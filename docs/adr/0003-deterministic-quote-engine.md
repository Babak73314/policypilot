# ADR-0003: Prices come only from a deterministic rating engine

- Status: Accepted
- Date: 2026-11-20

## Context

Language models make arithmetic mistakes and can invent numbers. A wrong premium shown to a
customer is a conduct risk, and it would undermine the whole demo.

## Decision

- A pure Python package (`policypilot.domain.rating`) computes every premium from **versioned
  rate tables** stored in the database (`rate_tables`, immutable once published). It works in
  `Decimal` and rounds once, half-up, to the currency's minor unit.
- Each product line has its own rater: term life, individual health and private auto. A rater
  takes a validated input model and returns a `QuoteResult`. The result holds the premium, the
  billing period, the currency, a **factor-by-factor breakdown**, the rate table version and an
  engine version.
- Eligibility rules (age limits, coverage bounds) are deterministic too. They run before rating,
  and an ineligible input gets a reason code, never a price.
- The LLM reaches prices only through the `get_quote` tool and the MCP tool of the same name. Both
  call the engine and **persist** the quote with its inputs.
- **The model never writes premiums.** It writes `{{quote:<id>}}` placeholders, and the server renders
  them from the stored quote with the product name, billing period and labels. Annual premiums are
  shown together with an explicitly labelled monthly equivalent. A **final deterministic
  validation** rejects any other literal money amount, except monetary attributes of products
  returned by tools in the same turn. If validation fails, the reply is rebuilt from templates
  (agent-design.md; review 01, finding 12).
- Rate tables are selected by publication and effective period, without overlap, and can never be
  updated or deleted once published (data-model.md).
- Every quote carries a disclaimer: it is an estimate, not an offer, and the final premium depends
  on the carrier's underwriting.
- The engine never uses sex or gender as a rating factor. Life quotes show both the smoker and the
  non-smoker rate, so the assistant never has to ask about health habits in chat (see `docs/compliance.md`).

## Consequences

- Quotes are reproducible: the same inputs and the same rate table version always give the same
  premium. This is covered by unit tests and golden-file tests.
- Rate tables are fictional seed data. A real deployment would replace them with carrier raters
  through an adapter (Phase 3).
- Replies read a little more templated around prices. In exchange, a premium can never be attached
  to the wrong product or period.

## Alternatives considered

- **Let the model calculate from the rate table in context:** not reproducible, and errors are
  possible.
- **Spreadsheet-style formulas in the database:** harder to test and review than typed Python.
