"""RFQ bots: a buyer bot writes the Request for Quotation for each part, every invited supplier bot writes a quote.

Same pattern as the negotiators: the bots write and judge, code checks every reply against hard rules.
A broken rule is sent back to the bot once with the reason; if it is broken again, code builds a safe reply (a "fallback").

RFQ rules (buyer bot)            Quote rules (supplier bot)
- every section filled           - opening price 97-112 % of the supplier's own list quote
- no prices or budgets in it     - the price in the cover note matches the price field
- 1-3 questions to suppliers     - capacity never above what the plant can really make
- weights add to 100 and the     - lead time within -5 / +10 days of its standard lead time
  part's goal weighs the most    - one answer per buyer question
- reply due in 7-21 days         - a decline must say why
"""
import random, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
import agents, db
from engine import prices_in

RUNS = {}
PROMPT_VERSION = 'rfq-v1'
GOALS = {'cost': 'lower cost', 'esg': 'better ESG / lower CO2', 'lead': 'shorter lead time and reliable delivery', 'risk': 'lower supply risk'}
WEIGHT_KEY = {'cost': 'price', 'esg': 'esg', 'lead': 'lead', 'risk': 'risk'}

RFQ_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'scope', 'quality', 'delivery', 'commercial', 'esg', 'weights', 'due_days', 'questions'],
    'properties': {
        'summary': {'type': 'string', 'description': '2-3 sentences: what Zeno is buying and why'},
        'scope': {'type': 'array', 'items': {'type': 'string'}, 'description': '3-5 bullet points: part, spec, volume, plant, start'},
        'quality': {'type': 'string'}, 'delivery': {'type': 'string'}, 'commercial': {'type': 'string'}, 'esg': {'type': 'string'},
        'weights': {'type': 'object', 'additionalProperties': False, 'required': ['price', 'esg', 'lead', 'risk'],
                    'properties': {k: {'type': 'integer'} for k in ('price', 'esg', 'lead', 'risk')}},
        'due_days': {'type': 'integer'},
        'questions': {'type': 'array', 'items': {'type': 'string'}, 'description': '1-3 questions every supplier must answer'},
    },
}
QUOTE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['decision', 'price', 'lead_days', 'capacity_per_week', 'esg_commitment', 'validity_days', 'conditions', 'answers', 'message'],
    'properties': {
        'decision': {'type': 'string', 'enum': ['quote', 'decline']},
        'price': {'type': 'number', 'description': 'EUR per car for the whole part line (0 if declining)'},
        'lead_days': {'type': 'integer'}, 'capacity_per_week': {'type': 'integer', 'description': 'sets per week you can deliver'},
        'esg_commitment': {'type': ['string', 'null']}, 'validity_days': {'type': 'integer'},
        'conditions': {'type': 'array', 'items': {'type': 'string'}, 'description': '0-3 conditions attached to the quote'},
        'answers': {'type': 'array', 'items': {'type': 'string'}, 'description': 'one answer per buyer question, same order'},
        'message': {'type': 'string', 'description': 'cover note to the buyer, max 50 words'},
    },
}


# ---------------------------------------------------------------- prompts
def buyer_rfq_system(line):
    return (
        f"You are Zeno Group's senior buyer. Write a Request for Quotation (RFQ) that will be sent to several suppliers.\n"
        f"Part: {line['part']} ({line['spec']}) for the {line['model']}. Volume: {line['vol']:,} cars per year "
        f"(about {line['need_week']:,} sets per week). Delivery to {line['plant']}. Supply should start in about {line['start_days']} days. "
        f"Contract: {line['contract_years']} years.\n"
        f"Main goal for this part: {GOALS.get(line['goal'], 'best overall value')}.\n\n"
        f"PRIVATE (never write it in the RFQ): Zeno pays EUR {line['now']:,} per car today to {line['incumbent']}. "
        f"An RFQ never contains prices, budgets, targets or the name of the current supplier.\n\n"
        f"RULES:\n"
        f"1. Fill every section in plain, short business English: summary (max 40 words), scope (3-5 bullets, max 15 words each), "
        f"quality, delivery, commercial and ESG (max 35 words each).\n"
        f"2. Quality: name the certification (IATF 16949) and a PPM defect target. Delivery: Incoterm, JIT or weekly call-offs, the plant.\n"
        f"3. Commercial: payment terms, contract length, yearly price-down expectation as a percent is allowed, but no euro amounts.\n"
        f"4. Evaluation weights (price, esg, lead, risk) must add up to 100 and the main goal must have the highest weight "
        f"(its key is '{WEIGHT_KEY.get(line['goal'], 'price')}'), e.g. 40 for the goal and 20 for each of the others.\n"
        f"5. Reply due in 7 to 21 days. Ask 1 to 3 short questions (max 15 words each) every supplier must answer (capacity, ramp-up, tooling, CO2...).\n"
        f"OUTPUT: only the JSON object.")


