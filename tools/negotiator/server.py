"""Zeno Forge local server.

  python tools/negotiator/server.py [port]

Serves the app (no caching) and the negotiation API:
  GET  /api/health                         -> server up, Sarvam key present?, mock mode?
  GET  /api/state      POST /api/state     -> load / save budgets, criteria, suppliers, bids, selections (SQLite)
  POST /api/negotiations                   -> start a run {mode: 'sarvam'|'mock', rounds, cap_inr, lines:[...]}
  GET  /api/negotiations                   -> past runs
  GET  /api/negotiations/<id>/events       -> live event stream (Server-Sent Events)
  GET  /api/negotiations/<id>/moves        -> full transcript of a past run
  POST /api/negotiations/<id>/control      -> {action: pause|resume|stop}
  POST /api/runs                           -> store a finished simulation run from the browser
  POST /api/rfqs  GET /api/rfqs            -> start an RFQ round (buyer bot writes RFQs, supplier bots quote) / past rounds
  GET  /api/rfqs/<id>/events               -> live RFQ event stream
  POST /api/evaluations                    -> evaluator bot ranks the quotes   GET /api/evaluations/<id>/events -> live stream
"""
import http.server, json, sys, time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import agents, db, engine, evaluator, p2p, rfq

ROOT = Path(__file__).resolve().parents[2]  # the renault/ folder that holds zeno-forge.html


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and '/api/' in str(args[0]):
            super().log_message(fmt, *args)

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n).decode('utf-8') or '{}') if n else {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self._json({'ok': True, 'sarvam': bool(agents.api_key()), 'mock': agents.mock_mode(), 'model': agents.MODEL})
        if path == '/api/state':
            return self._json(db.load_state())
        if path == '/api/negotiations':
            return self._json(db.list_negotiations())
        if path == '/api/evals':
            return self._json(db.list_evals())
        if path == '/api/rfqs':
            return self._json(db.list_rfq_runs())
        if path == '/api/ai-log':
            return self._json(db.ai_log())
        parts = path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'negotiations'] and parts[3] == 'moves':
            return self._json(db.negotiation_moves(parts[2]))
        if len(parts) == 4 and parts[:2] in (['api', 'negotiations'], ['api', 'rfqs'], ['api', 'evaluations'], ['api', 'p2p']) and parts[3] == 'events':
            return self._stream(parts[2])
        if path == '/':
            self.path = '/zeno-forge.html'
        return super().do_GET()

    def _stream(self, rid):
        run = engine.RUNS.get(rid) or rfq.RUNS.get(rid) or evaluator.RUNS.get(rid) or p2p.RUNS.get(rid)
        if not run:
            return self._json({'error': 'unknown run'}, 404)
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Connection', 'keep-alive'); self.end_headers()
        sent = 0
        try:
            while True:
                with run.cond:
                    while sent >= len(run.events) and not run.done:
                        run.cond.wait(timeout=15)
                        if sent >= len(run.events) and not run.done:
                            self.wfile.write(b': keep-alive\n\n'); self.wfile.flush()
                    batch = run.events[sent:]; sent = len(run.events)
                for e in batch:
                    self.wfile.write(f'data: {json.dumps(e)}\n\n'.encode('utf-8'))
                self.wfile.flush()
                if run.done and sent >= len(run.events):
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith('/api/promo/'):
            return self._promo(path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({'error': 'bad json'}, 400)
        if path == '/api/state':
            db.save_state(body); return self._json({'ok': True})
        if path == '/api/negotiations':
            mode = body.get('mode', 'sarvam')
            if mode == 'sarvam' and agents.mock_mode():
                mode = 'mock'
            if mode == 'sarvam' and not agents.api_key():
                return self._json({'error': 'No Sarvam API key. Put SARVAM_API_KEY=... in tools/negotiator/.env and restart the server.'}, 400)
            lines = body.get('lines') or []
            if not lines:
                return self._json({'error': 'no bid lines'}, 400)
            run = engine.start(mode, lines, int(body.get('rounds', 5)), float(body.get('cap_inr', 250)))
            return self._json({'id': run.id, 'mode': mode})
        if path == '/api/rfqs':
            mode = body.get('mode', 'sarvam')
            if mode == 'sarvam' and agents.mock_mode():
                mode = 'mock'
            if mode == 'sarvam' and not agents.api_key():
                return self._json({'error': 'No Sarvam API key. Put SARVAM_API_KEY=... in tools/negotiator/.env and restart the server.'}, 400)
            lines = body.get('lines') or []
            if not lines or not all(l.get('suppliers') for l in lines):
                return self._json({'error': 'every part needs at least one invited supplier'}, 400)
            run = rfq.start(mode, lines)
            return self._json({'id': run.id, 'mode': mode})
        if path == '/api/evaluations':
            mode = body.get('mode', 'sarvam')
            if mode == 'sarvam' and agents.mock_mode():
                mode = 'mock'
            if mode == 'sarvam' and not agents.api_key():
                return self._json({'error': 'No Sarvam API key. Put SARVAM_API_KEY=... in tools/negotiator/.env and restart the server.'}, 400)
            lines = [l for l in body.get('lines') or [] if l.get('quotes')]
            if not lines:
                return self._json({'error': 'no quotes to evaluate - run the RFQ step first'}, 400)
            run = evaluator.start(mode, lines)
            return self._json({'id': run.id, 'mode': mode})
        if path == '/api/ai-log':
            db.add_ai_event(str(body.get('kind', 'note'))[:30], str(body.get('title', ''))[:300], body.get('data') or {}); return self._json({'ok': True})
        if path == '/api/p2p':
            mode = body.get('mode', 'sarvam')
            if mode == 'sarvam' and agents.mock_mode():
                mode = 'mock'
            if mode == 'sarvam' and not agents.api_key():
                return self._json({'error': 'No Sarvam API key. Put SARVAM_API_KEY=... in tools/negotiator/.env and restart the server.'}, 400)
            orders = body.get('orders') or []
            if not orders:
                return self._json({'error': 'no awarded orders'}, 400)
            run = p2p.start(mode, orders)
            return self._json({'id': run.id, 'mode': mode})
        if path == '/api/runs':
            rid = time.strftime('%Y%m%d-%H%M%S-sim')
            db.new_negotiation(rid, 'simulation', {'parts': [p.get('key') for p in body.get('parts', [])]})
            for p in body.get('parts', []):
                for e in p.get('events', []):
                    db.add_move(rid, dict(e, key=p.get('key')))
            db.finish_negotiation(rid, 'done', 0, 0, 0, body.get('finals', []))
            return self._json({'id': rid})
        parts = path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'negotiations'] and parts[3] == 'control':
            run = engine.RUNS.get(parts[2])
            if not run:
                return self._json({'error': 'unknown run'}, 404)
            a = body.get('action')
            if a == 'pause': run.paused = True
            elif a == 'resume': run.paused = False
            elif a == 'stop': run.stopped = True; run.paused = False
            return self._json({'ok': True})
        return self._json({'error': 'not found'}, 404)


    # ---- promo film: the page encodes H.264 itself (WebCodecs) and streams the chunks here; ffmpeg wraps them into an MP4 ----
    def _promo(self, path):
        import subprocess
        n = int(self.headers.get('Content-Length') or 0); data = self.rfile.read(n) if n else b''
        d = ROOT / 'promo'; d.mkdir(exist_ok=True); raw = d / 'stream.h264'
        if path == '/api/promo/start':
            raw.write_bytes(b''); PROMO['cfg'] = json.loads(data or b'{}'); return self._json({'ok': True})
        if path == '/api/promo/chunk':
            with open(raw, 'ab') as f: f.write(data)
            return self._json({'ok': True})
        if path == '/api/promo/finish':
            cfg = PROMO.get('cfg', {}); outp = d / cfg.get('name', 'zeno-forge-promo.mp4')
            r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-r', str(cfg.get('fps', 30)), '-i', str(raw), '-c:v', 'copy', '-movflags', '+faststart', str(outp)],
                               capture_output=True, text=True)
            if r.returncode == 0: raw.unlink(missing_ok=True)
            return self._json({'ok': r.returncode == 0, 'file': str(outp), 'err': r.stderr[-500:]})
        return self._json({'error': 'not found'}, 404)


PROMO = {}


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8771
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), H)
    srv.daemon_threads = True
    key = 'found' if agents.api_key() else 'NOT set (Sarvam mode disabled)'
    print(f'Zeno Forge on http://127.0.0.1:{port}/zeno-forge.html | Sarvam key {key}{" | MOCK mode" if agents.mock_mode() else ""} | db {db.DB_PATH}', flush=True)
    srv.serve_forever()
