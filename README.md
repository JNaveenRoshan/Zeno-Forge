# Zeno Forge: Source-to-Pay with AI agents

A 3D car-factory campus with an AI agent at every step of Source-to-Pay:
Parts → Suppliers → **RFQ** → **Evaluate** → **Negotiate** → Award → **PO → Pay** → Performance.

- **Live 3D demo** (no AI, nothing to install): see the website link in the repo description.
- **AI agents** (RFQ writer, supplier quoting, evaluator, buyer vs supplier negotiation, PO → Pay with 3-way match): run locally with **your own** Sarvam AI key.

## Run it with the AI agents
Requires Python 3.10+ and nothing else (standard library only).

```bash
git clone https://github.com/JNaveenRoshan/Zeno-Forge.git
cd Zeno-Forge
cp tools/negotiator/.env.example tools/negotiator/.env   # then put your key in it
python tools/negotiator/server.py
```

Open http://localhost:8771/zeno-forge.html and choose **Sarvam AI** in the Source-to-Pay steps.

- No key yet? Set `SARVAM_MOCK=1` before starting the server to try every agent in free mock mode.
- Your key stays in `tools/negotiator/.env` on your machine. It is only sent to api.sarvam.ai and never to the browser.

## The agents
| Step | Agent | What it does |
|---|---|---|
| RFQ | Buyer agent + one supplier agent per invited supplier | Writes the RFQ; each supplier quotes price, lead time, capacity, ESG, conditions |
| Evaluate | Evaluator agent | Ranks quotes, flags risks, recommends a shortlist (impact shown in cars at risk) |
| Negotiate | Buyer agent vs supplier agents | Live rounds; neither side sees the other's limits |
| PO → Pay | Procurement, supplier, warehouse and AP agents | PO → confirmation → goods receipt → invoice → 3-way match; the AP agent explains failures |

The AI writes; code supplies the facts, checks every answer (no invented numbers, no deal over budget, no leaked prices) and a human makes the final call.

## Bot Lab (optional)
`python tools/negotiator/lab_server.py 8780` opens a test bench at http://localhost:8780 to test the agents on your own golden cases.

## Publish the website yourself
```bash
python tools/build_site.py
npx vercel deploy site --prod
```

3D models: [Kenney](https://kenney.nl) (CC0).
