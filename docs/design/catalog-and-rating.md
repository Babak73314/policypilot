# Catalog, seed data and the rating engine

Everything here is **fictional**. The carrier names were chosen to avoid real insurers; any
resemblance is accidental. Websites use `example.com`, and agency emails use `.test`.
The seed data lives in `data/seed/` and is loaded by `policypilot seed`. The loader validates
everything before anything is written (`policypilot.seed.loader`).

## The agency

**Harbor & Pine Insurance Agency** is a fictional independent agency. It works in USD with
locale `en-US`, and sells life, individual health and private auto cover from four fictional
carriers. Regions are abstract codes (`north`, `central`, `south`), so the demo does not imply
rules for any real jurisdiction.

## Carriers and products (13)

| Carrier | Code | Products (code) |
|---|---|---|
| Kestrel Life Assurance | `KESTREL` | Term Life 10, 20, 30 (`KESTREL-TERM-10/20/30`) |
| Solace Health Cooperative | `SOLACE` | Bronze Saver, Silver Choice, Gold Complete (`SOLACE-BRONZE/SILVER/GOLD`) |
| Tollgate Auto Insurance | `TOLLGATE` | Liability Basic, Standard Driver, Comprehensive Plus (`TOLLGATE-LIABILITY/STANDARD/COMPREHENSIVE`) |
| Evergreen Mutual | `EVERGREEN` | Term Life 20 Flex, Silver Family Health, Auto Standard Plus, Auto Comprehensive (`EVERGREEN-TERM-20`, `EVERGREEN-SILVER-FAMILY`, `EVERGREEN-AUTO-STANDARD`, `EVERGREEN-AUTO-COMPREHENSIVE`) |

The comparable `attributes` per line are below. Money values are minor units (cents).

| Line | Attributes |
|---|---|
| Life | `term_years`, `coverage_min_minor`, `coverage_max_minor`, `max_issue_age`, `conversion_option`, `accelerated_benefit` |
| Health | `tier`, `deductible_minor`, `out_of_pocket_max_minor`, `coinsurance_pct`, `network` (`narrow` \| `broad`), `telehealth`, `dental_addon` |
| Auto | `coverage_level`, `liability_limit_minor`, `collision_deductible_minor` (null = no collision cover), `comprehensive`, `roadside`, `rental_car` |

## Rating engine

`policypilot.domain.rating` has one rater per line (`rate(inputs, table, currency=…)`). Its rules:

- **Units:** every money input and output is an integer in **minor units**. Rates and factors are
  `Decimal`, written as JSON/YAML **strings** (for example `"0.140"`, at most 6 decimal places), so
  a float never touches a rate.
- **Rounding:** each line's premium is computed from unrounded intermediates and rounded **once**,
  `ROUND_HALF_UP`, to a whole minor unit. Breakdown lines show each intermediate rounded half-up for
  display, so they may not add up exactly to the total. The total is authoritative.
- **Monthly equivalent:** for annual products, `round_half_up(annual / 12)`. It is always labelled
  "about … per month". For monthly products, it equals the premium.
- **Validation:** inputs are strict Pydantic models (unknown fields are rejected). An age outside
  every band of the table, or a missing factor, is a `RatingError`: a seed or programming bug, never
  a silent default. Eligibility failures return `Ineligible` with reason codes.
- `ENGINE_VERSION` (currently `2026.11.1`) is stored on every quote and changes whenever the logic does.

### Input models

| Model | Fields (bounds) |
|---|---|
| `LifeQuoteInputs` | `line="life"`, `age` (0–120), `coverage_minor` (> 0) |
| `HealthQuoteInputs` | `line="health"`, `age` (0–120), `region` (`north` \| `central` \| `south`), `dependents` (0–12) |
| `AutoQuoteInputs` | `line="auto"`, `driver_age` (0–120), `vehicle_class` (`economy` \| `standard` \| `premium` \| `suv`), `annual_mileage_band` (`low` < 8,000 mi, `medium` 8,000–15,000, `high` > 15,000), `claims_last_5y` (0–20) |

The schema bounds are deliberately wider than eligibility, so that an out-of-range age is reported
as *ineligible* with a reason instead of being rejected as invalid input. A life product's term is
a product attribute, not an input. The assistant picks products whose `term_years` matches the
customer's goal.

### Term life (monthly)

```text
bands          = 18-29, 30-39, 40-49, 50-59, 60-65   (inclusive; per product table)
rate           = rate_per_1000[band][non_smoker | smoker]           (Decimal, per 1,000 of cover per month)
premium_minor  = round_half_up(coverage_minor / 1000 × rate + policy_fee_minor)
eligibility    = min_issue_age ≤ age ≤ max_issue_age  and  min_coverage ≤ coverage ≤ max_coverage
```

`coverage_minor / 1000 × rate` is the same as dollars-per-1,000 × rate, expressed in cents.
Both variants (`non_smoker`, `smoker`) are always returned; the headline premium is the
non-smoker one.

