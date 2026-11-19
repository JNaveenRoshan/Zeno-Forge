# AI negotiation — Plan (v2)

## 1. Budget sliders per part, with live margin impact

In **Sourcing → Bidding**, every part line gets a **budget slider**. It runs from about 20% below today's price to about 20% above, with markers for today's price and your target.

As you drag a slider, the panel updates instantly for that car model:

| Shows | Example |
|---|---|
| Budget for this part vs today | €8,610 → €9,040 (+€430) |
| Worst-case **cost per car** if every part closes at its budget | €20,650 → €21,080 |
| **Margin** against the price we sell the car for today (Volt €27,900) | €7,250 (26.0%) → €6,820 (24.4%) |
| **Yearly impact** at planned volume | −€22.4M margin / yr |

A colour bar turns amber when the margin falls below a threshold you set (default 20%), and red below 15%.

## 2. Which suppliers meet our criteria, per part

**Criteria you set once (with a per-part override):**
- minimum ESG score
- maximum lead time
- allowed risk levels
- required certifications (e.g. IATF 16949)
- qualification stage (Approved / Qualifying / RFQ only)

**Each part shows:**
- the suppliers that **pass**, with a green tick
- the suppliers that **fail**, greyed out with the reason (e.g. "Lead 35 d > 30 d", "Not IATF certified")

Only passing suppliers can be invited to the bid.

## 3. KPI impact of each supplier on the car

Each eligible supplier card shows what choosing it would do to **the whole car**:
- **Cost per car:** e.g. −€430
- **Margin:** e.g. +1.5 pts
- **Car ESG score:** e.g. 77.3 → 79.8
- **Supply lead time:** the slowest part, e.g. 21 d → 28 d
- **CO₂e of parts:** e.g. −950 kg
- **Risk:** e.g. 1 more medium-risk supplier

These same numbers appear on every negotiated offer, so you compare deals on their effect on the car, not just on price.

## 4. AI agents (Sarvam AI)

- **The agents run on Sarvam AI,** using your API key and credits.
- **The key stays on a small local server** (`tools/negotiator/`) and is never put in the web page.
- **The exact Sarvam endpoint and model name will be confirmed** against Sarvam's current documentation at build time.

**Who knows what:**

| Agent | Knows | Never sees |
|---|---|---|
| Buyer agent (one per part) | Its budget, target, our criteria, offers it has received | Any supplier's floor price or costs |
| Supplier agent (one per supplier) | Its own floor price, capacity, how much it wants the deal (from its profile) | The buyer's budget, the target, other suppliers' offers |

So neither side knows the other's limits. The engine passes on only the messages and offers each side is allowed to see.

**Hard rules, checked by the program rather than trusted to the AI:**
- the buyer can't offer above budget
- a supplier can't offer below its floor
- a maximum number of rounds
- a credit/cost cap per run

A move that breaks a rule is rejected and retried once.

**How a part is negotiated:**
1. The buyer sends a request for quotes. Each supplier makes an opening offer: price, lead days, ESG commitment, payment terms and a short message.
2. The buyer counters each one, accepts, or drops it.
3. This repeats for up to N rounds.
4. The engine records every move live.

**Watch it live** in the **Negotiate** tab:
- one chat lane per supplier
- a price-per-round chart with your budget line
- a running "best offer" for each part

## 5. You pick the winners

**No automatic award.** When the bidding ends, the **Results table** lists every part with each supplier's **final offer**:
- price
- difference vs today's price and vs your budget
- the car-level KPI impact from section 3 (cost, margin, ESG, lead time, CO₂, risk)
- whether it is within budget

Each row is tagged **Best price**, **Best ESG** or **Best overall** to guide you, but **you choose**: tick one offer per part and press **Confirm selection**. That:
- updates the sourcing scenario, cost per car and margin
- updates the car's ESG score and the Sourcing board
- saves the transcript

## 6. New suppliers appear in the Supplier Hub

- **New suppliers enter the market every few minutes** (in simulation time), for example a new battery maker or a cheaper tyre supplier. Each comes with its own ESG scores, lead time, certifications and an opening price.
- **They show at the top** of their part's block in the Supplier Hub with a **NEW** badge, plus a toast message ("New supplier: Velta Cells — batteries, ESG 86").
- **They're checked against your criteria automatically.** The ones that pass can be invited to the next bid.

## 6b. How we evaluate the agents

The engine knows both hidden limits (the buyer's budget and each supplier's floor), so every negotiation can be scored objectively after it ends. Each run produces an **Agent scorecard**:

