"""PO -> Pay bots (step 7 of the Source-to-Pay flow).

For every awarded part the run goes:  PO created -> supplier confirms -> shipped -> goods receipt -> invoice -> 3-way match -> paid / on hold.
Code decides the facts (quantities, prices, which orders have a problem) and does the 3-way match itself.
Bots write: the supplier bot writes its order confirmation and invoice note; the AP (accounts payable) bot explains a failed
match in plain words and recommends one of the allowed fixes. Rules checked in code: the recommended fix must be one of the
allowed options, and the explanation may only use numbers that are in the documents (no invented numbers).
"""
import random, threading, time, uuid
import agents, db
from evaluator import invented

RUNS = {}
PROMPT_VERSION = 'p2p-v1'

OPTIONS = {
    'short': ['Pay the received quantity only', 'Ask the supplier for a credit note', 'Hold payment until the rest arrives'],
    'price': ['Ask for a corrected invoice at the PO price', 'Pay the PO price and short-pay the difference', 'Accept the higher price (needs approval)'],
    'quality': ['Pay accepted units and return the rejects', 'Ask for a credit note for the rejects', 'Hold payment until replacements arrive'],
    'duplicate': ['Reject the duplicate invoice', 'Pay it again (not recommended)'],
    'early': ['Hold the invoice until the goods are received', 'Reject the invoice'],
}
WHAT = {'short': 'short delivery', 'price': 'price mismatch', 'quality': 'quality rejection', 'duplicate': 'duplicate invoice', 'early': 'invoice before goods receipt'}

SUP_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['confirmation', 'invoice_note'],
              'properties': {'confirmation': {'type': 'string'}, 'invoice_note': {'type': 'string'}}}
AP_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['explanation', 'recommended', 'why'],
             'properties': {'explanation': {'type': 'string'}, 'recommended': {'type': 'string'}, 'why': {'type': 'string'}}}


def facts(o):
    return (f"PO {o['po']}: {o['qty']:,} sets of {o['part']} for the {o['model']} at EUR {o['price']:,} each = EUR {o['po_value']:,}. "
            f"Shipped {o['shipped']:,}. Goods receipt: {o['received']:,} received, {o['accepted']:,} accepted. "
            f"Invoice {o['inv_no']}: {o['inv_qty']:,} sets at EUR {o['inv_price']:,} = EUR {o['inv_value']:,}.")


def supplier_system(o):
    return (f"You are the order desk of {o['supplier']}. Zeno Group sent you purchase order {o['po']} (write it exactly like that, without adding 'PO' in front) for {o['qty']:,} sets of "
            f"{o['part']} at EUR {o['price']:,} per set, payment 60 days. Write two short business messages (max 30 words each): "
            f"'confirmation' confirms the order and delivery in {o['lead']} days; 'invoice_note' is the cover note of your invoice {o['inv_no']} "
            f"for {o['inv_qty']:,} sets at EUR {o['inv_price']:,} per set. Use only these numbers. Output only the JSON object.")


def ap_system(o):
    return ("You are Zeno Group's accounts payable bot. A 3-way match (purchase order vs goods receipt vs invoice) failed. "
            "Explain to a manager in plain words what went wrong (max 45 words), using only numbers from the documents. "
            "Then recommend exactly one fix from the allowed list (copy it exactly) and say why in one sentence (max 25 words).\n\n"
            f"Documents: {facts(o)}\nProblem found by the code: {WHAT[o['issue']]}"
            + (f" (invoice {o['inv_no']} was already paid on an earlier PO)" if o['issue'] == 'duplicate' else '')
            + (" (the invoice arrived before any goods were received)" if o['issue'] == 'early' else '')
            + "\nAllowed fixes:\n" + '\n'.join(f"- {x}" for x in OPTIONS[o['issue']]) + "\nOutput only the JSON object.")


def mock_supplier(o):
    return {'confirmation': f"We confirm {o['po']} for {o['qty']:,} sets, delivery in {o['lead']} days.",
            'invoice_note': f"Please find invoice {o['inv_no']} for {o['inv_qty']:,} sets at EUR {o['inv_price']:,}."}