def buyer_rfq_user(line):
    return f"Write the RFQ for {line['part']} now."


def supplier_quote_system(line, s, rfq):
    qs = '\n'.join(f"  Q{i + 1}. {q}" for i, q in enumerate(rfq['questions']))
    return (
        f"You are the key-account sales manager of {s['n']}, a supplier of {line['category']} parts. "
        f"Zeno Group sent you this RFQ. Write your quote.\n\n"
        f"RFQ {rfq['ref']}: {rfq['summary']}\nScope: " + '; '.join(rfq['scope']) + "\n"
        f"Quality: {rfq['quality']}\nDelivery: {rfq['delivery']}\nCommercial: {rfq['commercial']}\nESG: {rfq['esg']}\n"
        f"They will score quotes on: price {rfq['weights']['price']}%, ESG {rfq['weights']['esg']}%, lead time {rfq['weights']['lead']}%, risk {rfq['weights']['risk']}%.\n"
        f"Questions you must answer:\n{qs}\n\n"
        f"YOUR PRIVATE FACTS (never reveal them):\n"
        f"- Your list price for this part: EUR {s['quote']:,} per car.\n"
        f"- Your plant can really make at most {s['cap']:,} sets per week. Zeno needs about {line['need_week']:,}, so you can cover "
        f"{min(100, round(s['cap'] / line['need_week'] * 100))}% of their need{' (all of it)' if s['cap'] >= line['need_week'] else ''}.\n"
        f"- Standard lead time {s['lead']} days, ESG score {s['esg']}, risk {s['risk']}, relationship with Zeno: {s['rel']}.\n"
        + ("- You have never supplied Zeno. A first contract matters to you; offer a trial batch or warranty to lower their risk.\n"
           if s.get('rel_score') is None else "- You already supply Zeno, they know your quality.\n") +
        f"\nRULES:\n"
        f"1. Your quoted price MUST be between EUR {round(s['quote'] * .97):,} and EUR {round(s['quote'] * 1.12):,} per car "
        f"(you keep room to negotiate later).\n"
        f"2. Never promise more capacity than your plant can make. If you can cover at least half of their need you MUST quote "
        f"(decision \"quote\": offer your real capacity and, if it is below their need, a ramp-up plan). Decline ONLY if you can "
        f"cover less than half of it (decision \"decline\", price 0, and say why).\n"
        f"3. Lead time MUST be between {max(1, s['lead'] - 5)} and {s['lead'] + 10} days.\n"
        f"4. Answer every question, in order, one short sentence each (max 25 words).\n"
        f"5. Quote validity 30 to 90 days. 0-3 conditions (e.g. volume commitment, raw-material index, tooling paid by Zeno).\n"
        f"OUTPUT: only the JSON object. If you write a price in the message, it must equal the price field. Message max 50 words.")


def supplier_quote_user():
    return 'Write your quote for this RFQ now.'


# ---------------------------------------------------------------- mock bots (free, same JSON)
def mock_rfq(line):
    time.sleep(random.uniform(.3, .7))
    w = {'price': 25, 'esg': 25, 'lead': 25, 'risk': 25}; w[WEIGHT_KEY.get(line['goal'], 'price')] = 40
    rest = [k for k in w if k != WEIGHT_KEY.get(line['goal'], 'price')]; w[rest[0]] = 20; w[rest[1]] = 20; w[rest[2]] = 20
    return {'summary': f"Zeno Group invites quotations for the {line['part']} of the {line['model']}, focused on {GOALS.get(line['goal'], 'value')}.",
            'scope': [f"{line['part']}: {line['spec']}", f"{line['vol']:,} sets per year", f"Delivery to {line['plant']}", f"Start of production in {line['start_days']} days"],
            'quality': 'IATF 16949 certified, below 50 PPM defects, PPAP level 3 before first delivery.',
            'delivery': f"DAP {line['plant']}, weekly call-offs, 98% on-time delivery.",
            'commercial': f"{line['contract_years']}-year contract, payment 60 days, 2% yearly price-down.",
            'esg': 'Share product carbon footprint; renewable electricity roadmap.',
            'weights': w, 'due_days': 14, 'questions': ['What weekly capacity can you commit?', 'How long is your ramp-up?']}


