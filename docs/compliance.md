# Compliance notes

PolicyPilot is a **portfolio demo**. Its carriers, products, rates and documents are fictional, and
all data is synthetic. This page records how the design treats the rules a real deployment would
face. It is not legal advice. A real deployment needs counsel in each market it serves.

## Positioning

PolicyPilot is a copilot for a licensed agency (ADR-0002):

- it informs, compares and drafts;
- humans approve anything binding;
- prices come from carriers' rate tables through a deterministic engine (ADR-0003), never from a
  model.

## EU AI Act (Regulation (EU) 2024/1689, as amended by Regulation (EU) 2026/1744)

The provisions and dates below are verified (verified-facts.md). The **classification of this
system is a design hypothesis, not an established conclusion.**

| Topic | Position |
|---|---|
| **Annex III 5(c)**: AI systems intended for risk assessment and pricing of natural persons in life and health insurance (high-risk; obligations from **2027-12-02**) | **Design intent: stay outside the intended purpose in 5(c).** No model sets prices or assesses individual risk: premiums come from deterministic code over carrier rate tables, eligibility rules are deterministic, and a licensed human reviews every application. **The model still influences the outcome.** It extracts the rating inputs from conversation (age, coverage goal, region), selects which products to show and frames recommendations. Whether a combined system like this falls under 5(c), or under the provider and deployer duties elsewhere in the Act, depends on how its intended purpose is defined and documented. That question is **unresolved** for a real deployment and needs legal review. Mitigations already in place: the extracted inputs are shown to the customer in the application summary and to the agent in the snapshot; quotes are reproducible from inputs and rate-table version; every decision has an audit trail. |
| **Article 50(1)**: users must be told they are interacting with an AI system (applies from 2026-08-02) | Every conversation starts with the disclosure on both channels (`conversations.ai_disclosed_at`). The UI labels assistant messages "AI assistant" and human messages "Agent". |
| General-purpose AI model obligations | These fall on the model providers (Anthropic, OpenAI). |
| AI literacy (Article 4) | A real agency trains the staff who review in the dashboard. Phase 2 will add an agent guide. |

## Insurance distribution

- **EU IDD, demands and needs:** the needs profile and the quote basis are frozen in an immutable
  **application snapshot** at submission (`application_snapshots.needs_profile`). The agent reviews
  that snapshot, and "approved" refers to that exact version (data-model.md).
- **Licensing:** the assistant provides information and estimates. Advice and sales stay with
  licensed agents, reached through the always-available handoff.
- **US states:** the NAIC Model Bulletin on insurers' use of AI expects governance, testing and
  documentation. The ADRs, eval reports, audit log and run traces are the starting evidence.
- **Gender:** never a rating factor (the EU's gender-neutral pricing rule, Test-Achats).

## Data protection (GDPR-style practices)

| Principle | Implementation |
|---|---|
| Transparency | The first message gives the AI disclosure, the purpose and a link to the privacy notice (`/privacy`, a demo notice in the web app). |
| Lawful basis and consent evidence | Contact details are collected only in the application or contact-request flow, after a purpose statement. Consent to be contacted about that request is stored on the lead (`contact_consent_at`, `contact_consent_source`, `consent_text_version`). Marketing consent is separate, never requested in Phase 1, and defaults to false. |
| Minimisation | The assistant never requests card, bank, ID or health data. Volunteered identifiers **and health details** are removed at ingress before storage or any model call. Contact details are kept for the agent but never sent to a model. Life quotes show smoker and non-smoker variants instead of asking about smoking. |
| Special-category data (health) | Not processed by the assistant. Volunteered health sentences are removed, and the customer is offered a private call with an agent. **Known limitation:** the keyword detector can miss some phrasing. Live eval cases measure this, and a real deployment would need a stronger classifier and a DPIA. |
| Retention | Inactive conversations close after 30 days, pending reviews expire after 14 days, closed conversations are deleted after 180 days, and sales records after 24 months unless won (data-model.md#retention-and-erasure). |
| Rights | `policypilot customers export <id>` and `erase <id>` cover every store in the erasure inventory, including LangGraph checkpoints, and revoke the customer's tokens. Both are audit-logged. |
| Processors | Anthropic (Claude API) and OpenAI (fallback, when enabled) for the model; Meta (WhatsApp); AWS (hosting, later). A real deployment needs DPAs and a record of processing. No other trace processor is used in Phase 1. |
| International transfers | The processors are US-based. A real EU deployment relies on the providers' transfer mechanisms and should consider regional data-residency options. |
| Security | security-and-privacy.md |

## WhatsApp Business Platform

- **AI Providers policy** (in effect for existing users since 2026-02-16): the assistant is
  task-specific and serves the agency's own customers, which Meta says is not affected. Out-of-scope
  requests are refused.
- **Messaging Policy:** the assistant never asks for sensitive identifiers or health information,
  and removes them if volunteered. Escalation is always available ("agent" or a button), and STOP is
  honoured.
- **The 24-hour window and templates** are enforced in the adapter. Phase 1 uses only factual
  utility templates and sends no marketing.
- **Pricing:** messages have been priced per message since 2025-07-01. Since 2026-10-01, each number
  gets 1,000 free service messages a month.

## Component licences

| Component | Licence | Note |
|---|---|---|
| n8n | Sustainable Use License | Internal business use is fine. The project does not resell or host n8n for others. |
| pgvector, PostgreSQL | PostgreSQL License | |
| PolicyPilot | MIT | |

## Open points for a real deployment

1. Legal classification under the AI Act of the system as a whole (see above), especially if any
   ML enters pricing or underwriting.
2. Producer licensing, and the line between advice and information, per market and product line.
3. DPAs, a DPIA for the chat channels, a full privacy notice, and a stronger health-data classifier.
4. WhatsApp business verification and template approval.
5. Complaints handling and record keeping under local insurance rules.
