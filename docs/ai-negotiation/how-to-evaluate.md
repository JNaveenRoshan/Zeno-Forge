# Zeno Bot Lab: test the bots yourself

A separate test bench, not part of the Forge app. It runs on its own port and uses the same bots.

## Start it
```
python tools/negotiator/lab_server.py 8780
```
Open http://localhost:8780. (In Claude Code it's also in `.claude/launch.json` as **bot-lab**.)

Results are stored in `tools/negotiator/zeno.db` (tables `lab_sets`, `lab_results`).

## What you can do
| Tab | What it's for |
|---|---|
| **Negotiation** | Buyer bot vs supplier bots. You set today's price, the buyer's hidden **budget** and **target**, and each supplier's list quote, hidden **floor**, **eagerness** (0–1), ESG, lead time, risk and relationship (blank = new supplier). |
| **RFQ** | The buyer bot writes the RFQ; supplier bots quote. You set the goal and each supplier's hidden **real weekly capacity**. |
| **Evaluator** | You type the quotes. The lab shows the scores and supply-gap maths the code calculates, then the evaluator bot recommends a shortlist. |
| **Scoreboard** | Every run with your checks %, the auto grade, your stars and the cost. **Compare runs** shows a case × run grid per golden set. |
| **How to evaluate** | The method below. |

Each tab has a **golden dataset**, a list of cases. For each case you can:
- edit any number;
- write **what a good bot must do** (expected deal or no deal, max price, min saving, who must win, who must quote, who must be first pick or on the shortlist);
- save it, copy it as a new set, or export / import it as JSON.

**Mock (free)** runs rule-based bots. **Sarvam AI** runs the real bots. Set a **Label** (for example `v2`, `v3-new-prompt`) before running so you can compare.

## The three scores
1. **Your checks (golden expectations):** your definition of a correct business result. This is the main score.
2. **Auto grade:** the rules the code checks every time: no leaks, no stalling, concessions, retries, fallbacks. It covers behaviour, not business correctness.
3. **Your stars (1–5) and notes:** rate each conversation like a junior buyer's work. This catches tone and reasoning that rules can't.

## Method
1. **Write the expectation before you run.**
2. **Cover four kinds of case:**
   - normal (room to save);
   - **impossible** (every floor above the budget: the only correct answer is no deal);
   - **traps** (the cheapest supplier has a supply gap, or only one supplier fits);
   - edge cases (a single supplier, a very eager new supplier).
3. **Run Mock first.** If Mock fails a case, the case itself is probably wrong.
4. **Run Sarvam 3 times** on important sets: LLMs vary, so judge the average.
5. **Change one thing at a time,** with a new label, then rerun and compare in Scoreboard. Keep the change only if your checks and stars improve.
6. **Targets:**
   - impossible cases 100%;
   - other cases ≥ 80% over 3 runs;
   - stars ≥ 4 on average;
   - note the cost.

## First results (golden v1, 19 Nov 2026)
| Bot | Mock | Sarvam `v2` | Cost |
|---|---|---|---|
| Negotiation (5 cases) | 5/5 cases, 26/26 checks | **4/5 cases**, 22/25 checks | ₹0.92 |
| RFQ (3 cases) | – | 3/3 cases, 21/21 checks | ₹0.25 |
| Evaluator (3 cases) | – | 3/3 cases, 8/8 checks | ₹0.07 |

**Finding:** Sarvam failed **N3 "Only one supplier can fit"**.
- **What happened:** the buyer kept countering Doorworks, whose hidden floor is above the budget, and didn't close with Pressline, the only supplier that could fit. It ended with no deal.
- **Possible fixes:** a buyer prompt rule like "if a supplier is not moving and another is closer to your budget, focus there", or more rounds.
- **How to check a fix:** run it with a new label and compare N3 in the scoreboard.

## Fix for N3 (buyer prompt v3, 19 Nov 2026)
**Diagnosis**, from replaying N3 move by move:
- The buyer repeated the same counter (€1,646) for 4 rounds instead of moving.
- It kept arguing with Doorworks, whose hidden floor is above the budget.
- One message to Doorworks mentioned Pressline's price.
- Some counters and drops were worded as "we accept…", which would mislead a supplier.

**Changes:**
- **Buyer prompt rules 8–10:**
  - keep raising each counter;
  - focus on suppliers that can fit the budget, and in the final round accept the best offer within budget;
  - only mention that supplier's own numbers.
- **The buyer is given help each round:** a *suggested next counter* range per supplier, and which offers are already within budget.
- **Engine checks:**
  - a frozen counter is sent back;
  - any number in a message that is neither that supplier's offer nor the buyer's price is sent back;
  - "we accept" wording on a counter or drop is replaced with a neutral message;
  - **final-round safety net:** if the buyer would end with no deal while an offer within budget exists, the engine accepts the best one. This is counted as a rule correction.
- **Auto grade:**
  - "Deal within budget" and "Buyer captured ≥ 30%" now count *no deal* as correct when no supplier's floor fits the budget (N2);
  - a new check, "Messages match the moves".

**Results:**

| Run | N1 | N2 | N3 | N4 | N5 | Cases passed |
|---|---|---|---|---|---|---|
| Sarvam `v2` | ✓ | ✓ | ✕ no deal | ✓ | ✓ | 4/5 |
| Sarvam `v3-buyer-fix` run 1 | ✓ | ✓ | ✓ | ✓ | ✓ | 5/5 |
| Sarvam `v3-buyer-fix` run 2 | ✓ | ✓ | ✓ | ✓ | ✓ | 5/5 |

N3 replayed 3 more times on its own: deals with Pressline at €1,694, €1,610 and €1,649, every time.

**Still visible in the auto grade (worth watching):** occasional leak attempts (the supplier floor is blanked out before the buyer sees it) and the odd rule retry.