**Worked example** (`KESTREL-TERM-20`, age 40, $500,000):
50,000,000 / 1000 × 0.140 = 7,000, plus a fee of 500, gives **7,500 ($75.00 per month)**.
The smoker rate is 0.336, which gives 16,800 + 500 = **17,300**.

### Individual health (monthly)

```text
bands          = 18-29, 30-39, 40-49, 50-64
premium_minor  = round_half_up(base_minor[band] × region_factor[region] + dependents × dependent_minor)
eligibility    = min_age ≤ age ≤ max_age  and  dependents ≤ max_dependents
```

Each health product has its own base table. The tier is a product attribute, not a factor.

**Worked example** (`SOLACE-SILVER`, age 34, `south`, 2 dependents):
29,000 × 0.92 = 26,680, plus 2 × 19,000, gives **64,680 ($646.80 per month)**.

### Private auto (annual)

```text
driver age bands = 18-24, 25-29, 30-64, 65+
premium_minor    = round_half_up(base_annual_minor × driver_age_factor[band] × vehicle_class_factor[class]
                                 × mileage_factor[band] × claims_factor[0 | 1 | 2+])
eligibility      = min_driver_age ≤ driver_age ≤ max_driver_age  and  claims_last_5y ≤ max_claims_last_5y
```

The coverage level is a product attribute, not a factor.

**Worked example** (`TOLLGATE-STANDARD`, driver 45, SUV, high mileage, 1 claim):
88,000 × 1.00 × 1.15 × 1.20 × 1.25 = **151,800 ($1,518.00 per year)**. The monthly equivalent is
151,800 / 12 = **12,650 (about $126.50 per month)**.

### Golden tests

`apps/api/tests/unit/rating/golden.yaml` holds hand-computed cases: band edges, rounding,
ineligibility and variants. Property tests (Hypothesis) check that premiums are positive, that the
smoker rate is never below the non-smoker rate, and that health premiums never decrease with age or
dependents.

## Policy wordings and chunking

Each product has a wording in `data/seed/documents/<product_code>.md`, about 2–4 pages, with
numbered sections:

1. Definitions
2. Cover
3. Eligibility
4. Exclusions
5. Waiting periods
6. Claims
7. Cancellation
8. Complaints

Each line has deliberate differences between carriers, so comparison and Q&A have something real
to find. Examples: waiting periods, exclusions, roadside cover, conversion options.

`policypilot seed` renders each wording to PDF with fpdf2 (into `.data/documents/`, git-ignored)
and ingests the PDF:

1. Extract text per page with pypdf.
2. Split on numbered headings (`^\d+(\.\d+)? `) into sections. Chunks **never cross a section**.
3. Within a section, each numbered clause (`4.2 ...`) starts a new chunk, so a chunk is about one
   topic and a citation points at one clause. Short consecutive clauses are packed together up to
   **110 words**; a longer clause is split with a **20-word overlap** (well below bge-small's
   512-token limit).
4. Record `section_ref` (for example `4.2 Exclusions`), `page_start` and `page_end`.
5. Embed each chunk as "`<product name>, <section>: <clause text>`" (the stored `content` is the
   clause alone), so a clause such as "4.1 Suicide." still carries its product and section.
6. Insert the document version and its chunks and flip `is_current`, all in one transaction
   (data-model.md). `policypilot seed --reindex` re-embeds unchanged documents too.

## Templates (`data/seed/message_templates.yaml`)

| Key | Channels | Purpose |
|---|---|---|
| `ai_disclosure` | web, whatsapp | First message: AI assistant, fictional demo, estimates only, human review, privacy link |
| `review_pending_ack` | web, whatsapp | Customer writes while the thread is paused |
| `application_update` | web; whatsapp as a Meta utility template | Review decision (on WhatsApp, outside the 24-hour window) |
| `agent_follow_up` | whatsapp (Meta template, utility) | Human reply outside the window |
| `handoff_started` / `handoff_returned` | web, whatsapp | Handoff transitions |
| `out_of_scope` | web, whatsapp | Polite refusal listing what the assistant can do |
| `sensitive_data_warning` | web, whatsapp | After redacting a card, bank or ID number |
| `health_data_notice` | web, whatsapp | After removing volunteered health details; offers a private call with an agent |
| `insufficient_evidence` | web, whatsapp | A policy question the documents do not answer |
| `quote_fallback`, `quote_disclaimer` | web, whatsapp | Templated quote rendering and the estimate disclaimer |
| `assistant_unavailable` | web, whatsapp | Both model providers failed or the turn deadline passed; offers an agent |
| `unsupported_content` | whatsapp | Image, audio or location received |
| `rate_limited` | web, whatsapp | Daily model-call budget reached |
| `opt_out_confirmed` | whatsapp | Reply to STOP |
| `review_expired` | web, whatsapp | A pending review expired after 14 days |
