"""Negotiation engine: runs the rounds, calls the agents, enforces the hard rules in code, and scores every part.

Rules the agents cannot break (a broken rule is sent back to the agent once with the reason, then corrected):
  - a supplier never offers or accepts below its floor, and never raises its price after an offer
  - a supplier may not repeat the same price (above its floor) without improving something else
  - the buyer never counters or accepts above its budget, never lowers a counter it already made
  - the buyer may not accept an opening offer above its target in round 1, and a counter must be below the offer
  - the price written in a message must match the price field
  - nobody's private number is passed to the other side (messages are scanned and redacted)
  - the run stops when the credit cap is reached
"""
import random, re, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
import agents, db

PRICE_RE = re.compile(r'(?:EUR|€)\s?(\d[\d,\.]{2,})')
RUNS = {}  # id -> Run


NUM_RE = re.compile(r'(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{3,6})(?![\d])')


ACCEPT_RE = re.compile(r"\b(we|i)\s+(accept|agree)|\bagreed\b|\blet'?s (finali[sz]e|close|lock)|\bdeal is done\b", re.I)


def bare_numbers(text):
    """Every number >= 100 in a message, with or without 'EUR' (years and [withheld] are ignored)."""
    out = []
    for m in NUM_RE.finditer(text or ''):
        v = float(m.group(1).replace(',', ''))
        if v >= 100 and not 1990 <= v <= 2040: out.append(v)
    return out


def prices_in(text):
    out = []
    for m in PRICE_RE.finditer(text or ''):
        try: out.append(float(m.group(1).replace(',', '').rstrip('.')))
        except ValueError: pass
    return [p for p in out if p > 100]


class Run:
    def __init__(self, mode, lines, rounds, cap_inr, label=None):
        self.id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.mode, self.lines, self.rounds, self.cap = mode, lines, rounds, cap_inr
        self.label = label or agents.PROMPT_VERSION
        self.events, self.cond = [], threading.Condition()
        self.paused = self.stopped = self.done = False
        self.usage = {'prompt': 0, 'completion': 0}
        self.finals, self.scores = [], []
        self.count = {}  # per-part counters: retries for each rule, fallbacks
        db.new_negotiation(self.id, mode, {'prompt_version': self.label, 'rounds': rounds, 'cap_inr': cap_inr,
                                           'lines': [{k: v for k, v in l.items() if k != 'suppliers'} | {'suppliers': [s['n'] for s in l['suppliers']]} for l in lines]})

    def cost(self):
        return self.usage['prompt'] * agents.INR_IN + self.usage['completion'] * agents.INR_OUT

    def bump(self, key, what):
        c = self.count.setdefault(key, {}); c[what] = c.get(what, 0) + 1

    def emit(self, e):
        e.setdefault('t', time.time())
        db.add_move(self.id, e)
        with self.cond:
            self.events.append(e)
            self.cond.notify_all()

    def wait_if_paused(self):
        while self.paused and not self.stopped:
            time.sleep(.2)


def _money_variants(v):
    v = int(round(v))
    return {str(v), f'{v:,}', f'{v:,}'.replace(',', '.'), f'{v / 1000:.1f}k', f'{v / 1000:.2f}k'}


def redact(text, secrets):
    hit = False
    for v in secrets:
        for s in _money_variants(v):
            pat = r'(?<![\d])' + re.escape(s) + r'(?![\d])'
            if len(s) >= 3 and re.search(pat, text):
                text = re.sub(pat, '[withheld]', text); hit = True
    return text, hit


