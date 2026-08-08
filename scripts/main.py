#!/usr/bin/env python3
"""
Orchestrates the pipeline and writes the files that get published via
GitHub Pages. Every proxy every source reports shows up in the output --
nothing gets dropped for being slow, dead, or unreachable. If a liveness
probe runs, it only adds "alive"/"latency_ms"/"checked_at" fields.

  docs/proxies.json   - full JSON list, ALL fetched proxies, de-duped
  docs/socks.txt       - plain text, one "protocol://ip:port" per line
  docs/meta.json        - status file (counts, last update time, per-source)

Run with: python scripts/main.py
Env vars:
  SKIP_CHECK=1   -> skip the liveness probe entirely (fastest; no alive/
                     latency_ms fields, just the raw fetched list)
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
    proxies = [p.__dict__ for p in raw]
    log.info("Fetched %d unique proxies from all sources (nothing filtered)", len(proxies))

    if os.environ.get("SKIP_CHECK") == "1":
        for p in proxies:
            p.setdefault("alive", None)
            p.setdefault("latency_ms", None)
            p.setdefault("checked_at", None)
    else:
        proxies = check_all(proxies)  # same length in and out, just annotated

    assert len(proxies) == len(raw), "check_all must never change the count"

    (DOCS / "proxies.json").write_text(json.dumps(proxies, indent=2))

    lines = [f"{p['protocol']}://{p['ip']}:{p['port']}" for p in proxies]
    (DOCS / "socks.txt").write_text("\n".join(lines) + ("\n" if lines else ""))

    alive_count = sum(1 for p in proxies if p.get("alive"))
    meta = {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_count": len(proxies),
        "alive_count": alive_count,
        "by_protocol": {
            "socks4": sum(1 for p in proxies if p["protocol"] == "socks4"),
            "socks5": sum(1 for p in proxies if p["protocol"] == "socks5"),
        },
        "by_source": {},
    }
    for p in proxies:
        meta["by_source"][p["source"]] = meta["by_source"].get(p["source"], 0) + 1
    (DOCS / "meta.json").write_text(json.dumps(meta, indent=2))

    log.info(
        "Done. %d proxies written to docs/proxies.json (%d responded alive)",
        len(proxies),
        alive_count,
    )


if __name__ == "__main__":
    main()
