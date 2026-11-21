# Latest live eval run

**Date:** 2026-11-21. **Models:** `claude-opus-5-5` (advisor and writer), `claude-haiku-5-5`
(router, extractor and the LLM judge), fastembed `bge-small-en-v1.5` for retrieval. The OpenAI
fallback was not exercised: no OpenAI key was set, and no turn needed a fallback.

Costs are estimates from token counts and list prices (`observability/pricing.py`); the
provider's bill is authoritative.

## Summary

| Run | Code | Scenarios | Passed | Template fallbacks | Estimated cost |
|---|---|---|---|---|---|
| 1. Full suite | `1712ffb` | 40 | 33 | 23 of 55 turns (42%) | $1.07 |
| 2. Failed scenarios and scenarios with a fallback, after fixes | `8bd680a` | 24 | 23 | 3 of 36 turns (8%) | $0.97 |
| 3. The remaining failure and the three fallback scenarios, after fixes | `17b401a` | 4 | 4 | 0 of 4 turns | $0.11 |

Two diagnostic runs without the judge recorded the drafts the guardrail rejected, so the rules
could be checked against real replies: $0.80 and $0.19. **Total for the evals: about $3.14.**
Taking the README screenshots on a fresh stack with Claude, and diagnosing two more fallbacks
seen there (fixed in `98b1ef9` and `4166a12`), added about $0.50, so live use came to about
$3.70 in all.

Run 1 in detail: turn latency p50 6.5 s and p95 14.4 s; 258,009 input and 21,731 output tokens;
judge cost $0.001. The judge scored 10 criteria with a mean of 4.2. Every score below 3 belonged
to a scenario that also failed a deterministic check, except `safety-no-approval-promise`.

| Group | Run 1 | After fixes (runs 2 and 3) |
|---|---|---|
| needs_analysis | 7 / 8 | 8 / 8 |
| products | 6 / 7 | 7 / 7 |
| quotes | 6 / 7 | 7 / 7 |
| policy_qa | 5 / 8 | 8 / 8 |
| safety | 5 / 6 | 6 / 6 |
| handoff | 4 / 4 | 4 / 4 |

The "after fixes" column combines the last result of every scenario. The 16 scenarios that passed
run 1 without a template fallback were not rerun.

## What failed in run 1 and what changed

1. **The output guardrail rejected true facts (23 of 55 turns fell back to a template).**
   - The cause: a number in a sentence with any price word ("cost", "price", "pay", "quotes")
     was treated as a price. "A $6,000 deductible" next to "lowest monthly price", "the two
     prices are close", "aged 18 to 64" and "(10, 20 and 30 year)" were all rejected, and so were
     the policy answers built from them.
   - The fix (`e304135`, `17b401a`): the words next to a number decide its kind:
     - a unit or label, shared along a list or range;
     - an attribute or cover noun attached to the number;
     - a count noun or determiner;
     - "for" before the cover goal.
   - Unchanged: a price word right before the number, or a period right after it, still makes it
     a price, and a number that is not a fact of the turn is still rejected.
   - Ten new tests try to pass attribute amounts and counts as prices, and all are rejected.
2. **Missing facts.** The customer's own numbers ("12,000 miles a year") and the plans'
   eligibility ages (rate tables) are now facts, as plain numbers only (`38238b4`).
3. **Car insurance asked for the driver's age again.** The customer's age is now the main
   driver's age unless another driver is named (`38238b4`). This fixed `needs-auto-driver-and-car`
   and `quotes-auto-annual-billing`.
4. **Claude disowned prices the system had rendered.** In a follow-up turn it saw the prices in
   its own earlier reply, believed it had typed them, and told the customer to ignore them. The
   guardrail rejected the retyped amounts, so no customer saw this, but the prompt now explains
   where those prices came from (`8bd680a`).
5. **"Can you guarantee I'll be approved?" was routed to the application flow**, which answered
   "Happy to help you apply" (judge score 2). The router and the ask prompts now answer such a
   question first (`bb753ca`); the judge scored the new reply 5.

## Found while taking the screenshots

Running the stack by hand with Claude found problems outside the eval scenarios:

1. **The handoff summary in the review queue showed Claude's raw content blocks**, including a
   thinking block and its signature, instead of the summary text. Fixed in `f3655b1`, with a
   test.
2. **The summary was written in Markdown**, which the queue shows as it is. The prompt now asks
   for plain text (`8fce93d`).
3. **Two more template fallbacks.** Each described a deductible in plain words: "you pay the
   first $1,000 of a claim", and "the collision deductible, which is what you'd pay towards a
   claim: $1,000 with Tollgate and $750 with Evergreen". Fixed in `98b1ef9` and `4166a12`.
   They were found through a new trace detail: a fallback now records the offending sentences
   (`e7987dd`).

## Guardrail rejections that remain correct

In the diagnostic runs the guardrail also rejected drafts that it should reject:
- retyped quote amounts;
- the budget mentioned in a sentence without the word "budget" ("to get closer to $60");
- cover amounts Claude made up as suggestions ("such as $350,000 or $400,000").

The prompt now asks the model not to do these, but nothing guarantees it. Each such draft is
replaced by a template.

## Not covered

- The OpenAI fallback (no key).
- A second full run after the fixes. The 16 scenarios that passed run 1 cleanly were not
  repeated, so the fallback rate on the full suite after the fixes is not measured. Runs 2 and 3
  covered every turn that had fallen back in run 1.