def mock_quote(line, s, rfq):
    time.sleep(random.uniform(.3, .8))
    if s['cap'] < line['need_week'] * .5:
        return {'decision': 'decline', 'price': 0, 'lead_days': s['lead'], 'capacity_per_week': s['cap'], 'esg_commitment': None, 'validity_days': 30,
                'conditions': [], 'answers': ['Not enough capacity.'] * len(rfq['questions']), 'message': 'We cannot cover your weekly volume this year, so we decline.'}
    p = round(s['quote'] * random.uniform(1.0, 1.08))
    return {'decision': 'quote', 'price': p, 'lead_days': s['lead'], 'capacity_per_week': min(s['cap'], line['need_week']), 'esg_commitment': '30% recycled content by 2027',
            'validity_days': 60, 'conditions': ['Raw-material price index'], 'answers': [f"{min(s['cap'], line['need_week']):,} sets per week.", '8 weeks.'][:len(rfq['questions'])] + ['Yes.'] * max(0, len(rfq['questions']) - 2),
            'message': f"Thank you for the RFQ. Our quote is EUR {p:,} per car."}


# ---------------------------------------------------------------- checks
def check_rfq(line, d):
    errs = []
    for k in ('summary', 'quality', 'delivery', 'commercial', 'esg'):
        if not str(d.get(k) or '').strip(): errs.append(f'section "{k}" is empty')
    if not (3 <= len(d.get('scope') or []) <= 6): errs.append('scope must have 3-5 bullet points')
    if not (1 <= len(d.get('questions') or []) <= 3): errs.append('ask 1-3 questions')
    text = ' '.join([d.get('summary', ''), d.get('quality', ''), d.get('delivery', ''), d.get('commercial', ''), d.get('esg', '')] + list(d.get('scope') or []))
    if prices_in(text) or 'EUR' in text or '€' in text: errs.append('an RFQ must not contain euro amounts or prices')
    if line['incumbent'].lower() in text.lower(): errs.append('do not name the current supplier')
    return errs


def fix_rfq(line, d, cnt):
    """Small structural problems are corrected in code (counted, not retried)."""
    w = d.get('weights') or {}
    w = {k: max(0, int(w.get(k) or 0)) for k in ('price', 'esg', 'lead', 'risk')}
    goal = WEIGHT_KEY.get(line['goal'], 'price')
    tot = sum(w.values())
    if tot != 100 or max(w, key=w.get) != goal:
        cnt['weights_fixed'] = cnt.get('weights_fixed', 0) + 1
        if tot <= 0: w = {'price': 25, 'esg': 25, 'lead': 25, 'risk': 25}; tot = 100
        w = {k: round(v * 100 / tot) for k, v in w.items()}
        if max(w, key=w.get) != goal or list(w.values()).count(w[goal]) > 1:
            top = max(w.values()); w[goal] = top + 5
        tot = sum(w.values()); w = {k: round(v * 100 / tot) for k, v in w.items()}
        w[goal] += 100 - sum(w.values())
    d['weights'] = w
    if not 7 <= int(d.get('due_days') or 0) <= 21:
        cnt['due_fixed'] = cnt.get('due_fixed', 0) + 1; d['due_days'] = min(21, max(7, int(d.get('due_days') or 14)))
    d['questions'] = (d.get('questions') or [])[:3]
    return d


def check_quote(line, s, q, nq):
    errs = []
    if q['decision'] == 'decline':
        if s['cap'] >= line['need_week'] * .5: errs.append('you can cover at least half of their need, so you must quote, not decline')
        if len((q.get('message') or '').split()) < 4: errs.append('a decline must explain why')
        return errs
    p = float(q.get('price') or 0)
    if not s['quote'] * .97 <= p <= s['quote'] * 1.12: errs.append(f"price must be between EUR {round(s['quote'] * .97):,} and EUR {round(s['quote'] * 1.12):,}")
    ps = prices_in(q.get('message', ''))
    if ps and all(abs(x - p) > 1 for x in ps): errs.append(f'the price in your message ({ps[0]:,.0f}) is not the price field ({p:,.0f})')
    if int(q.get('capacity_per_week') or 0) > s['cap']: errs.append(f"you promised more capacity than your plant can make")
    if not s['lead'] - 5 <= int(q.get('lead_days') or 0) <= s['lead'] + 10: errs.append(f"lead time must be between {max(1, s['lead'] - 5)} and {s['lead'] + 10} days")
    if len(q.get('answers') or []) < nq: errs.append(f'answer all {nq} questions')
    return errs


