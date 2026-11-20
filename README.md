# Zeno Forge · Source-to-Pay with AI agents

**What if every step of Source-to-Pay had an AI agent, and you could watch them work?**

Zeno Forge is a working demo of a car manufacturer, *Zeno Group*, on a live 3D campus: two assembly plants, trucks, a freight train, a car-carrier ship, a Supplier Hub and an AI Data Center. Behind it runs the full **Source-to-Pay** journey, with an AI agent at each step:

```
        SOURCE-TO-CONTRACT (S2C)                                   PROCURE-TO-PAY (P2P)
Parts → Suppliers → RFQ → Evaluate → Negotiate → Award  →  PO → Receive → Invoice → 3-way match → Pay → Performance
           │         │       │           │         │        │       │         │           │                │
        scouting   buyer + evaluator  buyer vs    YOU    procure- warehouse supplier    AP agent        feeds the
          agent   supplier  agent    supplier    decide   ment              agent     explains +       next round
                   agents            agents                agent                      recommends fix
```

- **Source-to-Contract (S2C)**, the strategic half: *who do we buy from, and at what price?*
- **Procure-to-Pay (P2P)**, the operational half: *order it, receive it, pay the right amount.*

**The AI writes, compares and negotiates. Code supplies the facts and enforces the rules. A human makes the decisions.**

---

## What's inside

| | |
|---|---|
| 🏭 **3D campus** | Two plants building cars station by station, logistics, day/night, rain and fog (Three.js, Kenney CC0 models) |
| 📝 **RFQ agents** | A buyer agent writes the RFQ (no prices, goal-based scoring weights); one supplier agent per supplier writes a quote |
| 📊 **Evaluator agent** | Ranks quotes and recommends a shortlist. Each quote's impact is shown in **cars at risk** on a 60-day production calendar |
| 🤝 **Negotiation agents** | A buyer agent negotiates live against supplier agents. Each side has hidden limits (budget, lowest price) |
| 🧾 **PO → Pay agents** | Procurement, supplier, warehouse and accounts-payable agents. The 3-way match catches invoice problems; the AP agent explains why |
| 📈 **Performance** | Real delivery and invoice results update supplier scores, which the agents use next round |
| 🧪 **Bot Lab** | Test any agent on your own golden cases, with scorecards and a run-by-run comparison |
| 🧠 **AI Data Center** | Every AI action is logged, with its cost |

**Guardrails in code, not just in prompts:** no deal over budget, no supplier below its floor, no invented numbers, no prices leaked between suppliers, every answer checked and sent back if it breaks a rule.

---

## Try it

**1. Just look:** open the live website (link in the repo description). The 3D campus and the simulated flow run in your browser; nothing to install.

**2. Run the real AI agents locally** with your own key. Python 3.10+ only, standard library, no `pip install`:

```bash
git clone https://github.com/JNaveenRoshan/Zeno-Forge.git
cd Zeno-Forge
cp tools/negotiator/.env.example tools/negotiator/.env   # put your Sarvam AI key in it
python tools/negotiator/server.py
```

Open **http://localhost:8771/zeno-forge.html** → **Zeno Group HQ → Source-to-Pay**, and choose **Sarvam AI** in the steps.

- No key? Set `SARVAM_MOCK=1` before starting the server; every agent then runs in free mock mode.
- Your key stays in `tools/negotiator/.env` on your machine. It is only sent to `api.sarvam.ai`, never to the browser, and it's git-ignored.
- A full cycle (RFQ → evaluate → negotiate → pay) for 3 parts costs about ₹1–2 of Sarvam credit.

**3. Test the agents:** `python tools/negotiator/lab_server.py 8780`, then open http://localhost:8780 (the Bot Lab).

---

## Guides

| Guide | |
|---|---|
| 🚀 [**Build the whole thing in one prompt**](docs/one-prompt.md) | A single prompt you can give an AI coding assistant to rebuild Zeno Forge from scratch, plus how to steer it |
| 🛠️ [**Create, build and evaluate your own agents**](docs/custom-agents.md) | The 6-part recipe every agent here follows, a runnable template (should-cost agent), and how to test it |
| 🏭 [**Five S2P agents that work alongside SAP Ariba + Joule**](docs/s2p-agent-playbooks.md) | How to build a tail-spend negotiator, BOM/spec agent, supply chain risk agent, contract redlining agent and intake agent: facts, schema, rules, golden cases and the human gate for each |
| 🤖 [The agents, their prompts and rules](docs/ai-negotiation/agents.md) | Every agent's prompt, schema, hard rules and live test results |
| 🧪 [How to evaluate the bots](docs/ai-negotiation/how-to-evaluate.md) | Golden sets, scorecards, the Bot Lab, and a real bug it caught |
| 🎬 [Demo script](docs/video-script.md) | What to click and say in a demo |

---

## Project layout

```
zeno-forge.html            the whole app: 3D campus + Source-to-Pay UI (single file, Three.js from CDN)
models/                    Kenney CC0 3D models
tools/negotiator/
  server.py                local server: serves the app, the agent APIs, SQLite state
  agents.py                LLM client (Sarvam) + buyer/supplier negotiation prompts
  engine.py                negotiation engine: rounds, hard rules, scorecard
  rfq.py · evaluator.py · p2p.py      RFQ, evaluator and PO → Pay agents
  custom_agent_template.py  start here to build your own agent
  lab_server.py · lab/      Bot Lab (golden cases, scoring, scoreboard)
tools/build_site.py        builds the public static website into site/
```

## Publish your own copy

```bash
python tools/build_site.py
npx vercel deploy site --prod
```

The website contains no server code, no database and no key. The AI features show visitors how to run them locally with their own key.

---

Made with simulated data for demonstration. Zeno Group is a fictional company.
3D models by [Kenney](https://kenney.nl) (CC0). AI agents run on [Sarvam AI](https://www.sarvam.ai); the design works with any LLM that returns JSON.
