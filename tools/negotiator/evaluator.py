"""Evaluator bot (step 4 of the Source-to-Pay flow): reads the quotes for each part and recommends a shortlist.

The maths is done in code (in the browser) before the bot sees it: the weighted score of each quote and its
calendar impact (first delivery day, days without supply after the current contract ends, cars and euros at risk).
The bot's job is judgement and explanation: rank, comment on each supplier, pick a shortlist, suggest a mitigation.

Rules checked in code (a broken rule is sent back once, then a code-written fallback is used):
  - only suppliers that quoted may be named; shortlist 1-3 of them; the first pick is in the shortlist
  - every quoted supplier gets a comment
  - no invented numbers: every number >= 100 in the text must match a number the bot was given (within 2 %)
"""
import json, random, re, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
import agents, db

RUNS = {}
PROMPT_VERSION = 'eval-v1'
NUM_RE = re.compile(r'(?<![\w.])(\d{1,3}(?:[,.]\d{3})+|\d{3,})(?![\d])')

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'suppliers', 'shortlist', 'first_pick', 'mitigation', 'calendar_note'],
    'properties': {
        'summary': {'type': 'string'},
        'suppliers': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['supplier', 'flag', 'comment'],
                      'properties': {'supplier': {'type': 'string'}, 'flag': {'type': 'string', 'enum': ['strong', 'ok', 'watch', 'risk']},
                                     'comment': {'type': 'string'}}}},
        'shortlist': {'type': 'array', 'items': {'type': 'string'}},
        'first_pick': {'type': 'string'},
        'mitigation': {'type': 'string'},
        'calendar_note': {'type': 'string'},
    },
}


def system(line):
    w = line['weights']
    return (
        "You are Zeno Group's sourcing evaluator. You compare supplier quotes for one part and recommend which suppliers "
        "the buyer should negotiate with. You never negotiate yourself.\n\n"
        f"Part: {line['part']} for the {line['model']} ({line['vol']:,} cars a year, about {line['daily']:,} cars a day). "
        f"Today Zeno pays EUR {line['now']:,} per car to {line['incumbent']}, whose contract ends in {line['contract_end_day']} days.\n"
        f"Goal: {line['goal_name']}. RFQ weights: price {w['price']}%, ESG {w['esg']}%, lead time {w['lead']}%, risk {w['risk']}%.\n\n"
        "HOW TO JUDGE:\n"
        "1. Start from the weighted score (computed for you). You may disagree with it only for a clear reason "
        "(for example a big supply gap, low capacity or a risky condition), and you must say that reason.\n"
        "2. Treat cars at risk as serious: a cheaper supplier that stops production is usually worse than a slightly dearer one.\n"
        "3. New suppliers (no history) are riskier; a trial batch, warranty or penalty offered in their quote lowers that risk.\n"
        "4. Shortlist 1 to 3 suppliers to negotiate with, best first. Keep a competitor in the shortlist so the buyer has leverage.\n"
        "5. Mitigation: say how to avoid a supply gap (for example extend the current contract by N days, dual-source, or a ramp-up plan).\n\n"
        "RULES: use supplier names exactly as written in the list (without notes in brackets); give every supplier a flag and a comment; "
        "use only numbers that appear in the data (do not calculate new ones); write full sentences; "
        "keep each field to its word limit.\n\n"
        "FIELDS: summary = 2-3 full sentences (max 50 words); suppliers = one entry per supplier with flag and comment (one sentence, max 30 words); "
        "shortlist = 1-3 names, best first; first_pick = the first name of the shortlist; mitigation = 1-2 sentences (max 40 words); "
        "calendar_note = 1 sentence on the next 30 days of production. OUTPUT: only the JSON object.")


def user(line):
    rows = []
    for r in line['quotes']:
        rows.append(
            f"- {r['supplier']}{' (current supplier)' if r['incumbent'] else ''}{' (new, no history)' if r['new'] else ' (relationship ' + str(r.get('rel')) + ')'}: "
            f"EUR {r['price']:,} per car ({'+' if r['price'] >= line['now'] else '-'}EUR {abs(r['price'] - line['now']):,} vs today), lead {r['lead']} days, "
            f"capacity {r['covers']}% of need, ESG {r['esg']}, risk {r['risk']}. "
            f"Scores: price {r['s_price']}, ESG {r['s_esg']}, lead {r['s_lead']}, risk {r['s_risk']} -> weighted {r['total']} (rank {r['rank']}). "
            f"First delivery in {r['start_day']} days; days without supply after the contract ends: {r['gap_days']}; "
            f"cars at risk in the next 30 days: {r['cars_at_risk']:,} (EUR {r['eur_at_risk']:,} revenue). "
            f"Conditions: {'; '.join(r['conditions']) or 'none'}. ESG commitment: {r['esg_commitment'] or 'none'}.")
    return "Quotes:\n" + '\n'.join(rows) + "\n\nWrite your evaluation."


