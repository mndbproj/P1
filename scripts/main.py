#!/usr/bin/env python3
"""
Orchestrates the pipeline and writes the files that get published via
GitHub Pages:

  docs/proxies.json   - full JSON list, working proxies only, richest format
  docs/socks.txt       - plain text, one "protocol://ip:port" per line
  docs/meta.json        - small status file (counts, last update time)

Run with: python scripts/main.py
Env vars:
  SKIP_CHECK=1   -> skip the liveness check (faster, but includes dead IPs)
"""
from __future__ import annotations

import os
import json
import logging
import datetime as dt
from pathlib import Path

from fetch_proxies import fetch_all
from check_proxies import check_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def main() -> None:
    DOCS.mkdir(exist_ok=True)

    raw = fetch_all()
    raw_dicts = [p.__dict__ for p in raw]
    log.info("Fetched %d unique raw proxies from all sources", len(raw_dicts))

    if os.environ.get("SKIP_CHECK") == "1":
        working = raw_dicts
        for p in working:
            p.setdefault("latency_ms", None)
            p.setdefault("checked_at", None)
    else:
        working = check_all(raw_dicts)

    (DOCS / "proxies.json").write_text(json.dumps(working, indent=2))

    lines = [f"{p['protocol']}://{p['ip']}:{p['port']}" for p in working]
    (DOCS / "socks.txt").write_text("\n".join(lines) + ("\n" if lines else ""))

    meta = {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "raw_count": len(raw_dicts),
        "working_count": len(working),
        "by_protocol": {
            "socks4": sum(1 for p in working if p["protocol"] == "socks4"),
            "socks5": sum(1 for p in working if p["protocol"] == "socks5"),
        },
        "by_source": {},
    }
    for p in working:
        meta["by_source"][p["source"]] = meta["by_source"].get(p["source"], 0) + 1
    (DOCS / "meta.json").write_text(json.dumps(meta, indent=2))

    log.info("Done. %d working proxies written to docs/proxies.json", len(working))


if __name__ == "__main__":
    main()
