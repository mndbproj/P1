#!/usr/bin/env python3
"""
Validates a list of proxy dicts (as produced by fetch_proxies.py) by actually
opening a connection *through* each one and requesting a small URL. Dead or
fake proxies (a huge fraction of any free list) get dropped. Survivors get a
measured latency and a "checked_at" timestamp.

Reads JSON from stdin, writes JSON (only working proxies) to stdout.
"""
from __future__ import annotations

import sys
import json
import time
import logging
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("check_proxies")

CHECK_URL = "http://httpbin.org/ip"  # small, fast, tells us the exit IP
TIMEOUT = 8
MAX_WORKERS = 40


def check_one(proxy: dict) -> dict | None:
    proxy_url = f"{proxy['protocol']}://{proxy['ip']}:{proxy['port']}"
    proxies = {"http": proxy_url, "https": proxy_url}
    start = time.monotonic()
    try:
        resp = requests.get(CHECK_URL, proxies=proxies, timeout=TIMEOUT)
        if resp.status_code != 200:
            return None
        latency_ms = round((time.monotonic() - start) * 1000)
        out = dict(proxy)
        out["latency_ms"] = latency_ms
        out["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return out
    except Exception:
        return None


def check_all(proxies: list[dict]) -> list[dict]:
    working: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_one, p): p for p in proxies}
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result:
                working.append(result)
            if i % 25 == 0:
                log.info("checked %d/%d (%d alive so far)", i, len(proxies), len(working))
    working.sort(key=lambda p: p["latency_ms"])
    return working


if __name__ == "__main__":
    raw = json.load(sys.stdin)
    log.info("Checking %d candidate proxies (this needs PySocks installed)...", len(raw))
    alive = check_all(raw)
    log.info("Alive: %d / %d", len(alive), len(raw))
    json.dump(alive, sys.stdout, indent=2)