# ---------------------------------------------------------------- supplier turn
def _supplier_move(run, line, s, rnd):
    hist, key = s['history'], line['key']
    for attempt in range(2):
        try:
            if run.mode == 'mock':
                mv = agents.mock_supplier(s, rnd, s['counter'], s['offer'])
            else:
                note = '' if attempt == 0 else f"\nYour last answer broke a rule: {s['why']}. Answer again, following your negotiation rules."
                mv = agents.chat_json(agents.supplier_system(line, s), agents.supplier_user(rnd, run.rounds, hist, s['counter'], s['offer']) + note,
                                      'supplier_move', agents.SUPPLIER_SCHEMA, run.usage)
        except agents.SarvamError as e:
            if e.fatal: raise
            s['why'] = str(e); run.bump(key, 'api_errors'); continue
        act, price = mv.get('action'), float(mv.get('price') or 0)
        lead = max(s['lead0'] - 4, min(int(mv.get('lead_days') or s['lead']), s['lead0']))
        if act == 'accept':
            if not s['counter'] or s['counter'] < s['floor']:
                s['why'] = 'you accepted a price below your minimum'; run.bump(key, 'below_floor')
                run.emit({'type': 'rejected', 'key': key, 'from': s['n'], 'to': 'Buyer', 'round': rnd, 'msg': 'Engine blocked: supplier tried to accept below its minimum.', 'rejected': True}); continue
            return {'action': 'accept', 'price': s['counter'], 'lead': s['lead'], 'esg': mv.get('esg_commitment'), 'msg': mv.get('message', '')}
        if price < s['floor']:
            s['why'] = 'you offered below your minimum'; run.bump(key, 'below_floor')
            run.emit({'type': 'rejected', 'key': key, 'from': s['n'], 'to': 'Buyer', 'round': rnd, 'msg': "Engine blocked: offer was below the supplier's minimum.", 'rejected': True}); continue
        said = prices_in(mv.get('message', ''))
        if said and all(abs(p - price) > max(1, price * .002) for p in said) and attempt == 0:
            s['why'] = f'your message says EUR {said[0]:,.0f} but your price field says EUR {price:,.0f}; they must match'; run.bump(key, 'mismatch'); continue
        if s['offer'] and price > s['offer']:
            price = s['offer']  # suppliers can't walk their price back up
        stalled = s['offer'] and abs(price - s['offer']) <= s['offer'] * .002 and s['offer'] > s['floor'] * 1.01
        improved_other = lead < s['lead'] or (mv.get('esg_commitment') and mv.get('esg_commitment') != s['commit'])
        if stalled and not improved_other and rnd < run.rounds and attempt == 0:
            s['why'] = 'you repeated your previous price without improving anything; lower the price or improve another term'; run.bump(key, 'stall')
            run.emit({'type': 'rejected', 'key': key, 'from': s['n'], 'to': 'Buyer', 'round': rnd, 'msg': 'Engine sent the supplier back: it repeated its price without any concession.', 'rejected': True}); continue
        if stalled: run.bump(key, 'stalls_kept')
        return {'action': 'offer', 'price': round(price), 'lead': lead, 'esg': mv.get('esg_commitment'), 'msg': mv.get('message', '')}
    run.bump(key, 'fallbacks')
    mv = agents.mock_supplier(s, rnd, s['counter'], s['offer'])
    return {'action': mv['action'], 'price': mv['price'], 'lead': s['lead'], 'esg': None, 'msg': mv['message'], 'fallback': True}


# ---------------------------------------------------------------- buyer turn
def _norm(t):
    return re.sub(r'[^a-z0-9]', '', (t or '').lower())


