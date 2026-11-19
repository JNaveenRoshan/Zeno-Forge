# Build Zeno Forge in one prompt

Paste the prompt below into an AI coding assistant that can create files and run commands (for example Claude Code). It describes the whole product: what to build, how the pieces fit, and the rules that make the agents trustworthy. Then steer it with the follow-ups at the end.

> One prompt gets you a working first version. Expect to spend a few rounds on polish. That's normal, and the follow-up prompts below cover the usual ones.

---

## Short version (copy this prompt)

```text
Build a single-file interactive web app (HTML, CSS and JS) using Three.js from a CDN: a live 3D car-factory
campus called "Zeno Forge" that runs Source-to-Pay with AI agents and feels like a strategy game.

WORLD: a low-poly isometric campus built entirely in code, no external models. Two assembly plants (EV and
petrol) with glass skylights and 10-station lines where cars are visibly built, robot arms, a Supplier Hub with
loading docks, a freight train siding, a car-carrier ship at a port, a test track, an HQ tower, wind turbines,
a solar farm and an AI Data Center. Clean toy-like clay look, dark mode first with one violet accent (#7C5CFF).

LIFE: cars move station to station and roll out to the test track. Trucks deliver parts, the train unloads
crates with forklifts, the ship loads 20 finished cars and sails. People walk, work at consoles, eat at the café.
Headlights and street lights at night; the Data Center glows with neon edges.

AGENTS: Source-to-Pay in 8 steps: Parts, Suppliers, RFQ, Evaluate, Negotiate, Award, PO to Pay, Performance.
A buyer agent writes RFQs; one supplier agent per supplier quotes; an evaluator ranks quotes and shows cars at
risk on a 60-day calendar; buyer and supplier agents negotiate live, each with hidden limits; procurement,
warehouse and AP agents run the PO, goods receipt, invoice and 3-way match, and the AP agent explains failures.
Code does all maths and enforces rules: no deal over budget, no invented numbers. I make every decision.

INTERACTION: drag to pan, right-drag to rotate, scroll or pinch to zoom. Click any plant, car, truck, train,
ship or supplier: the camera glides to it, a ring and label mark it, and a glass panel shows live stats. An
area switcher flies between EV Plant, Petrol Plant, Supplier Hub, Port and Test Track.

DASHBOARD: frosted glass panels: top bar (logo, search, area switcher, weather menu, live clock, alerts,
day/night toggle, user), 4 KPI cards (Cars built today, Material spend, Margin per car, Cars sold), a right
detail panel, a bottom-left Source-to-Pay tracker (Contract, PO, Shipped, Received, Invoice match, Paid) and a
tabbed table (Purchase orders, Invoices, Suppliers, Cost per car). Realistic data: part names, supplier ESG
scores, euros per car, lead times in days.

EXTRAS: weather (Clear, Monsoon with wet roads, Fog with floodlights); animated day/night; works on mobile.
```

For the **real AI agents** (an LLM instead of simulated agents), add one line to the prompt: *"Add a small Python standard-library server that runs the agents with my own LLM API key from a git-ignored .env file, and keep the in-browser simulation as a free fallback."* The long prompt below spells out every rule.

---

## Full version (every rule spelled out)

