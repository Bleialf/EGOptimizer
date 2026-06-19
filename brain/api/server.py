"""Minimal stdlib HTTP server exposing the recommend service.

Zero dependencies (http.server) so it runs anywhere immediately. The handler
is a thin adapter over the pure recommend() function; swapping in FastAPI later
touches only this file.

Endpoints:
    GET  /health      -> {"status": "ok"}
    POST /recommend   -> recommendation JSON (body = current state from HA)
    GET  /decisions   -> recent logged decisions (debugging)

Run:
    python -m brain.api.server
    python -m brain.api.server --port 8787
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

from brain.api.service import recommend
from brain.config import load_config
from brain.model.capacity import CapacityModel
from brain.storage import Store

_CONFIG = load_config()
_DB_PATH = _CONFIG["storage"]["db_path"]
_MODEL_PATH = _CONFIG.get("model", {}).get("path", "data/model.json")


class Handler(BaseHTTPRequestHandler):
    server_version = "EGOptimizer/0.2"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "version": Handler.server_version})
        elif self.path.startswith("/decisions"):
            with Store(_DB_PATH) as store:
                rows = store.conn.execute(
                    "SELECT id, decided_at, feed_kw, eg_budget_kwh, explore "
                    "FROM decisions ORDER BY id DESC LIMIT 20"
                ).fetchall()
            self._send(200, {"decisions": [dict(r) for r in rows]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/recommend":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            state = json.loads(self.rfile.read(length) or b"{}")
            model = CapacityModel.load(_MODEL_PATH)  # None until first train
            with Store(_DB_PATH) as store:
                result = recommend(state, _CONFIG, store, model)
            self._send(200, result)
        except Exception as exc:  # keep the loop alive; report cleanly
            self._send(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EGOptimizer recommend API.")
    ap.add_argument("--host", default=_CONFIG["api"]["host"])
    ap.add_argument("--port", type=int, default=_CONFIG["api"]["port"])
    args = ap.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"EGOptimizer recommend API on http://{args.host}:{args.port}  (db: {_DB_PATH})")
    print("  POST /recommend   GET /health   GET /decisions")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