def _buyer_moves(run, line, offers, rnd):
    key, target = line['key'], line['target']
    why = ''
    for attempt in range(2):
        try:
            if run.mode == 'mock':
                out = agents.mock_buyer(line, rnd, run.rounds, offers)
            else:
                note = '' if attempt == 0 else f'\nYour last answer broke a rule: {why}. Answer again, following your negotiation rules.'
                out = agents.chat_json(agents.buyer_system(line), agents.buyer_user(rnd, run.rounds, offers, line['budget']) + note, 'buyer_moves', agents.BUYER_SCHEMA, run.usage)
        except agents.SarvamError as e:
            if e.fatal: raise
            run.bump(key, 'api_errors'); continue
        raw = {_norm(d.get('supplier')): d for d in out.get('decisions', [])}
        dec = {}
        for o in offers:
            k = _norm(o['n']); d = raw.get(k) or next((v for kk, v in raw.items() if kk and (kk in k or k in kk)), None)
            if d: dec[o['n']] = d
        problems = []
        for o in offers:
            d = dec.get(o['n'])
            if not d: problems.append(f"no decision for {o['n']}"); continue
            p, said = float(d.get('price') or 0), prices_in(d.get('message', ''))
            if d['action'] == 'counter' and p >= o['price']: problems.append(f"your counter to {o['n']} was not below their offer")
            if d['action'] == 'accept' and rnd == 1 and o['price'] > target: problems.append(f"you accepted {o['n']}'s opening offer in round 1; counter first")
            if d['action'] == 'counter' and said and all(abs(x - p) > max(1, p * .002) for x in said): problems.append(f"your message to {o['n']} says EUR {said[0]:,.0f} but the price field says EUR {p:,.0f}")
            if d['action'] == 'counter' and o.get('counter') and abs(p - o['counter']) <= o['counter'] * .002 and o['counter'] < min(o['price'] * .99, line['budget']) and rnd < run.rounds:
                problems.append(f"your counter to {o['n']} did not move from EUR {o['counter']:,.0f}; raise it towards their offer")
            stray = [x for x in bare_numbers(d.get('message', '')) if all(abs(x - v) > max(1, v * .002) for v in [p, o['price'], o.get('counter') or 0, line['vol']] + list(o.get('hist') or []))]
            if stray: problems.append(f"your message to {o['n']} mentions {stray[0]:,.0f}, which is neither their offer nor your price")
        if problems and attempt == 0:
            why = '; '.join(problems)
            for w in ('not below', 'round 1', 'says EUR', 'no decision', 'did not move', 'neither their'):
                if w in why: run.bump(key, {'not below': 'counter_not_below', 'round 1': 'r1_accept', 'says EUR': 'mismatch', 'no decision': 'missing',
                                            'did not move': 'buyer_stall', 'neither their': 'stray_number'}[w])
            run.emit({'type': 'rejected', 'key': key, 'from': 'Buyer', 'to': offers[0]['n'], 'round': rnd, 'msg': f'Engine sent the buyer back: {problems[0]}.', 'rejected': True})
            continue
        return dec, False
    run.bump(key, 'fallbacks')
    return {d['supplier']: d for d in agents.mock_buyer(line, rnd, run.rounds, offers)['decisions']}, True


