# ADR-0013: Internationalisation groundwork for a second left-to-right language

- Status: Accepted
- Date: 2026-11-20

## Context

PolicyPilot is English-only. A second language may come later; it is not chosen yet, but it
will be **left-to-right**, for example German or French. The aim is to make adding it
cheap without slowing Phase 1 or building translations now.

## Decision

The project keeps five cheap habits:

1. **UI text in resource files.** The web app uses `i18next` and `react-i18next`, with strings in
   `apps/web/src/locales/en/*.json` and no hard-coded user-facing text. An ESLint rule
   (`i18next/no-literal-string` in JSX) enforces this.
2. **Locale is data.** `customers.locale` and `conversations.locale` hold a BCP 47 tag (default `en`).
   The graph state carries `locale`, and every prompt receives a `reply_language` parameter derived
   from it. Prompts never say "reply in English" as a constant.
3. **Translatable catalog and templates.** Customer-facing catalog text lives in
   `product_texts (product_id, locale, name, summary, highlights)`. Channel templates live in
   `message_templates (key, channel, locale, body, meta_template_name, meta_language_code)`.
   Lookups fall back to `en`.
4. **Locale-aware formatting.** The API formats money, dates and numbers with Babel
   (`format_currency`, `format_date`) and the conversation locale. The web app uses `Intl.NumberFormat`
   and `Intl.DateTimeFormat` with the i18next language. Amounts travel as minor units plus a currency
   code, never as preformatted strings.
5. **Policy documents carry a `locale` column.** Retrieval filters on it.

Not done now, by decision: translations, a language switcher, RTL support (no CSS logical
properties or direction flipping is needed), and multilingual embeddings (ADR-0008).

## Consequences

- Adding German, for example, means: translation JSON files, `product_texts` and `message_templates`
  rows, approved WhatsApp templates in that language, translated policy documents, a multilingual
  embedding model with a re-index, and eval scenarios in that language. No schema change is needed.
- The price-integrity guardrail (ADR-0003) parses amounts with the locale in mind, so it keeps
  working for other number formats.

## Alternatives considered

- **Hard-coding English now and refactoring later:** the refactor touches every component and
  prompt. The habits above cost almost nothing today.
