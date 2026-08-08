#!/usr/bin/env python3
"""
Probes a list of proxy dicts (as produced by fetch_proxies.py) by opening a
connection *through* each one and requesting a small URL.

IMPORTANT: this never drops anything. Every proxy that goes in comes back
out, just with three extra fields added:
  - "alive": true/false
  - "latency_ms": measured round trip if alive, else null
  - "checked_at": ISO timestamp of the probe

The list is meant to show everything the sources reported, dead or alive --
callers can filter on "alive" themselves if they want a stricter feed.

Reads JSON from stdin, writes JSON (same length as input) to stdout.
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


def check_one(proxy: dict) -> dict:
    proxy_url = f"{proxy['protocol']}://{proxy['ip']}:{proxy['port']}"
    proxies = {"http": proxy_url, "https": proxy_url}
    out = dict(proxy)
    out["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    start = time.monotonic()
    try:
        resp = requests.get(CHECK_URL, proxies=proxies, timeout=TIMEOUT)
        if resp.status_code == 200:
            out["alive"] = True
            out["latency_ms"] = round((time.monotonic() - start) * 1000)
        else:
            out["alive"] = False
            out["latency_ms"] = None
    except Exception:
        out["alive"] = False
        out["latency_ms"] = None
    return out


def check_all(proxies: list[dict]) -> list[dict]:
    """Annotates every proxy with alive/latency_ms/checked_at. Never removes
    an entry -- len(output) == len(input) always."""
    annotated: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_one, p): p for p in proxies}
        for i, fut in enumerate(as_completed(futures), 1):
            annotated.append(fut.result())
            if i % 25 == 0:
                alive_so_far = sum(1 for p in annotated if p["alive"])
                log.info("checked %d/%d (%d alive so far)", i, len(proxies), alive_so_far)
    # sort: alive + fastest first, then dead ones at the end
    annotated.sort(key=lambda p: (not p["alive"], p["latency_ms"] if p["latency_ms"] is not None else 10**9))
    return annotated


if __name__ == "__main__":
    raw = json.load(sys.stdin)
    log.info("Probing %d candidate proxies (this needs PySocks installed)...", len(raw))
    result = check_all(raw)
    alive = sum(1 for p in result if p["alive"])
    log.info("Alive: %d / %d (all %d kept in output)", alive, len(raw), len(result))
    json.dump(result, sys.stdout, indent=2)
