# Create, build and evaluate your own agents

Every agent in Zeno Forge (RFQ writer, supplier quoting, evaluator, negotiators, AP agent) follows the same 6-part recipe. This guide walks through it with a runnable example, then shows how to evaluate an agent and how to plug it into the app.

**Start here:** [`tools/negotiator/custom_agent_template.py`](../tools/negotiator/custom_agent_template.py), a complete **should-cost agent** in about 100 lines.

```bash
python tools/negotiator/custom_agent_template.py --mode mock     # free
python tools/negotiator/custom_agent_template.py --mode sarvam   # real AI (key in tools/negotiator/.env)
```

```
C1 Steel wheel rim        fair €  37 quote €  74 (+100.0%) → overpriced  PASS
     “Your quote of EUR 74 per rim is more than double our calculated should-cost of EUR 37 ...”
C2 Seat frame             fair €  92 quote €  95 (+3.3%)   → fair        PASS
C3 Aluminium door panel   fair €  55 quote €  50 (-9.1%)   → underpriced ...
3/3 cases passed · 2s · ₹0.02
```

---

## The 6-part recipe

| # | Part | Rule of thumb |
|---|---|---|
| 1 | **Facts (code)** | Code calculates every number. The LLM never does maths. |
| 2 | **Schema** | The exact JSON the agent returns. Use `enum` for any choice (verdict, action, flag). |
| 3 | **Prompt** | Role → the facts → numbered rules → output format. Short, specific, word limits. |
| 4 | **Rule checks (code)** | Check every reply. Broken rule → send it back once with the reason → safe fallback. |
| 5 | **Golden cases** | Inputs + what a *good* agent must do. Write the expectation **before** you run. |
| 6 | **Evaluate** | Your checks + automatic checks + your own rating + cost, over several runs. |

### 1 · Facts in code
```python
def should_cost(case):
    material = sum(kg * price for kg, price in case['materials'].values())
    ...
    return {'fair_price': fair, 'quote': case['quote'], 'gap_pct': ...}
```
In Zeno Forge, examples are cars at risk (`evalRows` in the app), weighted quote scores, and 3-way match results. The agent only ever *receives* these numbers.

### 2 · Schema
```python
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'biggest_driver', 'argument'],
          'properties': {'verdict': {'type': 'string', 'enum': ['fair', 'overpriced', 'underpriced']}, ...}}
```
Call it with `agents.chat_json(system, user, name, SCHEMA, usage, effort='medium')`. It handles retries, strict JSON and cost tracking.

### 3 · Prompt
```text
You are Zeno Group's cost analyst. A supplier quoted EUR 74 per rim ... fair price EUR 37 ...
RULES:
1. verdict: 'overpriced' if the gap is above +5%, ...
2. ...
3. argument: 2 sentences (max 45 words). Use only the numbers above; do not calculate new ones.
OUTPUT: only the JSON object.
```
Learned the hard way:
- With `reasoning_effort: low`, sarvam-105b sometimes returns `"..."` placeholders on long, number-heavy prompts. Use `medium` for judgement agents.
- Give exact ranges ("between EUR 8,100 and EUR 9,354"), not percentages. Models are bad at working out "97–112% of X".
- Never tell it what *not* to write with the literal text (for example `never write '...'`). It can make it more likely.

### 4 · Rule checks
```python
def check(case, f, d):
    errs = []
    if d['verdict'] != expected_from_numbers: errs.append("verdict must be 'overpriced' for a gap of 100%")
    if invented(d['argument'], known_numbers): errs.append('numbers not in the data: ...')
    return errs
```
Then: retry once with `YOUR LAST ANSWER WAS REJECTED: <reasons>`; if it fails again, use a code-built safe answer and **count it as a fallback**. Shared helpers:
- `evaluator.invented(text, known)`: numbers ≥ 100 that aren't in the data (years and percentages allowed).
- `engine.redact(text, secrets)`: blank out another party's private numbers.
- `engine.bare_numbers(text)`: every number in a message.

### 5 · Golden cases
```python
{'key': 'C2', 'part': 'Seat frame', 'quote': 95, ... 'expect': {'verdict': 'fair', 'driver_in': ['labour', 'material']}}
```
Cover four kinds:
- **normal**;
- **impossible** (the only right answer is "no deal" or "decline");
- **traps** (cheapest but risky, only one option fits);
- **edges**.

### 6 · Evaluate
- **Run mock first.** If the free mock fails a case, the *case* is wrong, not the agent. (This happened while writing the template: two quotes didn't match the expected verdicts.)
- **Run the real model 3 times.** LLMs vary, so judge the average.
- **Three scores:** your golden checks (business correctness), automatic checks (retries, fallbacks, invented numbers), and **your own 1–5 rating** of the text. The rating catches what rules can't: in a live run, the agent called a +3% gap "significant".
- **Change one thing at a time** (a prompt line, the model, the reasoning effort) and keep it only if the scores go up.
- **Targets:** impossible cases 100%; everything else ≥ 80% over 3 runs; average rating ≥ 4; cost noted.

**For the built-in agents, use the Bot Lab:**
```bash
python tools/negotiator/lab_server.py 8780     # http://localhost:8780
```
It has golden sets for the negotiation, RFQ and evaluator agents, editable hidden limits, live transcripts, star ratings and a scoreboard that compares runs by label. Details: [how-to-evaluate.md](ai-negotiation/how-to-evaluate.md).

---

## Plug a new agent into the app

1. **Server:** copy the run pattern from `rfq.py` or `p2p.py`. A `Run` class with `emit()` streams events, `start()` runs in a thread, and the run is saved to SQLite. Add a route in `server.py` (`POST /api/<your-agent>`) and add your runs dict to the SSE lookup in `_stream`.
2. **App:** in `zeno-forge.html`, POST the facts, open an `EventSource` on `/api/<your-agent>/<id>/events`, and render the events. Use `needAI()` when the server isn't available, so the public website explains how to run it.
3. **Log it:** call `aiLog(kind, title, data)` so it appears in the AI Data Center.
4. **Test it:** add a golden set for it in the Bot Lab, or keep a golden list in its own file as the template does.

## Agent ideas that fit Source-to-Pay

| Agent | Facts from code | The agent writes |
|---|---|---|
| **Should-cost** (the template) | Material, labour and energy breakdown | Verdict and the argument to use with the supplier |
| **Overpayment finder** | Every invoice vs the negotiated price | Which invoices to dispute and why |
| **Contract terms** | Clause list extracted from text | Risky clauses and a suggested redline |
| **Tier-2 risk** | Supplier → sub-supplier map, news flags | Early warning and an alternative plan |
| **Engineering change** | Changed BOM lines | Which suppliers, contracts and costs are affected |
| **Spend sorter** | Raw spend lines | Category, with a confidence score |

Keep the same rule for every one: **the AI writes, code supplies the facts and checks every answer, a human decides.**
