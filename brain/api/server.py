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
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from brain import __version__
from brain.api.service import recommend
from brain.config import load_config
from brain.ingest.importer import import_bytes
from brain.model.capacity import CapacityModel
from brain.model.train import train_model
from brain.storage import Store

logger = logging.getLogger("egoptimizer")

_CONFIG = load_config()
_DB_PATH = _CONFIG["storage"]["db_path"]
_MODEL_PATH = _CONFIG.get("model", {}).get("path", "data/model.json")
_MAX_INTERVAL = _CONFIG["ingest"]["max_interval_kwh"]


class Handler(BaseHTTPRequestHandler):
    server_version = f"EGOptimizer/{__version__}"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            model = CapacityModel.load(_MODEL_PATH)
            self._send(
                200,
                {
                    "status": "ok",
                    "version": Handler.server_version,
                    "model_path": _MODEL_PATH,
                    "model_loaded": model is not None,
                    "model_buckets": len(model.buckets) if model is not None else 0,
                },
            )
        elif self.path.startswith("/decisions"):
            with Store(_DB_PATH) as store:
                rows = store.conn.execute(
                    "SELECT id, decided_at, feed_kw, eg_budget_kwh, explore "
                    "FROM decisions ORDER BY id DESC LIMIT 20"
                ).fetchall()
            self._send(200, {"decisions": [dict(r) for r in rows]})
        else:
            self._send(404, {"error": "not found"})

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_POST(self) -> None:  # noqa: N802
        route = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(route.query).items()}
        try:
            if route.path == "/recommend":
                state = json.loads(self._body() or b"{}")
                model = CapacityModel.load(_MODEL_PATH)  # None until first train
                with Store(_DB_PATH) as store:
                    result = recommend(state, _CONFIG, store, model)
                dbg = result.get("debug") or {}
                model_dbg = dbg.get("model") or {}
                inputs_dbg = dbg.get("inputs") or {}
                decision_dbg = dbg.get("decision") or {}
                logger.info(
                    "POST /recommend -> feed=%.2f kW, budget=%.2f kWh, status=%s, confidence=%s, "
                    "path=%s, model_loaded=%s, buckets=%s, soc=%.1f%%, target=%.1f%%, load=%.3f kW",
                    result.get("feed_kw", 0),
                    result.get("eg_budget_kwh", 0),
                    result.get("status"),
                    result.get("confidence"),
                    decision_dbg.get("path"),
                    model_dbg.get("loaded"),
                    model_dbg.get("bucket_count"),
                    float(inputs_dbg.get("soc_pct", 0.0)),
                    float(inputs_dbg.get("target_morning_soc_pct", 0.0)),
                    float(inputs_dbg.get("load_kw", 0.0)),
                )
                self._send(200, result)

            elif route.path == "/import":
                # Upload a CSV instead of dropping it in a folder. Filename
                # matters (meter id is parsed from it) -> pass ?filename= or
                # the X-Filename header.
                filename = params.get("filename") or self.headers.get("X-Filename", "upload.csv")
                provider = params.get("provider", "netznoe")
                logger.info("POST /import: %s (%s)", filename, provider)
                result = import_bytes(self._body(), filename, provider, _DB_PATH, _MAX_INTERVAL)
                logger.info(
                    "  imported=%s dropped=%s total=%s",
                    result["imported"], result["dropped"], result["store"]["n"],
                )
                if params.get("train") in ("1", "true", "yes"):
                    logger.info("  training model...")
                    result["train"] = train_model(
                        _DB_PATH, _MODEL_PATH,
                        _CONFIG["model"]["exploration_aggressiveness"],
                        _CONFIG["model"]["mode"],
                    )
                    logger.info(
                        "  trained: %s records, %s buckets",
                        result["train"]["records"], result["train"]["buckets"],
                    )
                self._send(200, result)

            elif route.path == "/train":
                logger.info("POST /train")
                result = train_model(
                    _DB_PATH, _MODEL_PATH,
                    _CONFIG["model"]["exploration_aggressiveness"],
                    _CONFIG["model"]["mode"],
                )
                logger.info(
                    "  trained: %s records, %s buckets, %s uncertain",
                    result["records"], result["buckets"], result["uncertain_buckets"],
                )
                self._send(200, result)

            elif route.path == "/purge":
                # Delete old data: ?before=ISO  or  ?keep_days=N
                before = params.get("before")
                if not before:
                    keep_days = int(params.get("keep_days", 730))
                    before = (datetime.now() - timedelta(days=keep_days)).isoformat()
                logger.info("POST /purge: before=%s", before)
                with Store(_DB_PATH) as store:
                    removed = store.delete_before(before)
                    summary = store.summary()
                logger.info("  deleted=%s remaining=%s", removed, summary["n"])
                self._send(200, {"deleted": removed, "before": before, "store": summary})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # keep the loop alive; report cleanly
            logger.warning("%s %s -> error: %s", "POST", route.path, exc)
            self._send(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        # Route the stdlib access line through our logger (debug = quiet by default).
        logger.debug("%s - %s", self.address_string(), fmt % args)


def main(argv: list[str] | None = None) -> int:
    # Log to STDOUT so `docker logs` / Portainer show it live. PYTHONUNBUFFERED=1
    # (set in the Dockerfile) keeps it from being block-buffered.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="EGOptimizer recommend API.")
    ap.add_argument("--host", default=_CONFIG["api"]["host"])
    ap.add_argument("--port", type=int, default=_CONFIG["api"]["port"])
    args = ap.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("=== EGOptimizer v%s ===", __version__)
    logger.info("data db : %s", _DB_PATH)
    logger.info("model   : %s", _MODEL_PATH)
    logger.info("listening: http://%s:%s", args.host, args.port)
    logger.info("endpoints: POST /recommend /import /train /purge | GET /health /decisions")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
