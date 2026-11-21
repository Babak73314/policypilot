# ADR-0002: Position the product as an agency copilot; a human approves anything binding

- Status: Accepted
- Date: 2026-11-20

## Context

Insurance distribution is regulated. Three facts shape the product:

- Under the EU AI Act, AI systems "intended to be used for risk assessment and pricing in relation
  to natural persons in the case of life and health insurance" are high-risk (Annex III 5(c)). Their
  obligations apply from 2027-12-02 under the Digital Omnibus (Regulation (EU) 2026/1744).
- The WhatsApp Business Platform bans general-purpose AI assistants. It allows task-specific
  business assistants and requires a "prompt, clear, and direct escalation path" to a human.
- Insurance distribution rules (for example the IDD in the EU) expect a licensed person to own the
  advice and the sale.

## Decision

PolicyPilot is a **sales and service copilot for a multi-carrier agency**, not an automated
insurer or pricing engine.

- The assistant gathers needs, explains and compares products, cites policy wording and shows
  **indicative** quotes from a deterministic engine (ADR-0003). It never states final prices or
  guarantees cover.
- Nothing binding happens without a human. Submitting an application and handing a customer over
  both pause the LangGraph run (ADR-0005). A human agent then approves, edits, rejects or takes over
  in the dashboard. In Phase 1, "approved" means "ready for the agent to submit to the carrier"; no
  carrier integration exists until Phase 3.
- Customers are told at the start of every conversation, on both channels, that they are talking to
  an AI assistant (AI Act Article 50(1)).
- The assistant stays on the agency's topics: insurance products, quotes, applications and agency
  service. Other requests get a polite refusal. On WhatsApp this is also a platform requirement.

## Consequences

- The **design intent** is to stay outside Annex III 5(c): no model prices or assesses risk, and a
  human makes every decision with legal effect. These controls alone do not settle how the combined
  system is classified, because the model still extracts rating inputs and selects products. The
  classification is therefore recorded as an **unresolved hypothesis** for a real deployment
  (`docs/compliance.md`; review 01, finding 13).
- Compliance becomes part of the product story: a demo can show the approval queue and the audit log.
- Every application waits for human review, so the end-to-end flow is slower. That is acceptable for
  an agency tool.

## Alternatives considered

- **Fully automated quote-and-bind bot:** high-risk under the AI Act for life and health, against
  WhatsApp policy, and weaker as a portfolio story.