# ---------------------------------------------------------------- one part
def negotiate_part(run, line, pool):
    key, budget, now = line['key'], line['budget'], line['now']
    if line['target'] >= budget * .98 and not line.get('fixed_target'):  # a target equal to the budget leaves nothing to negotiate for
        line['target'] = round(min(budget, now) * .95)
    target = line['target']
    line['rounds'] = run.rounds
    sups = []
    for s in line['suppliers']:
        # proven partners lean on goodwill; new suppliers are hungrier for a first contract
        eager = min(.95, max(.2, random.uniform(.3, .75) + (((s['rel_score'] - 75) / 100) if s.get('rel_score') is not None else .15)))
        if s.get('eager') is not None: eager = float(s['eager'])  # Bot Lab / golden cases can fix the hidden values
        floor = round(float(s['floor'])) if s.get('floor') else round(s['quote'] * random.uniform(.86, .94))
        sups.append(dict(s, floor=floor, eager=eager, offer=None, counter=None, status='live',
                         lead0=s['lead'], history=[], why='', commit=None, hist=[], opening=None))
    run.emit({'type': 'rfq', 'key': key, 'from': 'Buyer', 'to': 'all', 'round': 0,
              'msg': f"Request for quote: {line['part']} for {line['model']}, {line['vol']:,} cars / year. Please send your best price per car, lead time and ESG commitments."})
    last_round = 0
    for rnd in range(1, run.rounds + 1):
        if run.stopped: break
        run.wait_if_paused()
        if run.cost() >= run.cap:
            run.emit({'type': 'status', 'key': key, 'from': 'Engine', 'to': 'all', 'round': rnd, 'msg': f'Credit cap reached (₹{run.cap:.0f}). Stopping.'}); run.stopped = True; break
        last_round = rnd
        live = [s for s in sups if s['status'] == 'live']
        moves = dict(zip([s['n'] for s in live], pool.map(lambda s: _supplier_move(run, line, s, rnd), live)))
        for s in live:
            mv = moves[s['n']]
            text, leak = redact(mv['msg'] or '', [s['floor']])
            if leak: run.bump(key, 'redactions')
            if not re.search(r'[A-Za-z]', text):
                text = f"We accept EUR {mv['price']:,.0f}." if mv['action'] == 'accept' else f"Our offer is EUR {mv['price']:,.0f} per car."
            if mv['action'] == 'accept':
                s['status'], s['agreed'], s['agreed_round'] = 'deal', mv['price'], rnd
                run.emit({'type': 'accept', 'key': key, 'from': s['n'], 'to': 'Buyer', 'round': rnd, 'price': mv['price'], 'lead': s['lead'], 'msg': text, **({'fallback': True} if mv.get('fallback') else {}), **({'redacted': True} if leak else {})})
                s['history'].append(f"You: accepted EUR {mv['price']:,.0f}")
                continue
            s['offer'], s['lead'] = mv['price'], mv['lead']
            s['hist'].append(s['offer']); s['opening'] = s['opening'] or s['offer']
            if mv.get('esg'): s['commit'] = mv['esg']
            run.emit({'type': 'offer', 'key': key, 'from': s['n'], 'to': 'Buyer', 'round': rnd, 'price': s['offer'], 'lead': s['lead'], 'esg': mv.get('esg'), 'msg': text,
                      **({'fallback': True} if mv.get('fallback') else {}), **({'redacted': True} if leak else {})})
            s['history'].append(f"You (round {rnd}): offer EUR {s['offer']:,.0f}, lead {s['lead']} d. \"{text}\"")
        live = [s for s in sups if s['status'] == 'live']
        if not live: break
        run.wait_if_paused()
        offers = [{'n': s['n'], 'price': s['offer'], 'lead': s['lead'], 'esg': s['esg'], 'risk': s['risk'], 'rel': s['rel'], 'commit': s['commit'], 'counter': s['counter'], 'hist': s['hist']} for s in live]
        dec, fb = _buyer_moves(run, line, offers, rnd)
        if rnd == run.rounds:
            fits = [s for s in live if s['offer'] and s['offer'] <= budget]
            if fits and not any((dec.get(s['n']) or {}).get('action') == 'accept' for s in fits):
                best = min(fits, key=lambda s: s['offer'])
                run.bump(key, 'final_rescue')
                dec[best['n']] = {'supplier': best['n'], 'action': 'accept', 'price': best['offer'], 'message': f"We accept EUR {best['offer']:,.0f} per car."}
                run.emit({'type': 'rejected', 'key': key, 'from': 'Buyer', 'to': best['n'], 'round': rnd, 'rejected': True,
                          'msg': f"Engine: final round and {best['n']}'s offer is within budget, so it is accepted instead of ending with no deal."})
        for s in live:
            d = dec.get(s['n']) or {'action': 'counter', 'price': (s['counter'] or round(s['offer'] * .92)), 'message': 'Please improve your offer.'}
            act, price = d['action'], float(d.get('price') or 0)
            said = prices_in(d.get('message', ''))
            if act == 'accept' and said and min(said) < s['offer'] * .998:
                act, price = 'counter', min(said)  # the message proposes a lower price: that's a counter
            if act == 'accept' and rnd == 1 and s['offer'] > target:
                act, price = 'counter', round(s['offer'] * .92)  # no instant accepts of opening offers
            if act == 'counter' and said and all(abs(x - price) > max(1, price * .002) for x in said) and min(said) < s['offer']:
                price = min(said)  # trust the number the supplier will actually read
            own = {round(s['offer'] or 0), round(price)}
            secrets = [v for v in [budget, target] + [o['offer'] for o in sups if o is not s and o['offer']] if round(v) not in own]
            text, leak = redact(d.get('message', '') or '', secrets)
            if leak: run.bump(key, 'redactions')
            if act == 'accept':
                if s['offer'] > budget:
                    run.bump(key, 'over_budget')
                    run.emit({'type': 'rejected', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'msg': 'Engine blocked: buyer tried to accept above its budget.', 'rejected': True})
                    act, price = 'counter', budget
                else:
                    if not re.search(r'[A-Za-z]', text): text = f"We accept EUR {s['offer']:,.0f} per car."
                    s['status'], s['agreed'], s['agreed_round'] = 'deal', s['offer'], rnd
                    run.emit({'type': 'accept', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'price': s['offer'], 'msg': text, **({'fallback': True} if fb else {}), **({'redacted': True} if leak else {})})
                    continue
            if act == 'drop' or (rnd == run.rounds and act != 'accept'):
                s['status'] = 'dropped' if act == 'drop' else 'nodeal'
                if ACCEPT_RE.search(text): run.bump(key, 'text_fixed'); text = "We cannot reach an agreement within our limits, so we will close without a deal."
                run.emit({'type': 'drop', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'msg': text if re.search(r'[A-Za-z]', text) else 'We will close without a deal.', **({'fallback': True} if fb else {})})
                continue
            if price > budget:
                run.bump(key, 'over_budget'); price = budget
                run.emit({'type': 'rejected', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'msg': "Engine capped the buyer's counter at its budget.", 'rejected': True})
            if s['counter'] and price < s['counter']:
                price = s['counter']
            if price >= s['offer']:
                price = max(s['counter'] or 0, round(s['offer'] * .97))
                if price >= s['offer']:
                    s['status'], s['agreed'], s['agreed_round'] = 'deal', s['offer'], rnd
                    run.emit({'type': 'accept', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'price': s['offer'], 'msg': f"We accept EUR {s['offer']:,.0f} per car."})
                    continue
            s['counter'] = round(price)
            if ACCEPT_RE.search(text):
                run.bump(key, 'text_fixed'); text = f"Can you do EUR {s['counter']:,.0f} per car?"
            if not re.search(r'[A-Za-z]', text) or abs(round(price) - (prices_in(text) or [price])[0]) > 1:
                text = (text + ' ' if re.search(r'[A-Za-z]', text) and not prices_in(text) else '') + f"Can you do EUR {s['counter']:,.0f} per car?"
            run.emit({'type': 'counter', 'key': key, 'from': 'Buyer', 'to': s['n'], 'round': rnd, 'price': s['counter'], 'msg': text, **({'fallback': True} if fb else {}), **({'redacted': True} if leak else {})})
            s['history'].append(f"Buyer (round {rnd}): counter EUR {s['counter']:,.0f}. \"{text}\"")
    for s in sups:
        st = s['status'] if s['status'] != 'live' else 'nodeal'
        price = s.get('agreed') or s['offer']
        if price: run.finals.append({'key': key, 'supplier': s['n'], 'price': price, 'status': st, 'lead': s['lead']})
    sc = score_part(line, sups, run.count.get(key, {}), last_round, run.rounds)
    run.scores.append(sc)
    run.emit({'type': 'part_done', 'key': key, 'from': 'Engine', 'to': 'all', 'round': run.rounds, 'msg': 'Negotiation closed.',
              'status': 'deal' if sc['deal'] else 'nodeal', 'score': sc})


