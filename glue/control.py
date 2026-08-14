"""Tiny HTTP control server for glue runtime settings.

Exposes GET/POST /websearch so the extension popup can toggle live web-search
term verification without restarting glue.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_searches = []  # registry of TavilySearch instances (usually one)


def register_search(search):
    _searches.append(search)
    return len(_searches) - 1


def _current():
    return _searches[-1] if _searches else None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        # Preflight: Chrome's Private Network Access requires the
        # Allow-Private-Network header when an extension/web page calls a
        # localhost HTTP service, otherwise fetch() rejects with
        # "Failed to fetch".
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/websearch"):
            s = _current()
            self._send(200, {"enabled": bool(s and s.enabled)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/websearch"):
            n = int(self.headers.get("Content-Length") or 0)
            body = {}
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    pass
            s = _current()
            if s is not None:
                s.enabled = bool(body.get("enabled", s.enabled))
            self._send(200, {"enabled": bool(s and s.enabled)})
        else:
            self._send(404, {"error": "not found"})


def start_control(host="127.0.0.1", port=5120):
    srv = HTTPServer((host, port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[glue] control on http://{host}:{port} (GET/POST /websearch)", flush=True)
    return srv