def mock_ap(o):
    t = o['issue']
    exp = {'short': f"We were invoiced for {o['inv_qty']:,} sets but only {o['received']:,} arrived.",
           'price': f"The invoice charges EUR {o['inv_price']:,} per set, but the PO price is EUR {o['price']:,}.",
           'quality': f"{o['received'] - o['accepted']:,} of {o['received']:,} sets failed inspection, but all {o['inv_qty']:,} were invoiced.",
           'duplicate': f"Invoice {o['inv_no']} has already been paid once.",
           'early': "The invoice arrived before the goods were received, so nothing can be matched yet."}[t]
    return {'explanation': exp, 'recommended': OPTIONS[t][0], 'why': 'It pays only for what we actually received at the agreed price.'}


def known(o):
    ks = {o['qty'], o['price'], o['po_value'], o['shipped'], o['received'], o['accepted'], o['inv_qty'], o['inv_price'], o['inv_value'], o['lead']}
    ks |= {abs(o['inv_qty'] - o['received']), abs(o['inv_qty'] - o['accepted']), abs(o['inv_price'] - o['price']), abs(o['inv_value'] - o['po_value']),
           abs(o['inv_price'] - o['price']) * o['inv_qty'], o['received'] - o['accepted'], abs(o['inv_value'] - o['accepted'] * o['price']), o['accepted'] * o['price'], o['received'] * o['price']}
    return {k for k in ks if k}


