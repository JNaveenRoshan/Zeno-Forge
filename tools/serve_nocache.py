# Local dev server for Zeno Forge that tells the browser never to cache, so a normal refresh always shows the latest file.
import http.server, sys
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.send_header('Expires', '0')
        super().end_headers()
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8771
http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
