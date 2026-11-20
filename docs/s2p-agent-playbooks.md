# Five S2P agents that work alongside SAP Ariba + Joule

Big manufacturers run Source-to-Pay in **SAP Ariba**, with **Joule** as the built-in GenAI assistant. Joule works mainly inside SAP's own data and screens. A lot of procurement work happens *outside* those screens: in emails, PDFs, Word contracts, engineering systems, chat and the news.

This page shows how to build five agents for those gaps, using the same recipe as every agent in Zeno Forge. Treat Ariba as the **system of record**. These agents read from it, work outside it, and only write back drafts that a human approves.

> Read [custom-agents.md](custom-agents.md) first. It explains the 6-part recipe (facts → schema → prompt → rule checks → golden cases → evaluate) and has a runnable template, [`custom_agent_template.py`](../tools/negotiator/custom_agent_template.py). Each playbook below fills in those six parts for one agent.

| # | Agent | The gap it fills | Human gate |
|---|---|---|---|
| 1 | [Tail-spend negotiator](#1-tail-spend-negotiator) | Small one-off buys nobody has time to negotiate; suppliers not on Ariba | Buyer approves the draft PO |
| 2 | [Engineering-to-procurement (BOM / spec)](#2-engineering-to-procurement-bom--spec-agent) | Procurement starts in engineering (BOMs, change orders, drawings) | Engineer confirms any spec deviation |
| 3 | [Proactive supply chain risk](#3-proactive-supply-chain-risk-agent) | Disruptions (strikes, floods, shortages) seen *before* a PO is late | Category manager picks the mitigation |
| 4 | [Contract redlining and playbook compliance](#4-contract-redlining-and-playbook-compliance-agent) | Contract negotiation happens in Word and email | Legal approves high-risk clauses |
| 5 | [Intake "front door"](#5-intake-front-door-agent) | Employees avoid procurement tools, which creates off-contract (maverick) spend | Budget owner approves the request |

---

## 1. Tail-spend negotiator

**Job:** take a small purchase request, get quotes from 3–5 suppliers by email, negotiate a better price, and hand back a draft PO.

| Part | What to build |
|---|---|
| **Facts (code)** | The request (item, qty, needed-by date); price benchmarks (last 3 prices paid, catalogue price); a **walk-away price** (for example the benchmark median); the supplier shortlist. Parse PDF quotes into `{supplier, unit_price, qty, lead_days, valid_until}` with a separate extraction step, and check its totals in code. |
| **Schema** | `{action: enum[accept, counter, decline, ask_clarification], supplier, counter_price, message}` |
| **Prompt rules** | Never offer above the walk-away price. Never reveal the benchmark or other suppliers' prices. Counter-offers must move toward the supplier's price each round. Maximum 3 rounds. Message under 80 words, polite, one clear ask. |
| **Rule checks** | `counter_price ≤ walk_away`; the price in the message equals `counter_price`; no number from another supplier's quote appears in the message (`engine.redact`); counters always move. |
| **Golden cases** | Normal (one supplier comes down 8%); **impossible** (every quote is above walk-away, so the right answer is decline and escalate); trap (cheapest quote, but its lead time misses the needed-by date); a supplier asks a question instead of quoting. |
| **Human gate** | The buyer sees the thread and the final quote, then clicks to create the **draft** PO. |

**Zeno Forge reuse:** the negotiation engine (`engine.py`) already does hidden limits, moving counters and redaction. Replace the live chat lane with an email adapter.

---

## 2. Engineering-to-procurement (BOM / spec) agent

**Job:** read an engineering change or BOM, write a technical RFQ, and flag quotes that quietly deviate from the spec.

| Part | What to build |
|---|---|
| **Facts (code)** | A diff of the BOM before and after the change: which lines changed (material grade, tolerance, quantity). The spec table per part `{material, grade, tolerance_mm, finish, certification}`. For each quote, a code comparison of quoted values against the spec. |
| **Schema** | RFQ: `{part, spec_lines[], questions[]}`. Quote check: `{part, deviations: [{field, spec, quoted, severity: enum[blocker, review, ok]}], summary}` |
| **Prompt rules** | Copy spec values exactly as given; never round or convert units. Every deviation must name a field from the spec table. Severity follows the rules in the prompt (for example, wrong alloy grade or a missing certification is a blocker). |
| **Rule checks** | Every spec value in the RFQ matches the table character for character. Every deviation the agent reports exists in the code diff, and every deviation in the diff is reported (no misses, no inventions). |
| **Golden cases** | Clean quote (no deviations); a wrong alloy grade (blocker); a tolerance that is looser than the spec (review); a quote in different units (inches vs mm), which the code converts and the agent must not double-flag. |
| **Human gate** | An engineer confirms or waives each blocker before evaluation continues. |

**Zeno Forge reuse:** the RFQ agent (`rfq.py`) already writes RFQs from part data. Feed it spec lines instead of goals.

---

## 3. Proactive supply chain risk agent

**Job:** watch outside signals (news, weather, port status, commodity prices) against open POs, and warn early with a plan.

| Part | What to build |
|---|---|
| **Facts (code)** | Open POs with supplier location, ship route and due date. Signals as structured events `{type, region, start, severity, source_url}`. Code matches events to POs by region and route, then calculates **days of stock left**, **cars at risk** and **revenue at risk**. A list of pre-approved backup suppliers per part. |
| **Schema** | `{alert_level: enum[watch, act_now], affected_pos[], reason, options: [{action: enum[expedite, split_order, switch_backup, wait], supplier?, cost_impact}], recommendation}` |
| **Prompt rules** | Use only the matched events and the computed impact numbers. Options can only use suppliers from the backup list. Always cite the source of each event. No alert if nothing matches. |
| **Rule checks** | Every PO named is in the matched list; every number exists in the facts (`evaluator.invented`); recommended suppliers are on the backup list; every event cited has a `source_url`. |
| **Golden cases** | A port strike on a key route (act now); a storm in a region with no open POs (no alert, so silence is correct); a commodity price spike under the threshold (watch only); a backup supplier who also sits in the affected region (must not be recommended). |
| **Human gate** | The category manager chooses the option. The agent only drafts the messages. |

**Zeno Forge reuse:** the evaluator already computes cars and euros at risk on the 60-day calendar (`evalRows`). Point it at live signals instead of quotes.

---

## 4. Contract redlining and playbook compliance agent

**Job:** compare a supplier's redlined contract with your legal playbook, propose counter-redlines, and escalate only the risky clauses.

| Part | What to build |
|---|---|
| **Facts (code)** | Extract clauses from the Word file (python-docx can read the text and tracked changes). The playbook as data: `{clause_type, our_position, acceptable_range, fallback, never_accept}` for liability caps, IP ownership, payment terms, termination, ESG and regulatory clauses. Code tags each clause with its type and extracts any numbers (caps, days, percentages). |
| **Schema** | `[{clause_id, clause_type, status: enum[ok, counter, escalate], issue, counter_text, playbook_ref}]` |
| **Prompt rules** | Judge each clause only against its playbook entry. Counter-text must use the playbook's position or fallback wording. Anything in `never_accept` is always `escalate`. Explain each issue in one sentence. |
| **Rule checks** | Numbers in the counter-text are inside the acceptable range; every clause has a `playbook_ref` that exists; `never_accept` items are never marked `ok` or `counter`; no clause is skipped. |
| **Golden cases** | A standard contract (all ok); payment terms changed from 60 to 30 days (counter with the fallback); unlimited liability (escalate); a clause type missing from the playbook (escalate, never guess). |
| **Human gate** | Legal reviews every `escalate`. A buyer approves the counters before anything is sent. |

**Note:** the agent prepares the redline; it is not legal advice. Keep a human lawyer on anything escalated.

---

## 5. Intake "front door" agent

**Job:** let employees ask for things in Slack, Teams or email ("I need 15 Figma licences for design"), then do the checks and create a compliant request.

| Part | What to build |
|---|---|
| **Facts (code)** | Parse the request into `{item, qty, team, needed_by}`. Look up existing contracts and catalogue items, the team's remaining budget, and the approval policy (for example: under €5,000 the manager approves, above that procurement approves). Run security or privacy checks (for example a ticket in your InfoSec tool) for software. |
| **Schema** | `{understood: {item, qty, team}, route: enum[use_existing_contract, catalogue, new_sourcing, needs_info], missing_info[], approver, reply_to_employee}` |
| **Prompt rules** | Ask for missing details instead of guessing them. Always prefer an existing contract. The reply is short and plain, with no procurement jargon. Never say a request is approved; only the approver can do that. |
| **Rule checks** | The approver matches the policy for the amount (code calculates the amount); the route is `use_existing_contract` whenever a matching contract exists; no budget figure for another team appears in the reply. |
| **Golden cases** | Item on an existing contract (route to it); over budget (needs approval, explain why); vague request ("some laptops"), so the agent asks for the quantity; a software tool with no security review (start the review first). |
| **Human gate** | The approver clicks approve. Only then does code create the requisition in Ariba through its APIs. |

---

## Connecting to SAP Ariba

- **Read and write through APIs, not screens.** SAP publishes Ariba APIs (for requisitions, POs, invoices and supplier data) on the SAP Business Accelerator Hub. Check which ones your licence includes.
- **Write drafts only.** Agents create draft requisitions, draft POs and suggested redlines. A person submits them in Ariba.
- **Long-running work:** negotiations by email and risk monitoring run for days. Store each case's state (SQLite here, or a queue or workflow engine in production) so an agent can pause and resume.
- **Log everything.** Every agent step, message and document goes into an audit log (the AI Data Center in Zeno Forge does this with `aiLog`).
- **Try it first:** SAP offers trial access to some of its products. Test the API calls there before you touch production data.

## Evaluate every agent the same way

- Write the golden cases **before** you run the agent, including at least one impossible case.
- Run mock mode first. If the mock fails, fix the case, not the agent.
- Run the real model 3 times. Targets: impossible cases 100%, everything else ≥ 80%, your own rating ≥ 4/5, cost noted.
- When the agent misbehaves, add a **code check** and a **golden case** for it, rather than only rewording the prompt.

Same rule for all five: **the AI writes, code supplies the facts and checks every answer, a human decides.**
