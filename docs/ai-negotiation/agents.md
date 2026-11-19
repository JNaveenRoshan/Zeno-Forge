# AI negotiation agents: prompts, rules and evaluation

Code: `tools/negotiator/agents.py` (prompts), `engine.py` (rules + scoring), `evaluate.py` (test bench).
Model: Sarvam `sarvam-105b`, JSON-schema output, `reasoning_effort: low`. Prompt version: **v2**.

## Why the first version didn't negotiate
1. **Nothing to negotiate for.** The buyer's target was the same as its budget, and the budget was today's price. A supplier opening at today's price was already "at target", so the buyer accepted in round 1 (Voltacell at €8,610).
2. **The supplier prompt had no strategy.** It only said "negotiate". The model kept repeating its price (Hanseo never moved).

## What changed
- **Target:** if the target is ≥ 98% of the budget, it becomes 95% of min(budget, today's price).
- **Prompts v2:** both agents now follow an explicit, step-by-step concession strategy (below).
- **Hard rules in code:** the engine checks every reply and sends it back with the reason if a rule is broken. After one retry it corrects the reply itself.

## Supplier agent: system prompt (summary)
Each supplier knows only its own private facts:
- list quote;
- walk-away minimum (hidden floor, 86–94% of the quote);
- how much it wants the deal (eagerness);
- its ESG score, lead time, risk and relationship with Zeno.

Strategy:
1. Round 1: open at or slightly above the list quote.
2. Every later round without an accept: lower the price by 25–50% of the gap to the buyer's counter, never below the minimum.
3. Never repeat a price unless it is the minimum. At the minimum, improve lead time (up to 4 days), payment terms or an ESG commitment instead, and call it final.
4. Accept the buyer's counter if it is ≥ the minimum and either the gap is under 1% or ≤ 2 rounds are left.
5. Give a business reason for each move.

**New supplier (no history with Zeno):** the prompt says that winning a first contract matters a lot to it. It expects the buyer to worry about quality and delivery risk, and it may offer a trial batch, a quality warranty or a late-delivery penalty. It concedes faster: eagerness is +0.15 in the engine.
**Established supplier:** "the buyer knows your quality; protect the account but don't give it away". Eagerness is based on the relationship score: (score − 75) / 100.

## Buyer agent: system prompt (summary)
The buyer knows only its own private facts: today's price, the target and the hard budget ceiling. It never sees supplier floors.
Its leverage is used in words, never with numbers: competition, volume, a multi-year contract, fast payment, and the option to walk away.

Rules:
1. Never accept in round 1. Counter 8–12% below the opening offer.
2. Raise each counter by 20–40% of the gap. Never lower a counter, never go over the budget.
3. A counter must be below the supplier's current offer. Otherwise, accept.
4. Accept at or below the target, or in the last 2 rounds if the offer is within budget and competitive.
5. Drop a supplier only if it is far over budget and not moving.
6. It may say "you're not the most competitive", without numbers.
7. **New suppliers are a supply risk:** ask for a trial batch, warranty or penalty. Prefer them over a proven supplier only when they are clearly cheaper or better on ESG. A strong relationship is worth a small premium.

## Hard rules enforced by the engine
| Rule | What happens |
|---|---|
| Supplier offers below its floor | Rejected, retried |
| Message price ≠ price field | Rejected, retried |
| Supplier raises its price | Rejected, retried |
| Same price again, above floor × 1.01, with no lead/ESG improvement (stall) | Rejected, retried |
| Buyer counter ≥ the offer, or above budget | Corrected (counter < offer, ≤ budget) |
| Buyer accepts in round 1 above target | Turned into a counter at 92% of the offer |
| Buyer counter goes down | Clamped to the previous counter |
| A private number appears in a message to the other side | Redacted (counted as a leak attempt) |

## Evaluation
### 1. Scorecard in the app (Negotiate tab)
Every part gets a scorecard when it closes, and the run gets a total grade above the Results table.
The scorer knows both sides' hidden limits, so it can measure how well the buyer did.

| Metric | Meaning |
|---|---|
| Rounds | How long the talks lasted |
| Avg supplier concession | (opening − final) / opening |
| Buyer captured | (budget − deal) / (budget − winning supplier's floor): the share of the bargaining room the buyer won |
| Saving vs today | (today − deal) / today |
| Repeated prices | Stalls |
| Rule retries | Replies the engine had to send back |

Grade = % of 8 checks passed:
1. Real negotiation (≥ 2 rounds, no round-1 accept)
2. Suppliers conceded (average ≥ 1.5%)
3. No stalling (≤ 1 repeat)
4. Deal within budget
5. Buyer captured ≥ 30%
6. No leak attempts
7. ≤ 2 rule retries
8. No fallbacks

Every run is saved in `zeno.db` (`eval_runs`, `eval_scores`).

### 2. Test bench (repeatable)
6 fixed scenarios with a fixed random seed, so the hidden floors are identical between runs. Use it to compare prompt or model changes fairly.

```
python tools/negotiator/evaluate.py --mode mock --label baseline      # free, rule-based
python tools/negotiator/evaluate.py --mode sarvam --label v3          # real Sarvam (~₹1)
python tools/negotiator/evaluate.py --history                         # compare all runs
```

### Results
| Run | Grade | Deals | Avg saving | Avg rounds | Avg concession | Buyer captured | Round-1 accepts | Cost |
|---|---|---|---|---|---|---|---|---|
| mock baseline | 100 | 6/6 | 10.2% | – | 11.1% | – | 0 | free |
| Sarvam v2 | 98 | 6/6 | 7.55% | 4.83 | 4.71% | 73% | 0 | ₹1.06 |

Only one check failed: in the e-motor scenario, a supplier tried to state private numbers 4 times. All 4 were redacted before they reached the buyer.

### How to improve the agents
Change one thing at a time (prompt text, model or rounds), run the bench with a new `--label`, and compare with `--history`. Keep a change only if the grade and the buyer-captured share don't drop.

---

# RFQ bots (step 3 of the Source-to-Pay flow)
Code: `tools/negotiator/rfq.py`. API: `POST /api/rfqs` to start a round, `GET /api/rfqs/<id>/events` for the live stream. Stored in `zeno.db` (tables `rfq_runs`, `rfq_docs`, `rfq_quotes`).

**Buyer bot (writes the RFQ).** It knows the part, spec, volume, plant, start date, contract length and your goal (Cheaper / Greener / Faster / Safer). Privately it also knows today's price and the current supplier, which it must never write in the RFQ.
It writes:
- a summary;
- scope bullets;
- quality, delivery, commercial and ESG requirements;
- the scoring weights;
- a reply deadline;
- 1–3 questions for the suppliers.

**Supplier bot (writes the quote).** It sees the RFQ plus its own private facts: list price, real plant capacity, lead time, ESG, risk and relationship. A new supplier is told a first contract matters to it and that it can offer a trial batch or warranty.
It replies with:
- quote or decline;
- price;
- lead time;
- weekly capacity;
- an ESG commitment;
- how long the quote is valid;
- conditions;
- an answer to each question;
- a cover note.

| Rule (checked in code) | If broken |
|---|---|
| RFQ: every section filled, 1–3 questions, no euro amounts, no current-supplier name | Sent back once, then a fallback |
| RFQ: weights add to 100 and the goal weighs most; reply due in 7–21 days | Corrected in code (counted) |
| Quote: price within 97–112% of the supplier's own list price (exact € range given) | Sent back once, then a fallback |
| Quote: price in the message = price field; capacity ≤ real plant capacity; lead time within −5/+10 days | Sent back once, then a fallback |
| Quote: decline only if the plant covers less than half of the need | Sent back |

**RFQ scorecard (6 checks):**
1. The RFQ is clean.
2. The weights fit the goal.
3. Every supplier replied.
4. At most 1 retry.
5. All questions answered.
6. Declines are justified, and there are no fallbacks.

**First live results (3 parts, 7 suppliers):**
- Before giving the bots exact € ranges: grade 86, 2 fallbacks (they couldn't work out "97–112% of list").
- After: grade 100, 0 fallbacks, 1 retry, about ₹0.1 per round.

The negotiation step then uses the RFQ quotes: only suppliers who quoted take part, and their quote is their opening price.

---

# Evaluator bot and calendar (step 4)
Code: `tools/negotiator/evaluator.py`. API: `POST /api/evaluations`, `GET /api/evaluations/<id>/events`.

**Code does the maths (in the browser), before the bot sees anything:**
- **Score per quote** (0–100, weighted by the RFQ weights):
  - price = cheapest / this price;
  - ESG = the supplier's ESG score;
  - lead = shortest / this lead time;
  - risk = base by risk level, minus missing capacity, −5 for a new supplier, +5 for a strong relationship.
- **Impact per quote over the next 30 days:**
  - first delivery day = lead × 1.2 + 10 days (new supplier) or + 4 days (existing);
  - days with no supply after the current contract ends;
  - missing capacity;
  - cars at risk, and revenue and margin at risk.

  It's counted day by day, skipping Sundays, exactly like the calendar.

**The bot judges and explains.** It writes:
- a summary;
- a flag (strong / ok / watch / risk) and a comment per supplier;
- a shortlist of 1–3 suppliers, plus a first pick;
- a mitigation (for example, extend the current contract);
- a note on the next 30 days.

The shortlist decides who goes to the negotiation. You can tick or untick suppliers.

| Rule (checked in code) | If broken |
|---|---|
| Only quoted supplier names (near-matches like "X (current supplier)" are corrected) | Sent back |
| Shortlist of 1–3, first pick = first in the shortlist, every supplier commented | Sent back |
| No invented numbers: every number ≥ 100 must match a number it was given (±2%); percentages and years are fine | Sent back |
| Word limits per field | Sent back |
| Placeholder reply ("...") | Asked again; counted as a **model glitch**, not a rule break |

**Scorecard:**
1. Shortlist valid.
2. Every supplier commented.
3. No invented numbers.
4. The first pick is in the top 2 by score, or avoids a supply gap.
5. At least 2 suppliers shortlisted, so the buyer keeps leverage.
6. At most 1 retry.

**What we learned testing live:**
- With `reasoning_effort: low`, sarvam-105b often fills this long, number-heavy prompt with "..." placeholders (only 1 clean reply out of 4). With `medium` it writes proper answers, so the evaluator uses medium. The RFQ and negotiation bots stay on low, where it works.
- Results: grade 78 (first version) → 94, with 0 fallbacks, ~₹0.05 per part.

**Calendar** (`buildCal()` in the app, seeded per day so it is stable):
- **Last 30 days (actual):** cars built and sold, revenue, and incidents with cars, € and relationship effect:
  - short deliveries;
  - late deliveries;
  - quality holds.
- **Next 30 days (forecast):** plan, contract endings for the parts in the bid, expected risks (Medium/High-risk suppliers) and RoRo sailings.
- **"Forecast with":** picking a quote paints its impact on the days it hits. "Extend the current contract" removes the supply gap.

## Buyer prompt v3 (after the Bot Lab found case N3)
Added rules:

8. **Keep moving:** raise each counter every round (a suggested range is given per supplier).
9. **Focus:** ignore suppliers that stay far above budget. In the final round, accept the best offer within budget rather than ending with no deal.
10. **Private messages:** each message mentions only that supplier's offer and the buyer's own price.

New engine checks:
- frozen counter → sent back;
- a stray number in a message → sent back;
- "we accept" wording on a counter or drop → replaced;
- final-round safety net → accepts the best offer within budget, counted as a correction.

The auto grade now treats *no deal* as correct when no supplier could fit the budget, and checks that messages match the moves. Details and before/after results are in `how-to-evaluate.md`.

---

# PO → Pay bots (step 7)
Code: `tools/negotiator/p2p.py`. API: `POST /api/p2p`, `GET /api/p2p/<id>/events`.

Pressing **Confirm selection** on Award creates one purchase order per awarded part (one month of volume at the agreed price) and opens step 7, which runs by itself.

- **Stages:** PO sent (procurement bot) → confirmed (supplier bot) → shipped → goods receipt (warehouse bot) → invoiced (supplier bot) → **3-way match** → paid.
- **Who does what:** code decides the facts and runs the 3-way match (PO vs goods receipt vs invoice: quantity, unit price, amount).
- **Problems:** about 1 order in 4 has one, and a run with 2 or more orders always has at least one, so the demo shows an exception. The five kinds are:
  - short delivery;
  - price mismatch (invoice at the old price);
  - quality rejection;
  - duplicate invoice;
  - invoice before goods.
- **AP bot (Sarvam, medium reasoning):** explains the failed match in plain words and recommends one fix from a fixed list for that problem.
- **Checks:** the recommended fix must be copied exactly from the list; no invented numbers (document numbers like PO/INV are ignored); a placeholder reply is asked again.
- **You decide:** the fix buttons. Code then works out the payment (pay received quantity only, credit note, hold, corrected invoice, reject duplicate…).
- **Totals:** PO value, paid, on hold, decisions needed and overpayment prevented update as you go.

Live test, 6 cases (clean + all 5 problems): every problem was found and explained with an allowed fix, for about ₹0.05 per run.
