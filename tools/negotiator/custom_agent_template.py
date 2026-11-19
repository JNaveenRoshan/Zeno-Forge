"""Template: build, run and evaluate your own AI agent, the same way every agent in Zeno Forge is built.

Example agent: a SHOULD-COST agent. Code calculates what a part *should* cost from its material, labour and energy;
the agent compares that with the supplier's quote, gives a verdict and writes the argument the buyer can use.

  python tools/negotiator/custom_agent_template.py --mode mock      # free, rule-based stand-in
  python tools/negotiator/custom_agent_template.py --mode sarvam    # real Sarvam AI (needs tools/negotiator/.env)

Copy this file, rename it, and change the 6 numbered parts below.
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agents
from evaluator import invented   # "no invented numbers" check, shared by every agent

# ---- 1. FACTS: code does all the maths. Never ask the LLM to calculate. ----
def should_cost(case):
    material = sum(kg * price for kg, price in case['materials'].values())
    labour = case['labour_hours'] * case['labour_rate']
    energy = case['energy_kwh'] * case['energy_price']
    fair = round((material + labour + energy) * (1 + case['overhead_pct'] / 100) * (1 + case['margin_pct'] / 100))
    gap = case['quote'] - fair
    return {'material': round(material), 'labour': round(labour), 'energy': round(energy), 'fair_price': fair,
            'quote': case['quote'], 'gap': gap, 'gap_pct': round(gap / fair * 100, 1)}

# ---- 2. SCHEMA: the exact JSON the agent must return. Use fixed choices (enum) wherever you can. ----
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'biggest_driver', 'argument'],
          'properties': {'verdict': {'type': 'string', 'enum': ['fair', 'overpriced', 'underpriced']},
                         'biggest_driver': {'type': 'string', 'enum': ['material', 'labour', 'energy', 'margin']},
                         'argument': {'type': 'string'}}}

# ---- 3. PROMPT: role, the facts, the rules, the output. Short and specific. ----
def system(case, f):
    return (f"You are Zeno Group's cost analyst. A supplier quoted EUR {f['quote']:,} per {case['unit']} for {case['part']}.\n"
            f"Calculated should-cost: material EUR {f['material']:,}, labour EUR {f['labour']:,}, energy EUR {f['energy']:,}; "
            f"fair price including overhead and a normal margin: EUR {f['fair_price']:,}. Gap: EUR {f['gap']:,} ({f['gap_pct']}%).\n\n"
            "RULES:\n"
            "1. verdict: 'overpriced' if the gap is above +5%, 'underpriced' if below -5%, otherwise 'fair'.\n"
            "2. biggest_driver: the cost element that matters most for this part.\n"
            "3. argument: 2 sentences (max 45 words) the buyer can say to the supplier. Use only the numbers above; do not calculate new ones.\n"
            "OUTPUT: only the JSON object.")

# ---- 4. RULE CHECKS: code checks every answer. Broken rule -> sent back once -> safe fallback. ----
def check(case, f, d):
    errs = []
    want = 'overpriced' if f['gap_pct'] > 5 else 'underpriced' if f['gap_pct'] < -5 else 'fair'
    if d.get('verdict') != want: errs.append(f"verdict must be '{want}' for a gap of {f['gap_pct']}%")
    words = len((d.get('argument') or '').split())
    if not 8 <= words <= 60: errs.append('argument must be 2 short sentences')
    bad = invented(d.get('argument', ''), {v for v in f.values() if isinstance(v, (int, float)) and v} | {abs(f['gap'])})
    if bad: errs.append(f"numbers not in the data: {', '.join(bad[:3])}")
    return errs

def mock(case, f):   # free stand-in with the same JSON, for testing without credits
    v = 'overpriced' if f['gap_pct'] > 5 else 'underpriced' if f['gap_pct'] < -5 else 'fair'
    return {'verdict': v, 'biggest_driver': 'material',
            'argument': f"Our should-cost for this part is EUR {f['fair_price']:,}, and your quote is EUR {f['quote']:,}. Can you show us where the gap comes from?"}

def run_agent(case, mode, usage):
    f, errs, d, retries = should_cost(case), [], None, 0
    for attempt in range(2):
        sysmsg = system(case, f) + (f"\nYOUR LAST ANSWER WAS REJECTED: {'; '.join(errs)}." if errs else '')
        d = mock(case, f) if mode == 'mock' else agents.chat_json(sysmsg, 'Write your analysis.', 'should_cost', SCHEMA, usage, effort='medium')
        errs = check(case, f, d)
        if not errs: break
        retries += 1
    fallback = bool(errs)
    if fallback: d = mock(case, f)
    return f, d, retries, fallback, errs

# ---- 5. GOLDEN CASES: inputs + what a GOOD agent must do. Write the expectation before you run. ----
GOLDEN = [
    {'key': 'C1', 'part': 'Steel wheel rim', 'unit': 'rim', 'quote': 74, 'materials': {'steel': (9.5, 1.1)}, 'labour_hours': .4, 'labour_rate': 38,
     'energy_kwh': 22, 'energy_price': .18, 'overhead_pct': 15, 'margin_pct': 10, 'expect': {'verdict': 'overpriced', 'driver_in': ['material', 'labour']}},
    {'key': 'C2', 'part': 'Seat frame', 'unit': 'frame', 'quote': 95, 'materials': {'steel': (12, 1.1), 'foam': (2.2, 4.0)}, 'labour_hours': 1.2, 'labour_rate': 38,
     'energy_kwh': 30, 'energy_price': .18, 'overhead_pct': 15, 'margin_pct': 10, 'expect': {'verdict': 'fair', 'driver_in': ['labour', 'material']}},
    {'key': 'C3', 'part': 'Aluminium door panel', 'unit': 'panel', 'quote': 50, 'materials': {'aluminium': (6.5, 2.6)}, 'labour_hours': .5, 'labour_rate': 38,
     'energy_kwh': 40, 'energy_price': .18, 'overhead_pct': 15, 'margin_pct': 10, 'expect': {'verdict': 'underpriced', 'driver_in': ['material']}},
]

# ---- 6. EVALUATE: your checks (golden) + rule checks (auto) + cost. Run it 3 times for an LLM. ----
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--mode', choices=['mock', 'sarvam'], default='mock'); a = ap.parse_args()
    if a.mode == 'sarvam' and not agents.api_key(): sys.exit('Add SARVAM_API_KEY to tools/negotiator/.env')
    usage, t0, passed = {'prompt': 0, 'completion': 0}, time.time(), 0
    for c in GOLDEN:
        f, d, retries, fb, why = run_agent(c, a.mode, usage)
        golden = [('verdict', d['verdict'] == c['expect']['verdict']), ('driver', d['biggest_driver'] in c['expect']['driver_in'])]
        auto = [('no fallback', not fb), ('≤ 1 retry', retries <= 1)]
        ok = all(x for _, x in golden + auto); passed += ok
        print(f"{c['key']} {c['part']:22s} fair €{f['fair_price']:>4} quote €{f['quote']:>4} ({f['gap_pct']:+}%) → {d['verdict']:11s} {'PASS' if ok else 'FAIL'} "
              f"{[n for n, x in golden + auto if not x] or ''}\n     “{d['argument']}”" + (f"\n     rule check: {'; '.join(why)}" if fb else ''))
    cost = usage['prompt'] * agents.INR_IN + usage['completion'] * agents.INR_OUT
    print(f"\n{passed}/{len(GOLDEN)} cases passed · {time.time() - t0:.0f}s · ₹{cost:.3f}")

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8'); main()