class P2PRun:
    def __init__(self, mode, orders):
        self.id = time.strftime('p2p-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.mode, self.orders = mode, orders
        self.events, self.cond = [], threading.Condition()
        self.done = self.stopped = False
        self.usage = {'prompt': 0, 'completion': 0}
        self.scores = []
        db.new_rfq_run(self.id, mode, PROMPT_VERSION, [{'po': o['po'], 'part': o['part'], 'issue': o.get('issue')} for o in orders])

    def cost(self):
        return self.usage['prompt'] * agents.INR_IN + self.usage['completion'] * agents.INR_OUT

    def emit(self, e):
        e.setdefault('t', time.time())
        with self.cond:
            self.events.append(e); self.cond.notify_all()


def _llm(run, system, schema, name, mock, effort='low'):
    if run.mode == 'mock':
        time.sleep(random.uniform(.2, .5)); return mock()
    return agents.chat_json(system, 'Write it now.', name, schema, run.usage, max_tokens=2000, effort=effort)


def run_order(run, o):
    k, cnt = o['po'], {}
    step = lambda stage, who, msg, **x: (run.emit({'type': 'stage', 'key': k, 'stage': stage, 'from': who, 'msg': msg, **x}), time.sleep(random.uniform(.5, .9)))
    step('po', 'Procurement bot', f"{o['po']} sent to {o['supplier']}: {o['qty']:,} sets × EUR {o['price']:,} = EUR {o['po_value']:,}, payment 60 days.")
    try:
        s = _llm(run, supplier_system(o), SUP_SCHEMA, 'supplier_docs', lambda: mock_supplier(o))
    except agents.SarvamError as e:
        if e.fatal: raise
        cnt['fallbacks'] = 1; s = mock_supplier(o)
    txt = s.get('confirmation', '') + ' ' + s.get('invoice_note', '')
    for ident in (o['po'], o['inv_no'], o['asn']): txt = txt.replace(ident, '')  # document numbers are not prices
    if invented(txt, known(o)):
        cnt['invented'] = 1; s = mock_supplier(o)
    step('confirm', o['supplier'], s['confirmation'] or mock_supplier(o)['confirmation'])
    if o['issue'] == 'early':
        step('invoice', o['supplier'], s['invoice_note'] or mock_supplier(o)['invoice_note'], early=True)
    step('ship', o['supplier'], f"Shipped {o['shipped']:,} sets (ASN {o['asn']}).")
    if o['issue'] != 'early':
        step('gr', 'Warehouse bot', f"Goods receipt: {o['received']:,} received, {o['accepted']:,} accepted" + (f", {o['received'] - o['accepted']:,} rejected at inspection." if o['received'] > o['accepted'] else '.'))
        step('invoice', o['supplier'], s['invoice_note'] or mock_supplier(o)['invoice_note'])
    if o['issue'] == 'duplicate':
        step('invoice', o['supplier'], f"Invoice {o['inv_no']} received again.", duplicate=True)
    if not o['issue']:
        step('match', 'AP bot', f"3-way match OK: PO, goods receipt and invoice agree ({o['accepted']:,} sets × EUR {o['price']:,}).", ok=True)
        run.emit({'type': 'stage', 'key': k, 'stage': 'paid', 'from': 'AP bot', 'msg': f"Payment of EUR {o['inv_value']:,} scheduled for day 60.", 'amount': o['inv_value']})
        sc = {'po': k, 'issue': None, 'pass': True, 'checks': [{'name': 'Clean match paid in full', 'pass': True}]}
    else:
        ap, errs = None, []
        for attempt in range(3):
            try:
                ap = _llm(run, ap_system(o) + (f"\nYOUR LAST ANSWER WAS REJECTED: {'; '.join(errs)}." if errs else ''), AP_SCHEMA, 'ap_explanation', lambda: mock_ap(o), effort='medium')
            except agents.SarvamError as e:
                if e.fatal: raise
                ap = None; errs = [str(e)]; continue
            if len((ap.get('explanation') or '').replace('...', ' ').split()) < 5:
                cnt['hiccups'] = cnt.get('hiccups', 0) + 1; errs = ['your explanation was empty']; continue
            errs = []
            rec = next((x for x in OPTIONS[o['issue']] if x.lower() == (ap.get('recommended') or '').strip().lower().rstrip('.')), None)
            if not rec: errs.append('recommended must be copied exactly from the allowed fixes')
            else: ap['recommended'] = rec
            txt = ap.get('explanation', '') + ' ' + ap.get('why', '')
            for ident in (o['po'], o['inv_no'], o['asn']): txt = txt.replace(ident, '')  # document numbers are not prices
            bad = invented(txt, known(o))
            if bad: errs.append(f"these numbers are not in the documents: {', '.join(bad[:3])}")
            if not errs: break
            cnt['retries'] = cnt.get('retries', 0) + 1
            run.emit({'type': 'rejected', 'key': k, 'from': 'Rule check', 'msg': 'AP bot sent back: ' + '; '.join(errs)})
        if ap is None or errs:
            cnt['fallbacks'] = cnt.get('fallbacks', 0) + 1; ap = mock_ap(o); ap['fallback'] = True
        run.emit({'type': 'stage', 'key': k, 'stage': 'match', 'from': 'AP bot', 'ok': False, 'issue': o['issue'], 'msg': ap['explanation'],
                  'recommended': ap['recommended'], 'why': ap['why'], 'options': OPTIONS[o['issue']], 'fallback': ap.get('fallback', False)})
        checks = [('AP bot found and explained the problem', not ap.get('fallback')), ('Recommended fix is an allowed option', ap['recommended'] in OPTIONS[o['issue']]),
                  ('No invented numbers', not cnt.get('invented') and not any('not in the documents' in e for e in errs)), ('Rules followed (≤ 1 retry)', cnt.get('retries', 0) <= 1)]
        sc = {'po': k, 'issue': o['issue'], 'pass': all(c for _, c in checks), 'checks': [{'name': n, 'pass': bool(c)} for n, c in checks], 'hiccups': cnt.get('hiccups', 0)}
    run.scores.append(sc)
    run.emit({'type': 'part_done', 'key': k, 'score': sc})


def run_all(run):
    status = 'done'
    try:
        threads = [threading.Thread(target=run_order, args=(run, o), daemon=True) for o in run.orders]
        for i, t in enumerate(threads): t.start(); time.sleep(.3)
        for t in threads: t.join()
    except agents.SarvamError as e:
        status = 'error'; run.emit({'type': 'status', 'key': None, 'msg': f'Stopped: {e}'})
    finally:
        sc = run.scores
        summ = {'orders': len(run.orders), 'issues': sum(1 for s in sc if s['issue']), 'grade': round(sum(1 for s in sc if s['pass']) / len(sc) * 100) if sc else 0}
        db.finish_rfq_run(run.id, status, run.cost(), summ, sc)
        run.emit({'type': 'run_done', 'key': None, 'status': status, 'summary': summ, 'cost': round(run.cost(), 3),
                  'msg': f"PO → Pay done: {summ['orders']} orders, {summ['issues']} need your decision · ≈ ₹{run.cost():.2f}"})
        run.done = True


def start(mode, orders):
    run = P2PRun(mode, orders)
    RUNS[run.id] = run
    threading.Thread(target=run_all, args=(run,), daemon=True).start()
    return run
