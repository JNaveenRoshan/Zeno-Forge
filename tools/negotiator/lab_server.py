"""Zeno Bot Lab: a separate test bench for the AI bots, on its own port (default 8780).

  python tools/negotiator/lab_server.py [port]

It does not serve the Forge app. It serves tools/negotiator/lab/ and lets you run the same bots
(negotiation, RFQ, evaluator) on golden test cases you write yourself, with the hidden limits you choose.

  GET  /api/lab/sets            POST /api/lab/sets {id, bot, name, cases}     DELETE via POST /api/lab/sets/delete {id}
  POST /api/lab/run             {bot: negotiation|rfq|evaluator, mode: mock|sarvam, label, rounds, cases:[...]}
  GET  /api/<negotiations|rfqs|evaluations>/<id>/events                      live stream (same as the app)
  GET  /api/lab/results         POST /api/lab/results {...}                  scored runs for the scoreboard
"""
import http.server, json, sys, time, uuid
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import agents, db, engine, evaluator, rfq, server

LAB = Path(__file__).with_name('lab')


def seed_sets():
    """Load the default golden datasets the first time the lab starts."""
    if db.lab_sets():
        return
    for st in json.loads((LAB / 'golden.json').read_text(encoding='utf-8')):
        db.lab_save_set(st['id'], st['bot'], st['name'], {'cases': st['cases']})


class LabH(server.H):
    def __init__(self, *a, **k):
        http.server.SimpleHTTPRequestHandler.__init__(self, *a, directory=str(LAB), **k)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            self.path = '/lab.html'
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        if path == '/api/lab/sets':
            return self._json(db.lab_sets())
        if path == '/api/lab/results':
            return self._json(db.lab_results())
        if path.startswith('/api/'):
            return super().do_GET()  # health, live event streams
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({'error': 'bad json'}, 400)
        if path == '/api/lab/sets':
            sid = body.get('id') or 'set-' + uuid.uuid4().hex[:6]
            db.lab_save_set(sid, body['bot'], body.get('name') or 'Untitled set', {'cases': body.get('cases', [])})
            return self._json({'id': sid})
        if path == '/api/lab/sets/delete':
            db.lab_delete_set(body.get('id')); return self._json({'ok': True})
        if path == '/api/lab/results':
            db.lab_save_result(body); return self._json({'ok': True})
        if path == '/api/lab/results/delete':
            db.lab_delete_result(body.get('id')); return self._json({'ok': True})
        if path == '/api/lab/run':
            mode = body.get('mode', 'mock')
            if mode == 'sarvam' and not agents.api_key():
                return self._json({'error': 'No Sarvam API key in tools/negotiator/.env'}, 400)
            cases, bot, label = body.get('cases') or [], body.get('bot'), body.get('label') or 'lab'
            if not cases:
                return self._json({'error': 'no cases selected'}, 400)
            if bot == 'negotiation':
                run = engine.start(mode, cases, int(body.get('rounds', 5)), float(body.get('cap_inr', 100)), label=f'lab:{label}')
            elif bot == 'rfq':
                run = rfq.start(mode, cases, label=f'lab:{label}')
            elif bot == 'evaluator':
                run = evaluator.start(mode, cases)
            else:
                return self._json({'error': 'unknown bot'}, 400)
            return self._json({'id': run.id, 'mode': mode, 'stream': {'negotiation': 'negotiations', 'rfq': 'rfqs', 'evaluator': 'evaluations'}[bot]})
        return self._json({'error': 'not found'}, 404)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8780
    seed_sets()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), LabH)
    srv.daemon_threads = True
    print(f'Zeno Bot Lab on http://127.0.0.1:{port}/ | Sarvam key {"found" if agents.api_key() else "NOT set"} | db {db.DB_PATH}', flush=True)
    srv.serve_forever()
