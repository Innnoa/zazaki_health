#!/usr/bin/env python3
"""
zazaki_health_receiver/server.py — HTTP receiver for HealthReader phone data.
手机 HealthReader → POST /upload (raw JSON body) → 存到 data/<timestamp>.json

Run on the Windows host (phone reaches it via 192.168.137.1):
    python server.py [--port 8899] [--dir data]
Or on any host the phone can reach; the app posts to its configured URL.
"""
import argparse
import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class DataServer(HTTPServer):
    def __init__(self, addr, handler, data_dir):
        super().__init__(addr, handler)
        self.data_dir = data_dir


class Handler(BaseHTTPRequestHandler):
    @property
    def data_dir(self) -> str:
        server = self.server
        assert isinstance(server, DataServer), "server must be DataServer"
        return server.data_dir

    def _send(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/upload":
            self._send(404, json.dumps({"ok": False, "error": "not found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if not raw:
                self._send(400, json.dumps({"ok": False, "error": "empty body"}))
                return
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": f"bad json: {e}"}))
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"health_{ts}.json"
        path = os.path.join(self.data_dir, fname)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        latest = os.path.join(self.data_dir, "_latest.json")
        with open(latest, "w", encoding="utf-8") as f:
            f.write(text)
        self._send(200, json.dumps({"ok": True, "file": fname, "size": len(raw)}))

    def log_message(self, format, *args):
        print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {format % args}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    args = ap.parse_args()
    os.makedirs(args.dir, exist_ok=True)
    srv = DataServer((args.host, args.port), Handler, args.dir)
    print(f"Listening on {args.host}:{args.port}, saving to {args.dir}")
    print("POST /upload with raw JSON body.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