# ---------------------------------------------------------------- run
class RfqRun:
    def __init__(self, mode, lines, label=None):
        self.id = time.strftime('rfq-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.mode, self.lines, self.label = mode, lines, label or PROMPT_VERSION
        self.events, self.cond = [], threading.Condition()
        self.done = self.stopped = False
        self.usage = {'prompt': 0, 'completion': 0}
        self.scores = []
        db.new_rfq_run(self.id, mode, self.label, [{k: v for k, v in l.items() if k != 'suppliers'} | {'suppliers': [s['n'] for s in l['suppliers']]} for l in lines])

    def cost(self):
        return self.usage['prompt'] * agents.INR_IN + self.usage['completion'] * agents.INR_OUT

    def emit(self, e):
        e.setdefault('t', time.time())
        with self.cond:
            self.events.append(e); self.cond.notify_all()


def _call(run, system, user, name, schema, mock):
    if run.mode == 'mock':
        return mock()
    return agents.chat_json(system, user, name, schema, run.usage)


def write_rfq(run, line, cnt):
    key = line['key']
    run.emit({'type': 'status', 'key': key, 'from': 'Buyer bot', 'msg': 'Writing the RFQ…'})
    d, errs = None, []
    for attempt in range(2):
        sysmsg = buyer_rfq_system(line) + (f"\n\nYOUR LAST DRAFT WAS REJECTED: {'; '.join(errs)}. Fix it." if errs else '')
        try:
            d = _call(run, sysmsg, buyer_rfq_user(line), 'rfq', RFQ_SCHEMA, lambda: mock_rfq(line))
        except agents.SarvamError as e:
            if e.fatal: raise
            cnt['api_errors'] = cnt.get('api_errors', 0) + 1; d = None; errs = [str(e)]; continue
        errs = check_rfq(line, d)
        if not errs: break
        cnt['retries'] = cnt.get('retries', 0) + 1
        run.emit({'type': 'rejected', 'key': key, 'from': 'Rule check', 'msg': 'RFQ sent back: ' + '; '.join(errs)})
    if d is None or errs:
        cnt['fallbacks'] = cnt.get('fallbacks', 0) + 1; d = mock_rfq(line); d['fallback'] = True
    d = fix_rfq(line, d, cnt)
    d['ref'] = f"RFQ-{time.strftime('%y%m')}-{abs(hash(key)) % 9000 + 1000}"
    d['part'], d['model'], d['goal'] = line['part'], line['model'], line['goal']
    return d


def write_quote(run, line, s, rfq, cnt):
    key = line['key']
    q, errs = None, []
    for attempt in range(2):
        sysmsg = supplier_quote_system(line, s, rfq) + (f"\n\nYOUR LAST QUOTE WAS REJECTED: {'; '.join(errs)}. Fix it." if errs else '')
        try:
            q = _call(run, sysmsg, supplier_quote_user(), 'quote', QUOTE_SCHEMA, lambda: mock_quote(line, s, rfq))
        except agents.SarvamError as e:
            if e.fatal: raise
            cnt['api_errors'] = cnt.get('api_errors', 0) + 1; q = None; errs = [str(e)]; continue
        errs = check_quote(line, s, q, len(rfq['questions']))
        if not errs: break
        cnt['retries'] = cnt.get('retries', 0) + 1
        run.emit({'type': 'rejected', 'key': key, 'from': 'Rule check', 'to': s['n'], 'msg': f"Quote from {s['n']} sent back: " + '; '.join(errs)})
    if q is None or errs:
        cnt['fallbacks'] = cnt.get('fallbacks', 0) + 1; q = mock_quote(line, s, rfq); q['fallback'] = True
    if q['decision'] == 'quote':
        q['price'] = round(float(q['price']))
        q['capacity_per_week'] = min(int(q['capacity_per_week']), s['cap'])
        q['covers_need_pct'] = round(q['capacity_per_week'] / line['need_week'] * 100)
    q['supplier'] = s['n']
    q['answers'] = (q.get('answers') or [])[:len(rfq['questions'])]
    return q


def score_rfq(line, rfq, quotes, cnt):
    got = [q for q in quotes if q]
    caps = {s['n']: s['cap'] for s in line['suppliers']}
    quoted = [q for q in got if q['decision'] == 'quote']
    checks = [
        ('RFQ complete, no prices or supplier names in it', not rfq.get('fallback')),
        ('Evaluation weights fit the goal (no fix needed)', not cnt.get('weights_fixed')),
        ('Every invited supplier replied', len(got) == len(line['suppliers'])),
        ('Quotes kept the rules (≤ 1 retry)', cnt.get('retries', 0) <= 1),
        ('All buyer questions answered', all(len(q.get('answers') or []) == len(rfq['questions']) for q in got)),
        ('Declines only when capacity is really short', all(q['decision'] == 'quote' or caps.get(q['supplier'], 0) < line['need_week'] * .5 for q in got)),
        ('No fallbacks', not cnt.get('fallbacks')),
    ]
    best = min(quoted, key=lambda q: q['price']) if quoted else None
    return {'key': line['key'], 'part': line['part'], 'model': line['model'], 'quotes': len(quoted), 'declined': len(got) - len(quoted),
            'best_supplier': best['supplier'] if best else None, 'best_price': best['price'] if best else None, 'today': line['now'],
            'retries': cnt.get('retries', 0), 'fallbacks': cnt.get('fallbacks', 0), 'fixes': cnt.get('weights_fixed', 0) + cnt.get('due_fixed', 0),
            'checks': [{'name': n, 'pass': bool(ok)} for n, ok in checks], 'grade': round(sum(1 for _, ok in checks if ok) / len(checks) * 100)}


def rfq_part(run, line, pool):
    cnt, key = {}, line['key']
    line.setdefault('need_week', round(line['vol'] / 48)); line.setdefault('start_days', 120); line.setdefault('contract_years', 3)
    for s in line['suppliers']:  # private plant capacity: some suppliers cannot cover the whole need
        s.setdefault('cap', int(line['need_week'] * (random.uniform(.3, .48) if random.random() < .12 else random.uniform(.6, 1.6))))
    rfq = write_rfq(run, line, cnt)
    db.add_rfq_doc(run.id, key, rfq)
    run.emit({'type': 'rfq_doc', 'key': key, 'from': 'Buyer bot', 'to': 'all', 'doc': rfq,
              'msg': f"{rfq['ref']} sent to {len(line['suppliers'])} suppliers · reply in {rfq['due_days']} days"})
    futs = {s['n']: pool.submit(write_quote, run, line, s, rfq, cnt) for s in line['suppliers']}
    quotes = []
    for s in line['suppliers']:
        if run.stopped: break
        try:
            q = futs[s['n']].result()
        except agents.SarvamError:
            raise
        quotes.append(q); db.add_rfq_quote(run.id, key, s['n'], q)
        run.emit({'type': 'quote' if q['decision'] == 'quote' else 'decline', 'key': key, 'from': s['n'], 'to': 'Buyer bot', 'quote': q,
                  'price': q['price'] or None, 'msg': q['message']})
    sc = score_rfq(line, rfq, quotes, cnt)
    run.scores.append(sc)
    run.emit({'type': 'part_done', 'key': key, 'from': 'Engine', 'score': sc, 'msg': f"{sc['quotes']} quotes, {sc['declined']} declined."})


def run_all(run):
    status = 'done'
    try:
        with ThreadPoolExecutor(max_workers=6) as qpool, ThreadPoolExecutor(max_workers=max(1, len(run.lines))) as ppool:
            parts = [ppool.submit(rfq_part, run, l, qpool) for l in run.lines]
            for f in parts: f.result()
    except agents.SarvamError as e:
        status = 'error'; run.emit({'type': 'status', 'key': None, 'from': 'Engine', 'msg': f'Stopped: {e}'})
    except Exception as e:  # never leave the browser waiting
        status = 'error'; run.emit({'type': 'status', 'key': None, 'from': 'Engine', 'msg': f'Error: {e}'})
    finally:
        sc = run.scores
        summ = {'parts': len(sc), 'quotes': sum(s['quotes'] for s in sc), 'declined': sum(s['declined'] for s in sc),
                'retries': sum(s['retries'] for s in sc), 'fallbacks': sum(s['fallbacks'] for s in sc),
                'grade': round(sum(s['grade'] for s in sc) / len(sc)) if sc else 0}
        db.finish_rfq_run(run.id, status, run.cost(), summ, sc)
        run.emit({'type': 'run_done', 'key': None, 'from': 'Engine', 'status': status, 'summary': summ, 'cost': round(run.cost(), 3),
                  'msg': f"RFQs done: {summ['quotes']} quotes, {summ['declined']} declined. Bot grade {summ['grade']}/100 · ≈ ₹{run.cost():.2f}"})
        run.done = True


def start(mode, lines, label=None):
    run = RfqRun(mode, lines, label)
    RUNS[run.id] = run
    threading.Thread(target=run_all, args=(run,), daemon=True).start()
    return run
