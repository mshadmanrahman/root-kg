#!/usr/bin/env python3
"""Warm Root daemon: keeps DB + embedder loaded for low-latency semantic search.

Zero LLM. Localhost only.
Endpoints:
  GET /health
  GET /search?q=...&limit=5
"""
from __future__ import annotations

import json
import logging
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import RootDB
from embeddings import Embedder
from tools.search import semantic_search

HOST = "127.0.0.1"
PORT = 8767
LOG = PROJECT_ROOT / "logs" / "rootd.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG), level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("rootd")

START = time.time()
DB = RootDB(PROJECT_ROOT / "data/root.db")
log.info("loading embedder")
EMBEDDER = Embedder()
log.info("rootd ready in %.1fs", time.time() - START)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        log.info("http " + format, *args)

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/health":
                self.send_json(200, {"ok": True, "uptime_s": round(time.time() - START, 1)})
                return
            if parsed.path == "/search":
                qs = urllib.parse.parse_qs(parsed.query)
                q = (qs.get("q") or [""])[0].strip()
                limit = int((qs.get("limit") or ["5"])[0])
                if not q:
                    self.send_json(400, {"ok": False, "error": "missing q"})
                    return
                t0 = time.time()
                results = semantic_search(q, DB, EMBEDDER)[:limit]
                self.send_json(200, {"ok": True, "duration_s": round(time.time() - t0, 3), "results": results})
                return
            self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            log.exception("request failed path=%s", self.path)
            self.send_json(500, {"ok": False, "error": str(e)})


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), Handler)
    log.info("listening on http://%s:%s", HOST, PORT)
    try:
        server.serve_forever()
    finally:
        DB.close()