# ---------------------------------------------------------------- checks
def known_numbers(line):
    ks = {line['now'], line['vol'], line['daily'], line['need_week'], line['contract_end_day']} | set(line['weights'].values())
    for r in line['quotes']:
        ks |= {abs(r['price'] - line['now'])} | {v for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {abs(k) for k in ks if k}


def invented(text, ks):
    bad = []
    for m in NUM_RE.finditer(text or ''):
        v = float(m.group(1).replace(',', '').replace('.', '')) if re.search(r'[,.]\d{3}$', m.group(1)) else float(m.group(1))
        if v < 100 or 1990 <= v <= 2040: continue  # small numbers and years are fine
        if (text[m.end():m.end() + 2].strip() or ' ')[0] == '%': continue  # percentages are fine
        if not any(abs(v - k) <= max(2, k * .02) for k in ks): bad.append(m.group(1))
    return bad


def _norm(names, n):
    n = (n or '').strip()
    if n in names: return n
    for x in names:  # e.g. "Kraftmotor Castings (current supplier)" or "kraftmotor castings"
        if x.lower() in n.lower() or n.lower() in x.lower(): return x
    return n


def normalise(line, d):
    names = [r['supplier'] for r in line['quotes']]
    d['shortlist'] = [_norm(names, n) for n in d.get('shortlist') or []]
    d['first_pick'] = _norm(names, d.get('first_pick'))
    for x in d.get('suppliers') or []: x['supplier'] = _norm(names, x.get('supplier'))
    return d


def check(line, d):
    names = [r['supplier'] for r in line['quotes']]
    errs = []
    for k, lo, hi in (('summary', 8, 70), ('mitigation', 5, 50), ('calendar_note', 3, 45)):
        nw = len((d.get(k) or '').replace('...', ' ').split())
        if not lo <= nw <= hi: errs.append(f'"{k}" must be {lo}-{hi} real words (no "...")')
    sl = d.get('shortlist') or []
    if not 1 <= len(sl) <= 3: errs.append('shortlist must have 1-3 suppliers')
    if any(n not in names for n in sl): errs.append(f'shortlist may only use these names: {", ".join(names)}')
    if d.get('first_pick') not in sl: errs.append('first_pick must be the first supplier of the shortlist')
    got = {s.get('supplier') for s in d.get('suppliers') or []}
    if set(names) - got: errs.append(f'add a flag and comment for: {", ".join(sorted(set(names) - got))}')
    text = ' '.join([d.get('summary', ''), d.get('mitigation', ''), d.get('calendar_note', '')] + [s.get('comment', '') for s in d.get('suppliers') or []])
    bad = invented(text, known_numbers(line))
    if bad: errs.append(f'these numbers are not in the data, do not calculate new ones: {", ".join(bad[:4])}')
    return errs


def fallback(line):
    q = sorted(line['quotes'], key=lambda r: r['rank'])
    safe = sorted(q, key=lambda r: (r['cars_at_risk'] > 0, r['rank']))
    sl = [r['supplier'] for r in safe[:min(3, len(safe))]]
    return {'summary': f"{safe[0]['supplier']} is the best choice once supply risk is counted.", 'shortlist': sl, 'first_pick': sl[0],
            'suppliers': [{'supplier': r['supplier'], 'flag': 'risk' if r['cars_at_risk'] else 'strong' if r['rank'] == 1 else 'ok',
                           'comment': f"Weighted score {r['total']}, {r['cars_at_risk']:,} cars at risk." if r['cars_at_risk'] else f"Weighted score {r['total']}, no supply gap."} for r in q],
            'mitigation': f"Extend {line['incumbent']}'s contract until the new supplier delivers.", 'calendar_note': 'See the calendar for the days at risk.'}


def mock(line):
    time.sleep(random.uniform(.3, .7))
    return fallback(line)


def score(line, d, cnt):
    top2 = [r['supplier'] for r in sorted(line['quotes'], key=lambda r: r['rank'])[:2]]
    risky = {r['supplier'] for r in line['quotes'] if r['cars_at_risk'] > 0}
    checks = [
        ('Shortlist valid (1-3 quoted suppliers, pick first)', not cnt.get('fallbacks')),
        ('Every supplier commented', len(d['suppliers']) >= len(line['quotes'])),
        ('No invented numbers', not cnt.get('invented')),
        ('First pick fits the scores (top 2) or avoids a supply gap', d['first_pick'] in top2 or d['first_pick'] not in risky),
        ('Kept a competitor for leverage (≥ 2 in shortlist when possible)', len(d['shortlist']) >= min(2, len(line['quotes']))),
        ('Rules followed (≤ 1 retry)', cnt.get('retries', 0) <= 1),
    ]
    return {'key': line['key'], 'part': line['part'], 'model': line['model'], 'first_pick': d['first_pick'], 'shortlist': d['shortlist'],
            'code_top': top2[0] if top2 else None, 'agrees_with_score': bool(top2) and d['first_pick'] == top2[0],
            'retries': cnt.get('retries', 0), 'fallbacks': cnt.get('fallbacks', 0), 'hiccups': cnt.get('hiccups', 0),
            'checks': [{'name': n, 'pass': bool(ok)} for n, ok in checks], 'grade': round(sum(1 for _, ok in checks if ok) / len(checks) * 100)}


# ---------------------------------------------------------------- run
class EvalRun:
    def __init__(self, mode, lines):
        self.id = time.strftime('eval-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.mode, self.lines = mode, lines
        self.events, self.cond = [], threading.Condition()
        self.done = self.stopped = False
        self.usage = {'prompt': 0, 'completion': 0}
        self.scores = []
        db.new_rfq_run(self.id, mode, PROMPT_VERSION, [{'key': l['key'], 'part': l['part']} for l in lines])

    def cost(self):
        return self.usage['prompt'] * agents.INR_IN + self.usage['completion'] * agents.INR_OUT

    def emit(self, e):
        e.setdefault('t', time.time())
        with self.cond:
            self.events.append(e); self.cond.notify_all()


def eval_part(run, line):
    key, cnt, d, errs = line['key'], {}, None, []
    run.emit({'type': 'status', 'key': key, 'from': 'Evaluator bot', 'msg': 'Comparing the quotes…'})
    for attempt in range(4):
        sysmsg = system(line) + (f"\n\nYOUR LAST ANSWER WAS REJECTED: {'; '.join(errs)}. Fix it." if errs and errs != ['hiccup'] else '')
        try:
            d = mock(line) if run.mode == 'mock' else agents.chat_json(sysmsg, user(line), 'evaluation', SCHEMA, run.usage, max_tokens=3000, effort='medium')  # 'low' effort makes this model write '...' placeholders here
        except agents.SarvamError as e:
            if e.fatal: raise
            cnt['api_errors'] = cnt.get('api_errors', 0) + 1; d = None; errs = [str(e)]
            run.emit({'type': 'rejected', 'key': key, 'from': 'Sarvam', 'msg': f'Sarvam error, retrying: {e}'}); continue
        d = normalise(line, d)
        # a reply of placeholders ("...", empty fields) is a model glitch, not a rule break: ask again without counting a retry
        if len((d.get('summary') or '').replace('...', ' ').split()) < 3:
            cnt['hiccups'] = cnt.get('hiccups', 0) + 1; errs = ['hiccup']
            run.emit({'type': 'rejected', 'key': key, 'from': 'Sarvam', 'msg': 'The model sent a placeholder reply ("..."), asking again'}); continue
        errs = check(line, d)
        if any('not in the data' in x for x in errs): cnt['invented'] = cnt.get('invented', 0) + 1
        if not errs: break
        cnt['retries'] = cnt.get('retries', 0) + 1
        run.emit({'type': 'rejected', 'key': key, 'from': 'Rule check', 'msg': 'Evaluation sent back: ' + '; '.join(errs)})
    if d is None or errs:
        cnt['fallbacks'] = cnt.get('fallbacks', 0) + 1; d = fallback(line); d['fallback'] = True
    names = [r['supplier'] for r in line['quotes']]
    d['suppliers'] = [s for s in d['suppliers'] if s.get('supplier') in names]
    sc = score(line, d, cnt)
    run.scores.append(sc)
    db.add_rfq_doc(run.id, key, d)
    run.emit({'type': 'part_done', 'key': key, 'from': 'Evaluator bot', 'eval': d, 'score': sc, 'msg': d['summary']})


def run_all(run):
    status = 'done'
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for f in [pool.submit(eval_part, run, l) for l in run.lines]: f.result()
    except agents.SarvamError as e:
        status = 'error'; run.emit({'type': 'status', 'key': None, 'from': 'Engine', 'msg': f'Stopped: {e}'})
    except Exception as e:
        status = 'error'; run.emit({'type': 'status', 'key': None, 'from': 'Engine', 'msg': f'Error: {e}'})
    finally:
        sc = run.scores
        summ = {'parts': len(sc), 'agree_pct': round(sum(1 for s in sc if s['agrees_with_score']) / len(sc) * 100) if sc else 0,
                'retries': sum(s['retries'] for s in sc), 'fallbacks': sum(s['fallbacks'] for s in sc),
                'grade': round(sum(s['grade'] for s in sc) / len(sc)) if sc else 0}
        db.finish_rfq_run(run.id, status, run.cost(), summ, sc)
        run.emit({'type': 'run_done', 'key': None, 'from': 'Engine', 'status': status, 'summary': summ, 'cost': round(run.cost(), 3),
                  'msg': f"Evaluation done. Bot grade {summ['grade']}/100 · ≈ ₹{run.cost():.2f}"})
        run.done = True


def start(mode, lines):
    run = EvalRun(mode, lines)
    RUNS[run.id] = run
    threading.Thread(target=run_all, args=(run,), daemon=True).start()
    return run
