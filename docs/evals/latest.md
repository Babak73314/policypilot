# Latest live eval run

**Date:** 2026-11-21. **Models:** `claude-opus-5-5` (advisor and writer), `claude-haiku-5-5`
(router, extractor and the LLM judge), fastembed `bge-small-en-v1.5` for retrieval. The OpenAI
fallback was not exercised: no OpenAI key was set, and no turn needed a fallback.

Costs are estimates from token counts and list prices (`observability/pricing.py`); the
provider's bill is authoritative.

## Latest full run (code `dd38b52`)

**38 of 40 scenarios passed, and the output guardrail replaced 1 of 55 replies with a template
(1.8%; one more draft was repaired).** Estimated cost $1.19 (judge $0.001). Turn latency p50
6.3 s and p95 15.2 s; 281,508 input and 24,031 output tokens. The judge scored 10 criteria with
a mean of 4.1.

| Group | Passed |
|---|---|
| needs_analysis | 7 / 8 |
| products | 7 / 7 |
| quotes | 7 / 7 |
| policy_qa | 7 / 8 |
| safety | 6 / 6 |
| handoff | 4 / 4 |

The two failures, both fixed in `03cc27b` and passing on a live rerun of those two scenarios
($0.07; the judge scored them 5, 5 and 4):

1. `rag-kestrel-suicide`: the only template fallback. A correct, cited answer said "All three
   Kestrel term policies (10, 20 and 30) don't pay out ...", and the product versions in
   brackets were read as prices. Numbers in brackets right after a plural product noun are now
   labels; they must still be facts of the turn.
2. `needs-life-extracts-casual-facts`: a judge mistake. Claude's reply was correct (it asked for
   the cover amount and term only), but the judge took the only assistant line of a one-turn
   conversation for an earlier message and scored it 1. The judge prompt now shows the final
   reply on its own.

A first attempt at this run stopped after 22 passing scenarios ($0.84): the judge wrote a reason
longer than the schema allowed and the runner crashed. A judge error now fails only its scenario
(`dd38b52`). With the multi-turn rerun described below, these runs cost about $2.45.

The rest of this page records the first live run of the day and what changed after it.

## First live run and reruns

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

## Found while recording the demo video

- **Claude still disowned rendered prices.** Despite the prompt rule (`8bd680a`), a policy answer
  in the recording began "I also need to correct my last reply. The smoker prices I gave didn't
  come from the quote tool, so please ignore them." The prices were correct engine output. The
  fix is structural (`a217db3`): the history the model reads has a marker where a quote was
  rendered, so it never sees those prices as its own text; a reply containing the marker is
  rejected. All seven multi-turn scenarios then passed live with no template fallback ($0.35).
- The web consent message showed the raw path "/privacy"; it now points at the Privacy link at
  the top of the chat.

## Guardrail rejections that remain correct

In the diagnostic runs the guardrail also rejected drafts that it should reject:
- retyped quote amounts;
- the budget mentioned in a sentence without the word "budget" ("to get closer to $60");
- cover amounts Claude made up as suggestions ("such as $350,000 or $400,000").

The prompt now asks the model not to do these, but nothing guarantees it. Each such draft is
replaced by a template.

## Not covered

- The OpenAI fallback (no key).