| Metric | What it tells you | Good |
|---|---|---|
| **Deal rate** | % of parts that closed inside budget | high |
| **Saving vs today** | € and % below today's price | high |
| **Buyer capture** | How much of the bargaining room the buyer won: (budget − deal) ÷ (budget − supplier floor) | high for the buyer agent |
| **Supplier capture** | (deal − floor) ÷ (budget − floor) | high for the supplier agents (shows they aren't pushovers) |
| **Rounds to close** | Speed of agreement | low |
| **Rule breaks** | Moves rejected by the engine (over budget, under floor, bad format) | 0 |
| **Leaks** | Did an agent reveal its own limit or another supplier's price? Checked automatically on every message | 0 |
| **Car impact of the winner** | Margin, ESG, lead time vs today | better than today |
| **Your pick vs agents' best** | How often your final choice matched the agents' "Best overall" | trend over time |
| **Cost** | Sarvam credits used per part | low |

**Test bench:** a fixed set of 20 negotiation scenarios (same parts, budgets, floors and random seed) that can be replayed with one click:
- **Simulation bots** set the baseline.
- **Sarvam agents** are scored against that baseline.
- **Every change** to an agent prompt, model or setting is re-run on the same 20 scenarios, so you can see whether it actually got better.
- **Results are saved** to `runs/` and shown side by side.

## 6c. Data storage (SQLite)

**One file, `tools/negotiator/zeno.db`,** managed by the same small Python server that runs the Sarvam agents. Nothing to install: SQLite is built into Python.

| Table | Holds |
|---|---|
| `budgets` | model, part, budget € per car, updated at |
| `criteria` | min ESG, max lead, allowed risks, required cert, allow unqualified, margin thresholds |
| `suppliers` | every supplier that joined (scouted or arrived on its own): name, part category, ESG, lead, risk, certs, stage, price factor, joined at |
| `bid_lines` | parts sent to bidding: model, part, today's price, budget, target, invited suppliers, status |
| `negotiations` | one row per run: mode (simulation / Sarvam), started, ended, credits used |
| `moves` | full transcript: negotiation, part, round, from, to, type (offer / counter / accept / drop / rejected), price, lead, ESG, terms, message, time |
| `final_offers` | each supplier's last offer per part, with its car-level impact (cost, margin, ESG, lead, CO₂, risk) |
| `selections` | the winner you picked per part and when |
| `eval_runs`, `eval_scores` | test-bench runs and their scorecard metrics, so runs can be compared over time |

**How the page uses it:**
- **On load,** the page reads budgets, criteria, suppliers and bid lines from the server, so nothing is lost on reload.
- **Every change is saved straight away:** slider, criteria, new supplier, bid line or selection.
- **Negotiation moves** are written as they stream, so a run can be replayed later.
- **Without the server** (opening the HTML file on its own), the page falls back to saving in the browser so simulation still works.

## 6d. Sales & supply calendar (after the database)

A month-view calendar in the Supplier Hub (or its own tab), covering **the past 30 days (actuals) and the next 30 days (forecast)**:

- **Each day** shows cars built, cars sold (shipped on the RoRo) and revenue. Past days are solid; forecast days are lighter, with a range.
- **Problem days are flagged on the calendar and quantified:**

| Event | Example | Quantified as |
|---|---|---|
| **Short delivery** | Voltacell sent 320 of 400 battery packs | −80 packs → −80 Volts built → −€2.23M revenue, −€0.58M margin; days of buffer left |
| **Late delivery** | Rimco tyres 2 days late (customs) | Line slowed 2 shifts → −46 cars, catch-up overtime cost |
| **Contract ending** | FuelTech contract ends 30 Nov | Countdown; volume not yet covered after that date; "Start bid" shortcut |
| **Quality hold** | ChromaCoat colour-match rework | Cars held for rework, days of delay |
| **Ship sailing** | Zeno Horizon departs with 20 cars | Sales booked that day |

- **Forecast** = planned production × the share of parts actually covered by contracts and stock. Days where a known risk (contract end, a supplier with repeated shortfalls) cuts coverage show the expected loss.
- **Click a day** to open its events, the suppliers involved and the € impact.
- **Month totals** at the top: cars built and sold, revenue, revenue lost to supply problems, and the top 3 suppliers that caused it.

**Supplier relationship scores feed in.** Every incident above lowers that supplier's score, and on-time, in-full months raise it. The score is stored in the database and shown everywhere a supplier appears (Hub, Sourcing board, Results). It also makes the simulation agents realistic: strong partners are more flexible in negotiations.

## 7. Build phases

| Phase | What gets built | You can test |
|---|---|---|
| 1 | Budget sliders with margin impact; criteria and eligibility per part; supplier KPI impact; new suppliers popping up in the hub | All the analysis, no AI needed |
| 2 | Negotiate tab live view + Results table with your selection + **simulation agents** (rule-based, no credits used) | The full bidding flow end-to-end, free |
| 3 | Local server + **SQLite database** (budgets, criteria, suppliers, bids, transcripts, selections persist) + **Sarvam AI** buyer and supplier agents; switch between Simulation and Sarvam AI | Real AI negotiation, live; nothing lost on reload |
| 3b | **Sales & supply calendar** (past 30 days + next 30 days) with short deliveries, late deliveries, contract endings, quality holds and sailings, all quantified in cars and €; relationship scores updated by incidents | Where sales are lost and why |
| 4 | Agent scorecard + 20-scenario test bench (replay, compare Simulation vs Sarvam, before/after prompt changes); rule tests, credit cap, transcripts | How good the agents are |

## 8. Decisions needed from you

1. **Local server language:** Python (matches your current dev server), or Node?
2. **Margin warning thresholds:** amber below 20%, red below 15% — OK?
3. **How often new suppliers appear:** every ~3 minutes of simulation time — OK?
4. **Start with Phase 1?**