```text
Build "Zeno Forge": a Source-to-Pay demo for a fictional car manufacturer, Zeno Group, with a live 3D campus
and an AI agent at every step. Work in this folder. Create everything, run it, and test it in a browser before
you tell me it's done.

TECH
- Front end: ONE file, zeno-forge.html. Three.js (r160, ES modules from a CDN, exact version) + OrbitControls.
  Plain JavaScript, no build step. Dark, polished UI with a violet accent (#7C5CFF), Inter font, glass panels.
- Back end: tools/negotiator/server.py using ONLY the Python standard library (http.server, sqlite3, urllib, json,
  threading). It serves the app, a JSON API, and Server-Sent Events for live agent moves. SQLite file zeno.db.
- LLM: Sarvam AI chat completions (POST https://api.sarvam.ai/v1/chat/completions, header api-subscription-key,
  model sarvam-105b, response_format json_schema strict). Key read from tools/negotiator/.env (git-ignored),
  never sent to the browser. SARVAM_MOCK=1 = rule-based stand-ins with the same JSON, so everything runs free.

1. 3D CAMPUS (procedural low-poly first; optionally swap in Kenney CC0 models from ./models later)
- Two assembly plants (EV and petrol). Each has a 10-station line where cars visibly get built: frame, body,
  paint, interior, wheels, robot arms that move. Glass skylight so you can see inside from above.
- Logistics: roads with traffic, trucks delivering to a Supplier Hub, a freight train on a rail siding that
  unloads crates with forklifts, a car-carrier ship that docks, loads finished cars and sails. Test track.
- HQ tower, wind turbines, solar farm, beach, ocean shader. Day/night cycle, rain and fog, street lights,
  headlights at night. Click any building/vehicle -> a side panel with live stats. KPI cards on top.

2. DATA (realistic but fictional)
- 4 car models (2 EV, 2 petrol), each with a bill of materials of ~12 parts (part, current supplier, qty,
  price per car, category). Each category has 3-5 alternative suppliers with ESG (E/S/G), lead time, risk,
  certifications, on-time %, CO2, relationship score (or "new"). Selling price per model, overheads, volume.

3. SOURCE-TO-PAY WINDOW (opened from HQ), an 8-step bar:
  1 Parts: pick a car, click a part, click a supplier tile; show cost, margin, ESG, lead time change for the
    whole car. "Add to bid" and "Find new suppliers" (scouting agent adds new suppliers).
  2 Suppliers: per part choose a goal (Cheaper/Greener/Faster/Safer) and which suppliers to invite; suppliers
    failing my criteria (min ESG, max lead, risk, certification) cannot be invited. Button "Send RFQs".
  3 RFQ: buyer agent writes one RFQ per part (summary, scope, quality, delivery, commercial, ESG, scoring
    weights where the goal weighs most, reply deadline, 1-3 questions). It must NOT contain prices, budgets or
    the current supplier's name. One supplier agent per invited supplier writes a quote (price, lead time,
    weekly capacity, ESG commitment, validity, conditions, answers). Each supplier knows only its own list price
    and real capacity; code checks the price is 97-112% of its list price and capacity <= real capacity.
  4 Evaluate: code scores each quote with the RFQ weights and computes its impact on the next 30 days
    (first delivery day, days without supply after the current contract ends, cars and euros at risk), shown
    on a 60-day sales & supply calendar (30 days actual with incidents, 30 days forecast). An evaluator agent
    recommends a shortlist with reasons and a mitigation. I can tick/untick the shortlist.
  5 Negotiate: per part, a buyer agent negotiates live (up to 5 rounds) with each shortlisted supplier agent.
    The buyer has a hidden budget and target; each supplier a hidden floor and eagerness; new suppliers concede
    faster. Show chat lanes, a price chart and an agent scorecard. Also a free in-browser "Simulation" mode.
  6 Award: results grouped by part; I pick one offer per part; an "If you confirm" panel shows cost, margin,
    ESG, lead time, CO2 and yearly profit versus what we pay today. Confirm -> step 7.
  7 PO -> Pay: per award a PO runs through PO sent -> confirmed -> shipped -> goods receipt -> invoiced ->
    3-way match -> paid. Code decides the facts and does the match; about 1 order in 4 has a problem (short
    delivery, price mismatch, quality rejection, duplicate invoice, invoice before goods). An AP agent explains
    the failure in plain words and recommends a fix from a fixed list; I click the fix; code computes payment.
  8 Performance: per supplier: on-time, quantity, quality, invoice accuracy, relationship before -> after.
    The new scores feed the next round (evaluation risk score, negotiation goodwill).

4. AGENT RULES (apply to every agent)
- The LLM writes and judges; CODE does all maths and gives the agent every number it needs.
- Every reply is JSON matching a schema; use enums for choices.
- Code checks every reply: price in message = price field, no deal over budget, no offer below the floor,
  counters always move, no invented numbers (every number >= 100 must exist in the data), no other party's
  secret numbers in a message (redact and count). Broken rule -> send back once with the reason -> then a safe
  code fallback (counted). Each agent run produces a scorecard (checks passed, retries, fallbacks, cost).
- Log every agent run and action to SQLite and show it in an "AI Data Center" building on the campus.

5. BOT LAB: a separate page (tools/negotiator/lab_server.py, own port) to test the negotiation, RFQ and
   evaluator agents on golden cases I write (inputs incl. hidden limits + expected outcomes), with live
   transcripts, pass/fail per expectation, my 1-5 star rating, and a scoreboard comparing runs by label.

6. QUALITY BAR
- No console errors. 60 fps on a laptop. Everything readable at 1440x900. Nothing overlaps.
- README with run instructions. .gitignore for .env and *.db.
- Test: run every step in mock mode, then once with the real API, and show me screenshots.
```

---

## Follow-up prompts that help

- **Look and feel:** *"It looks too busy. Remove clutter, keep one accent colour, make panels calmer, nothing should scroll in the Sourcing window."*
- **Realism:** *"Forklifts and conveyors move for no reason. Make every vehicle and person do a real job: unload, carry, work at a station, eat at the café."*
- **Agents that don't negotiate:** *"The agents agree in round 1. Make the target lower than the budget, never accept an opening offer, force counters to move, and show me the scorecard."*
- **Evaluation:** *"Add golden test cases including an impossible budget (correct answer: no deal) and a case where only one supplier fits. Run each 3 times and show the pass rate."*
- **Hallucinations:** *"Every number in an agent message must come from the data. Check it in code and send the reply back if not."*
- **Public website:** *"Build a static version for Vercel: the 3D campus and simulations work; AI features show how to run locally with your own key. No key, server code or database in the site."*

## Tips

- Ask for **one change at a time** after the first build, and ask it to verify in a browser each time.
- Keep the API key out of every prompt and file except `.env`.
- When an agent misbehaves, don't just reword the prompt: add a **code check** for that behaviour and a **golden case** that catches it (see [custom-agents.md](custom-agents.md)).
