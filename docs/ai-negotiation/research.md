# AI negotiation — Research

Goal: AI **buyer** agents negotiate with AI **supplier** agents, live, inside the Sourcing → Negotiate tab. We give each part a budget and limits, press **Start bidding**, and watch every offer and counter-offer as it happens.

## 1. What already exists in `zeno-forge.html`

| Piece | Where | What it gives us |
|---|---|---|
| Suppliers in contract | `SUPP` (line ~2514) | name, what they make, E/S/G scores, on-time %, lead days, risk, CO₂, certifications |
| Prospect suppliers | `PROS` (~2528) | same fields + qualification stage (Approved / Qualifying / RFQ only) |
| Bill of materials | `BOM` (~2551) | per car model: part, current supplier, qty, unit € price, category; list price; yearly volume |
| Quotes per category | `BIDS` (~2558) | each supplier's price multiplier vs today (e.g. NorthVolta 0.95 on batteries) |
| Sourcing scenario | `SCN` (~2600) | which supplier is picked per part |
| Bid basket | `BIDQ`, `addBid()` (~2739, ~2809) | parts sent from the Sourcing board: model, part, today's price, **target price**, invited suppliers, preferred supplier, status |
| Bidding tab | `biddingHTML()` (~2899) | table of bid lines, editable target, invite chips, "Send RFQ" (only flips a status today) |
| Negotiate tab | `negotiateHTML()` (~2916) | placeholder text and a 3-step diagram, nothing runs |

So the inputs a negotiation needs are already in the page: the part and its spec, today's price, the target, the invited suppliers and each supplier's profile.

**Missing:**
- a budget ceiling per line
- negotiation rules (rounds, what can be traded: price, lead time, ESG commitments, payment terms)
- something that actually generates offers
- a live view of them
- a way to award the winner back into the scenario

## 2. Constraints

- **Single static HTML page, no backend today.** It's served by `tools/serve_nocache.py`, a plain Python static file server.
- **API key safety.** Calling Claude straight from the browser would put an API key in the page. The key has to stay on a small server process, and the browser talks to that process.
- **Must still work without a key.** Demos and offline use need a no-API "simulation" mode that produces the same live stream.
- **Budgets must be hard limits.** A language model can't be trusted to never cross a number. The engine (plain code) must check every offer and counter and reject or clamp anything out of bounds.
- **Information separation.** Each supplier agent may only see its own conversation. Only the buyer sees the budget and the competing offers, the way a real RFQ works.

## 3. Options considered

| Option | How it works | Verdict |
|---|---|---|
| A. Rule-based bots in the browser | Each side follows a concession curve (start high/low, move toward a private limit each round) | Instant and free, but the messages are canned. **Keep as "Simulation" mode.** |
| B. Claude agents via a small local server (Messages API + structured outputs) | The server runs the rounds. Each agent turn is one Claude call that returns a structured offer (price, lead time, terms, short message). The server streams events to the browser. | **Recommended for "AI agents (live)".** Real reasoning and real language; our code owns the rules and limits. |
| C. Managed Agents (Anthropic-hosted agent loop) | Each agent is a hosted session | Overkill here: the negotiation is a turn-by-turn workflow our code drives, not an open-ended agent choosing its own tools. More moving parts and session-runtime cost. |

**Why Option B fits:** the structure (rounds, who speaks, limits) is fixed by us, and only the *content* of each move needs intelligence. That's a code-driven workflow where each step is one model call with a JSON answer. It's the simplest setup that gives genuinely negotiated outcomes.

## 4. Model and cost (Option B)

- **Model:** Claude Opus 5.5 (`claude-opus-5-5`) for both buyers and suppliers by default. If cost matters later, supplier agents can drop to Claude Haiku 5.5.
- **Each turn:**
  - one request with structured output, so the reply is guaranteed to be valid JSON
  - effort `low` for suppliers and `medium` for the buyer
  - refusal fallback enabled
- **Rough cost per negotiated part:** 4 suppliers × 5 rounds ≈ 25 calls of ~2k input / ~300 output tokens. That's about **$0.35** at Opus 5.5 prices ($4 / $20 per million tokens). A 7-part bid is about $2.50.
- **Speed:** all suppliers answer in parallel each round, so one round takes about as long as one call (a few seconds).

## 5. Risks

| Risk | Mitigation |
|---|---|
| Agent proposes a price above budget or below a supplier's floor | Engine validates every move; out-of-range moves are rejected and the agent is told why; repeated violations end that thread |
| Agents leak private numbers (buyer's budget, supplier's floor) | Private numbers live only in that agent's system prompt; the engine strips them from anything forwarded to the other side |
| Runaway cost | Max rounds per part, max parts per run, and a $ cap per run checked against usage after every call |
| No API key / offline | Simulation mode produces the same event stream |
| Slow or failed call | Per-call timeout and retry; a supplier that fails twice drops out with a visible note |