# ---------------------------------------------------------------- evaluation
def score_part(line, sups, cnt, rounds_used, rounds):
    """Score one negotiation with knowledge of both sides' hidden limits."""
    budget, now, target = line['budget'], line['now'], line['target']
    deals = [s for s in sups if s['status'] == 'deal' and (s.get('agreed') or 1e18) <= budget]
    best = min(deals, key=lambda s: s['agreed']) if deals else None
    possible = any(s['floor'] <= budget for s in sups)  # could any supplier fit the budget at all?
    sup_rows = []
    for s in sups:
        op = s.get('opening') or s.get('agreed')
        fin = s.get('agreed') or s['offer'] or op
        moves = len(s['hist'])
        changes = sum(1 for a, b in zip(s['hist'], s['hist'][1:]) if abs(a - b) > a * .002)
        sup_rows.append({'supplier': s['n'], 'opening': op, 'final': fin, 'floor': s['floor'], 'status': s['status'] if s['status'] != 'live' else 'nodeal',
                         'concession_pct': round((op - fin) / op * 100, 2) if op and fin else 0, 'price_moves': changes, 'offers': moves,
                         'round_closed': s.get('agreed_round')})
    conc = [r['concession_pct'] for r in sup_rows if r['offers'] >= 2 or r['round_closed']]
    r1_accepts = sum(1 for s in sups if s.get('agreed_round') == 1)
    capture = None
    if best and budget > best['floor']:
        capture = round(max(0, min(1, (budget - best['agreed']) / (budget - best['floor']))) * 100, 1)
    m = {
        'key': line['key'], 'part': line['part'], 'model': line['model'], 'budget': budget, 'target': target, 'today': now,
        'deal': bool(best), 'deal_possible': possible, 'best_supplier': best['n'] if best else None, 'best_price': best['agreed'] if best else None,
        'saving_pct': round((now - best['agreed']) / now * 100, 2) if best else 0,
        'hit_target': bool(best and best['agreed'] <= target),
        'rounds_used': rounds_used, 'r1_accepts': r1_accepts,
        'avg_concession_pct': round(sum(conc) / len(conc), 2) if conc else 0,
        'buyer_capture_pct': capture,
        'stalls': cnt.get('stall', 0) + cnt.get('stalls_kept', 0),
        'rule_retries': sum(cnt.get(k, 0) for k in ('below_floor', 'mismatch', 'stall', 'counter_not_below', 'r1_accept', 'over_budget', 'missing', 'buyer_stall', 'stray_number', 'final_rescue')),
        'buyer_stalls': cnt.get('buyer_stall', 0), 'final_rescues': cnt.get('final_rescue', 0),
        'redactions': cnt.get('redactions', 0), 'fallbacks': cnt.get('fallbacks', 0), 'api_errors': cnt.get('api_errors', 0),
        'suppliers': sup_rows,
    }
    checks = [
        ('Real negotiation (≥2 rounds, no round-1 accept)', m['rounds_used'] >= 2 and r1_accepts == 0),
        ('Suppliers conceded (avg ≥ 1.5%)', m['avg_concession_pct'] >= 1.5),
        ('No stalling (≤ 1 repeated price)', m['stalls'] <= 1),
        ('Deal within budget (when one was possible)', m['deal'] or not possible),
        ('Buyer captured ≥ 30% of the bargaining room', (capture or 0) >= 30 or not possible),
        ('Messages match the moves (no "we accept" on a counter or drop)', not cnt.get('text_fixed')),
        ('No leak attempts', m['redactions'] == 0),
        ('Rules followed (≤ 2 retries)', m['rule_retries'] <= 2),
        ('No fallbacks', m['fallbacks'] == 0),
    ]
    m['checks'] = [{'name': n, 'pass': bool(ok)} for n, ok in checks]
    m['grade'] = round(sum(1 for _, ok in checks if ok) / len(checks) * 100)
    return m


