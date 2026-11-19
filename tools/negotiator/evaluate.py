"""Agent test bench: replays the same negotiation scenarios so prompt / model changes can be compared fairly.

  python tools/negotiator/evaluate.py --mode mock   --label baseline     # free, rule-based agents
  python tools/negotiator/evaluate.py --mode sarvam --label v2 --n 6     # real Sarvam agents (uses credits)
  python tools/negotiator/evaluate.py --history                            # compare all saved runs

Every scenario uses a fixed random seed, so the suppliers' hidden floors and eagerness are identical between runs.
Results are stored in zeno.db (eval_runs / eval_scores) and shown in the app's Negotiate tab.
"""
import argparse, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agents, db, engine

S = lambda n, quote, esg, lead, risk, rel: {'n': n, 'quote': quote, 'esg': esg, 'lead': lead, 'risk': risk, 'rel': f'{rel} (score)' if rel else 'new, no history', 'rel_score': rel}
SCENARIOS = [
    # (model, part, spec, category, vol, plant, today, budget, target, suppliers)
    ('Zeno Volt', 'Battery pack 82 kWh', '82 kWh, 400 V, NMC cells', 'Battery', 52000, 'Zeno EV Plant', 8610, 8610, 8180,
     [S('Voltacell Energy', 8610, 75, 21, 'Medium', 71), S('Hanseo Battery', 8352, 79, 18, 'Low', None), S('NorthVolta Cells', 8180, 88, 28, 'Low', None)]),
    ('Zeno Volt', 'E-motor + inverter', 'Single motor, RWD, 160 kW', 'E-motor', 52000, 'Zeno EV Plant', 1950, 1950, 1850,
     [S('Helix E-Drive', 1950, 82, 14, 'Low', 88), S('Axion Drives', 1833, 79, 16, 'Low', None)]),
    ('Zeno Aero', 'Body-in-white panels', 'HSS body, aluminium closures', 'Body', 28000, 'Zeno EV Plant', 1780, 1800, 1690,
     [S('Doorworks Pressings', 1780, 78, 6, 'Low', 83), S('Pressline Iberia', 1691, 74, 11, 'Low', None)]),
    ('Zeno Terra', 'Engine 2.0 L', '1,998 cc, 190 hp, Euro 6e', 'Engine', 41000, 'Zeno Torque Plant', 3150, 3150, 2990,
     [S('Kraftmotor Castings', 3150, 73, 18, 'Medium', 80), S('Kessler Motorenwerk', 3024, 76, 20, 'Low', None)]),
    ('Zeno Strada', 'Wheels & tyres x4', '16 inch steel, 205/55 R16', 'Wheels', 47000, 'Zeno Torque Plant', 560, 580, 530,
     [S('Rimco Tyres', 560, 70, 9, 'Medium', 64), S('Velora Wheels', 577, 82, 12, 'Low', None), S('Rondo Tyres', 515, 65, 10, 'Medium', None)]),
    ('Zeno Volt', 'Seats & cockpit module', '5 seats + cockpit module', 'Seats', 52000, 'Zeno EV Plant', 1420, 1420, 1350,
     [S('Seatwise Interiors', 1420, 80, 7, 'Low', 78), S('Sitwell Interiors', 1377, 80, 9, 'Low', None)]),
]


def line_for(i, sc):
    model, part, spec, cat, vol, plant, now, budget, target, sups = sc
    return {'key': f'EVAL{i}|{part}', 'model': model, 'part': part, 'spec': spec, 'category': cat, 'vol': vol, 'plant': plant,
            'now': now, 'budget': budget, 'target': target, 'suppliers': [dict(s) for s in sups]}


def run_bench(mode, label, n, rounds):
    if mode == 'sarvam' and not agents.api_key():
        sys.exit('No Sarvam key: add SARVAM_API_KEY to tools/negotiator/.env')
    lines = [line_for(i, sc) for i, sc in enumerate(SCENARIOS[:n])]
    random.seed(20261119)  # same hidden floors / eagerness every time
    run = engine.Run(mode, lines, rounds, cap_inr=200, label=label)
    engine.RUNS[run.id] = run
    t0 = time.time()
    engine.run_all(run)  # synchronous
    summ = engine.run_summary(run.scores)
    print(f"\n{label} · {mode} · {len(run.scores)} scenarios · {time.time() - t0:.0f}s · ₹{run.cost():.2f}")
    print(f"{'part':28} {'grade':>5} {'deal':>5} {'saving':>7} {'rounds':>6} {'concess.':>8} {'capture':>8} {'R1acc':>5} {'stalls':>6} {'retries':>7}")
    for s in run.scores:
        print(f"{s['part'][:28]:28} {s['grade']:>5} {('yes' if s['deal'] else 'no'):>5} {s['saving_pct']:>6.1f}% {s['rounds_used']:>6} {s['avg_concession_pct']:>7.1f}% "
              f"{(s['buyer_capture_pct'] or 0):>7.0f}% {s['r1_accepts']:>5} {s['stalls']:>6} {s['rule_retries']:>7}")
        for c in s['checks']:
            if not c['pass']: print(f"{'':30}✕ {c['name']}")
    print('SUMMARY', summ)
    return run


def history():
    print(f"{'when':17} {'mode':8} {'label':12} {'grade':>5} {'deals':>6} {'saving':>7} {'rounds':>6} {'concess.':>8} {'capture':>8} {'R1acc':>5} {'stalls':>6}")
    for e in db.list_evals(50):
        s = e['summary']
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(e['started'])):17} {e['mode']:8} {e['label'][:12]:12} {s.get('grade', 0):>5.0f} {s.get('deal_rate_pct', 0):>5}% "
              f"{s.get('avg_saving_pct', 0):>6.1f}% {s.get('avg_rounds', 0):>6} {s.get('avg_concession_pct', 0):>7.1f}% {s.get('avg_buyer_capture_pct', 0):>7.0f}% {s.get('r1_accepts', 0):>5} {s.get('stalls', 0):>6}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['mock', 'sarvam'], default='mock')
    ap.add_argument('--label', default=agents.PROMPT_VERSION)
    ap.add_argument('--n', type=int, default=len(SCENARIOS))
    ap.add_argument('--rounds', type=int, default=5)
    ap.add_argument('--history', action='store_true')
    a = ap.parse_args()
    history() if a.history else run_bench(a.mode, a.label, a.n, a.rounds)
