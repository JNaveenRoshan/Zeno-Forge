"""SQLite storage for Zeno Forge: budgets, criteria, suppliers, bids, negotiations and their moves."""
import json, sqlite3, threading, time
from pathlib import Path

DB_PATH = Path(__file__).with_name('zeno.db')
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS budgets      (model TEXT, part INTEGER, value REAL, updated REAL, PRIMARY KEY (model, part));
CREATE TABLE IF NOT EXISTS criteria     (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS suppliers    (name TEXT PRIMARY KEY, category TEXT, data TEXT, price_factor REAL, joined REAL);
CREATE TABLE IF NOT EXISTS relationships(name TEXT PRIMARY KEY, score INTEGER, note TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS bid_lines    (key TEXT PRIMARY KEY, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS selections   (model TEXT, part INTEGER, supplier TEXT, price REAL, chosen REAL, PRIMARY KEY (model, part));
CREATE TABLE IF NOT EXISTS negotiations (id TEXT PRIMARY KEY, mode TEXT, started REAL, ended REAL, status TEXT, cost_inr REAL, prompt_tokens INTEGER, completion_tokens INTEGER, setup TEXT);
CREATE TABLE IF NOT EXISTS moves        (id INTEGER PRIMARY KEY AUTOINCREMENT, negotiation TEXT, part_key TEXT, round INTEGER, sender TEXT, receiver TEXT,
                                         type TEXT, price REAL, lead INTEGER, esg TEXT, message TEXT, flags TEXT, t REAL);
CREATE TABLE IF NOT EXISTS final_offers (negotiation TEXT, part_key TEXT, supplier TEXT, price REAL, status TEXT, lead INTEGER, PRIMARY KEY (negotiation, part_key, supplier));
CREATE TABLE IF NOT EXISTS eval_runs    (id TEXT PRIMARY KEY, mode TEXT, label TEXT, started REAL, data TEXT);
CREATE TABLE IF NOT EXISTS eval_scores  (run TEXT, metric TEXT, value REAL, PRIMARY KEY (run, metric));
CREATE INDEX IF NOT EXISTS moves_neg ON moves (negotiation);
CREATE TABLE IF NOT EXISTS rfq_runs     (id TEXT PRIMARY KEY, mode TEXT, label TEXT, started REAL, ended REAL, status TEXT, cost_inr REAL, data TEXT);
CREATE TABLE IF NOT EXISTS rfq_docs     (run TEXT, part_key TEXT, doc TEXT, PRIMARY KEY (run, part_key));
CREATE TABLE IF NOT EXISTS ai_events    (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL, kind TEXT, title TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS lab_sets     (id TEXT PRIMARY KEY, bot TEXT, name TEXT, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS lab_results  (id TEXT PRIMARY KEY, bot TEXT, set_name TEXT, label TEXT, mode TEXT, started REAL, cost_inr REAL, data TEXT);
CREATE TABLE IF NOT EXISTS rfq_quotes   (run TEXT, part_key TEXT, supplier TEXT, quote TEXT, PRIMARY KEY (run, part_key, supplier));
"""

def connect():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

_conn = connect()
with _lock:
    _conn.executescript(SCHEMA)
    _conn.commit()

def q(sql, args=(), many=False):
    with _lock:
        cur = _conn.executemany(sql, args) if many else _conn.execute(sql, args)
        _conn.commit()
        return cur

def rows(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]

# ---- app state (what the page loads on start and saves as you work) ----
def load_state():
    crit = rows('SELECT data FROM criteria WHERE id = 1')
    return {
        'budgets': rows('SELECT model, part, value FROM budgets'),
        'criteria': json.loads(crit[0]['data']) if crit else None,
        'suppliers': [dict(json.loads(r['data']), cat=r['category'], factor=r['price_factor'], joined=r['joined']) for r in rows('SELECT * FROM suppliers ORDER BY joined')],
        'relationships': rows('SELECT name, score, note FROM relationships'),
        'bid_lines': [json.loads(r['data']) for r in rows('SELECT data FROM bid_lines ORDER BY updated')],
        'selections': rows('SELECT model, part, supplier, price FROM selections'),
    }

def save_state(s):
    now = time.time()
    if 'budgets' in s:
        q('DELETE FROM budgets')
        q('INSERT INTO budgets VALUES (?,?,?,?)', [(b['model'], int(b['part']), float(b['value']), now) for b in s['budgets']], many=True)
    if s.get('criteria') is not None:
        q('INSERT OR REPLACE INTO criteria VALUES (1,?,?)', (json.dumps(s['criteria']), now))
    for sp in s.get('suppliers', []):
        d = {k: v for k, v in sp.items() if k not in ('cat', 'factor', 'joined')}
        q('INSERT OR IGNORE INTO suppliers VALUES (?,?,?,?,?)', (sp['n'], sp['cat'], json.dumps(d), float(sp['factor']), now))
    for r in s.get('relationships', []):
        q('INSERT OR REPLACE INTO relationships VALUES (?,?,?,?)', (r['name'], int(r['score']), r.get('note', ''), now))
    if 'bid_lines' in s:
        q('DELETE FROM bid_lines')
        q('INSERT INTO bid_lines VALUES (?,?,?)', [(b['key'], json.dumps(b), now) for b in s['bid_lines']], many=True)
    if 'selections' in s:
        for x in s['selections']:
            q('INSERT OR REPLACE INTO selections VALUES (?,?,?,?,?)', (x['model'], int(x['part']), x['supplier'], float(x['price']), now))

# ---- negotiations ----
def new_negotiation(nid, mode, setup):
    q('INSERT INTO negotiations VALUES (?,?,?,?,?,?,?,?,?)', (nid, mode, time.time(), None, 'running', 0, 0, 0, json.dumps(setup)))

def add_move(nid, e):
    flags = {k: e[k] for k in ('rejected', 'fallback', 'redacted', 'reason') if k in e}
    q('INSERT INTO moves (negotiation, part_key, round, sender, receiver, type, price, lead, esg, message, flags, t) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
      (nid, e.get('key'), e.get('round'), e.get('from'), e.get('to'), e.get('type'), e.get('price'), e.get('lead'), e.get('esg'), e.get('msg'), json.dumps(flags) if flags else None, time.time()))

def finish_negotiation(nid, status, cost, pt, ct, finals):
    q('UPDATE negotiations SET ended = ?, status = ?, cost_inr = ?, prompt_tokens = ?, completion_tokens = ? WHERE id = ?', (time.time(), status, cost, pt, ct, nid))
    q('INSERT OR REPLACE INTO final_offers VALUES (?,?,?,?,?,?)', [(nid, f['key'], f['supplier'], f['price'], f['status'], f.get('lead')) for f in finals], many=True)

def save_scores(nid, mode, label, summary, parts):
    q('INSERT OR REPLACE INTO eval_runs VALUES (?,?,?,?,?)', (nid, mode, label, time.time(), json.dumps({'summary': summary, 'parts': parts})))
    q('INSERT OR REPLACE INTO eval_scores VALUES (?,?,?)', [(nid, k, float(v)) for k, v in summary.items() if isinstance(v, (int, float))], many=True)

def list_evals(limit=30):
    out = []
    for r in rows('SELECT id, mode, label, started, data FROM eval_runs ORDER BY started DESC LIMIT ?', (limit,)):
        d = json.loads(r['data']); out.append({'id': r['id'], 'mode': r['mode'], 'label': r['label'], 'started': r['started'], 'summary': d.get('summary', {})})
    return out

def list_negotiations():
    return rows('SELECT id, mode, started, ended, status, cost_inr FROM negotiations ORDER BY started DESC LIMIT 50')

def negotiation_moves(nid):
    return rows('SELECT part_key AS key, round, sender AS "from", receiver AS "to", type, price, lead, esg, message AS msg, flags FROM moves WHERE negotiation = ? ORDER BY id', (nid,))

# ---- RFQs: the buyer bot's RFQ documents and the supplier bots' quotes ----
def new_rfq_run(rid, mode, label, setup):
    q('INSERT INTO rfq_runs VALUES (?,?,?,?,?,?,?,?)', (rid, mode, label, time.time(), None, 'running', 0, json.dumps({'setup': setup})))

def add_rfq_doc(rid, key, doc):
    q('INSERT OR REPLACE INTO rfq_docs VALUES (?,?,?)', (rid, key, json.dumps(doc)))

def add_rfq_quote(rid, key, supplier, quote):
    q('INSERT OR REPLACE INTO rfq_quotes VALUES (?,?,?,?)', (rid, key, supplier, json.dumps(quote)))

def finish_rfq_run(rid, status, cost, summary, scores):
    d = rows('SELECT data FROM rfq_runs WHERE id = ?', (rid,)); d = json.loads(d[0]['data']) if d else {}
    d.update(summary=summary, scores=scores)
    q('UPDATE rfq_runs SET ended = ?, status = ?, cost_inr = ?, data = ? WHERE id = ?', (time.time(), status, cost, json.dumps(d), rid))

def list_rfq_runs(limit=20):
    out = []
    for r in rows('SELECT id, mode, label, started, status, cost_inr, data FROM rfq_runs ORDER BY started DESC LIMIT ?', (limit,)):
        d = json.loads(r['data'] or '{}'); out.append({k: r[k] for k in ('id', 'mode', 'label', 'started', 'status', 'cost_inr')} | {'summary': d.get('summary', {})})
    return out

# ---- Bot Lab: golden datasets and scored results (tools/negotiator/lab_server.py) ----
def lab_sets():
    return [dict(r, data=json.loads(r['data'])) for r in rows('SELECT * FROM lab_sets ORDER BY bot, name')]

def lab_save_set(sid, bot, name, data):
    q('INSERT OR REPLACE INTO lab_sets VALUES (?,?,?,?,?)', (sid, bot, name, json.dumps(data), time.time()))

def lab_delete_set(sid):
    q('DELETE FROM lab_sets WHERE id = ?', (sid,))

def lab_save_result(r):
    q('INSERT OR REPLACE INTO lab_results VALUES (?,?,?,?,?,?,?,?)', (r['id'], r['bot'], r.get('set_name', ''), r.get('label', ''), r.get('mode', ''),
                                                                       r.get('started') or time.time(), float(r.get('cost') or 0), json.dumps(r)))

def lab_results(limit=200):
    return [json.loads(r['data']) for r in rows('SELECT data FROM lab_results ORDER BY started DESC LIMIT ?', (limit,))]

def lab_delete_result(rid):
    q('DELETE FROM lab_results WHERE id = ?', (rid,))

# ---- AI activity log (the AI Data Center on the campus): every bot run plus what the app's AI detected ----
def add_ai_event(kind, title, data=None):
    q('INSERT INTO ai_events (t, kind, title, data) VALUES (?,?,?,?)', (time.time(), kind, title, json.dumps(data or {})))

def ai_log(limit=300):
    out = []
    for r in rows('SELECT id, t, kind, title, data FROM ai_events ORDER BY t DESC LIMIT ?', (limit,)):
        out.append({'id': f"ev{r['id']}", 't': r['t'], 'kind': r['kind'], 'title': r['title'], 'data': json.loads(r['data'] or '{}'), 'source': 'event'})
    for r in rows('SELECT id, mode, started, status, cost_inr FROM negotiations ORDER BY started DESC LIMIT ?', (limit,)):
        fin = rows('SELECT part_key, supplier, price, status FROM final_offers WHERE negotiation = ?', (r['id'],))
        n = rows('SELECT COUNT(*) AS n FROM moves WHERE negotiation = ?', (r['id'],))[0]['n']
        ev = rows('SELECT data FROM eval_runs WHERE id = ?', (r['id'],))
        summ = json.loads(ev[0]['data']).get('summary', {}) if ev else {}
        deals = [f for f in fin if f['status'] == 'deal']
        out.append({'id': r['id'], 't': r['started'], 'kind': 'negotiation', 'source': 'run', 'mode': r['mode'], 'status': r['status'], 'cost': r['cost_inr'] or 0,
                    'title': f"Negotiation: {len(set(f['part_key'] for f in fin)) or '?'} parts, {n} messages, {len(deals)} offers agreed",
                    'data': {'grade': summ.get('grade'), 'saving_pct': summ.get('avg_saving_pct'), 'deals': deals[:12]}})
    kinds = {'rfq': 'rfq', 'eval': 'evaluation', 'p2p': 'p2p'}
    for r in rows('SELECT id, mode, label, started, status, cost_inr, data FROM rfq_runs ORDER BY started DESC LIMIT ?', (limit,)):
        k = kinds.get(r['id'].split('-')[0], 'rfq'); d = json.loads(r['data'] or '{}'); sm = d.get('summary', {}) or {}
        title = {'rfq': f"RFQ round: {sm.get('parts', '?')} RFQs written, {sm.get('quotes', '?')} quotes, {sm.get('declined', 0)} declined",
                 'evaluation': f"Evaluation: {sm.get('parts', '?')} parts ranked, shortlists recommended",
                 'p2p': f"PO → Pay: {sm.get('orders', '?')} purchase orders, {sm.get('issues', 0)} 3-way match problems caught"}[k]
        out.append({'id': r['id'], 't': r['started'], 'kind': k, 'source': 'run', 'mode': r['mode'], 'status': r['status'], 'cost': r['cost_inr'] or 0, 'title': title,
                    'data': {'grade': sm.get('grade'), 'label': r['label'], 'scores': (d.get('scores') or [])[:12]}})
    for r in rows('SELECT data FROM lab_results ORDER BY started DESC LIMIT ?', (limit,)):
        d = json.loads(r['data']); sm = d.get('summary', {})
        out.append({'id': d['id'] + '-lab', 't': d.get('started'), 'kind': 'lab', 'source': 'run', 'mode': d.get('mode'), 'cost': d.get('cost') or 0,
                    'title': f"Bot Lab test: {d.get('bot')} · {d.get('set_name')} · label {d.get('label')} · {sm.get('cases_passed')}/{len(d.get('cases', []))} cases passed",
                    'data': {'grade': sm.get('golden_pct'), 'cases': [{'key': c['key'], 'name': c['name'], 'pass': c['golden_pass'] == c['golden_total']} for c in d.get('cases', [])]}})
    out.sort(key=lambda e: e.get('t') or 0, reverse=True)
    return out[:limit]