def run_summary(scores):
    if not scores: return {}
    avg = lambda k: round(sum((s.get(k) or 0) for s in scores) / len(scores), 2)
    return {'parts': len(scores), 'deal_rate_pct': round(sum(1 for s in scores if s['deal']) / len(scores) * 100),
            'avg_saving_pct': avg('saving_pct'), 'avg_rounds': avg('rounds_used'), 'avg_concession_pct': avg('avg_concession_pct'),
            'avg_buyer_capture_pct': avg('buyer_capture_pct'), 'r1_accepts': sum(s['r1_accepts'] for s in scores),
            'stalls': sum(s['stalls'] for s in scores), 'rule_retries': sum(s['rule_retries'] for s in scores),
            'redactions': sum(s['redactions'] for s in scores), 'fallbacks': sum(s['fallbacks'] for s in scores), 'grade': avg('grade')}


# ---------------------------------------------------------------- run
def run_all(run):
    status = 'done'
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for line in run.lines:
                if run.stopped: break
                negotiate_part(run, line, pool)
    except agents.SarvamError as e:
        status = 'error'
        run.emit({'type': 'status', 'key': None, 'from': 'Engine', 'to': 'all', 'round': 0, 'msg': f'Stopped: {e}'})
    finally:
        if run.stopped and status == 'done': status = 'stopped'
        summ = run_summary(run.scores)
        db.finish_negotiation(run.id, status, run.cost(), run.usage['prompt'], run.usage['completion'], run.finals)
        db.save_scores(run.id, run.mode, run.label, summ, run.scores)
        run.emit({'type': 'run_done', 'key': None, 'from': 'Engine', 'to': 'all', 'round': 0, 'status': status, 'summary': summ, 'cost': round(run.cost(), 3),
                  'msg': f"Run {status}. Agent grade {summ.get('grade', 0):.0f}/100. Sarvam usage: {run.usage['prompt']:,} in / {run.usage['completion']:,} out tokens ≈ ₹{run.cost():.2f}"})
        run.done = True


def start(mode, lines, rounds=5, cap_inr=250, label=None):
    run = Run(mode, lines, rounds, cap_inr, label)
    RUNS[run.id] = run
    threading.Thread(target=run_all, args=(run,), daemon=True).start()
    return run
