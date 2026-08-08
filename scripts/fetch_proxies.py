#!/usr/bin/env python3
"""
Aggregates SOCKS4/SOCKS5 proxies geolocated to Iran (IR) from several free,
public proxy-list sources. Each source is best-effort and wrapped so that
one source failing (rate limit, layout change, downtime) never kills the run.

Sources:
  1. ProxyScrape  - real JSON/text API, most reliable. (primary)
  2. Geonode       - real JSON API, second most reliable.
  3. Geonix        - HTML table scrape (free.geonix.com).
  4. Spys.one      - HTML scrape, BEST EFFORT ONLY. Spys.one obfuscates port
                      numbers with per-page JavaScript, so this only works
                      when the page happens to use one of the encoding
                      patterns we know how to reverse. If it returns nothing,
                      that's expected sometimes -- see README for why.

Output: a flat, de-duplicated list of proxy dicts written to stdout as JSON
(the caller decides what to do with it -- see main.py).
"""
from __future__ import annotations

import re
import sys
import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Iterable

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_proxies")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
TIMEOUT = 15


@dataclass
class Proxy:
    ip: str
    port: int
    protocol: str  # "socks4" | "socks5"
    country: str = "IR"
    source: str = ""

    def key(self) -> tuple:
        return (self.ip, self.port, self.protocol)


def _safe(fn):
    """Run a source-fetching function; log and swallow any exception."""
    try:
        result = list(fn())
        log.info("%s: %d proxies", fn.__name__, len(result))
        return result
    except Exception as exc:  # noqa: BLE001 - we want to survive any source breaking
        log.warning("%s failed: %s", fn.__name__, exc)
        return []


# ---------------------------------------------------------------------------
# Source 1: ProxyScrape (has a real API)
# ---------------------------------------------------------------------------
def fetch_proxyscrape() -> Iterable[Proxy]:
    url = (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&proxy_format=protocolipport&format=text&country=ir"
    )
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    for line in resp.text.splitlines():
        line = line.strip()
        m = re.match(r"^(socks4|socks5)://([\d.]+):(\d+)$", line)
        if m:
            proto, ip, port = m.groups()
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="proxyscrape")


# ---------------------------------------------------------------------------
# Source 2: Geonode (has a real JSON API; kept best-effort since Geonode has
# been migrating this endpoint behind their dashboard).
# ---------------------------------------------------------------------------
def fetch_geonode() -> Iterable[Proxy]:
    url = (
        "https://proxylist.geonode.com/api/proxy-list"
        "?country=IR&protocols=socks4%2Csocks5&limit=200&page=1"
        "&sort_by=lastChecked&sort_type=desc"
    )
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    for item in data.get("data", []):
        ip = item.get("ip")
        port = item.get("port")
        protocols = item.get("protocols", [])
        if not (ip and port):
            continue
        for proto in protocols:
            proto = proto.lower()
            if proto in ("socks4", "socks5"):
                yield Proxy(ip=ip, port=int(port), protocol=proto, source="geonode")


# ---------------------------------------------------------------------------
# Source 3: Geonix (plain HTML table scrape)
# ---------------------------------------------------------------------------
def fetch_geonix() -> Iterable[Proxy]:
    from bs4 import BeautifulSoup

    url = "https://free.geonix.com/en/iran_islamic_republic_of/"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Layout-agnostic approach: scan every table row for an ip:port pattern
    # plus a socks4/socks5 marker somewhere in the same row.
    ip_port_re = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b.*?\b(\d{2,5})\b")
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        low = text.lower()
        if "socks5" in low:
            proto = "socks5"
        elif "socks4" in low:
            proto = "socks4"
        else:
            continue
        m = ip_port_re.search(text)
        if m:
            ip, port = m.groups()
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="geonix")


# ---------------------------------------------------------------------------
# Source 4: Spys.one -- BEST EFFORT ONLY, see module docstring.
# Spys.one XORs each port digit against a per-page JS variable table, so a
# plain-requests scrape can only recover proxies on the occasions the page
# ships port digits in cleartext spans (which happens for a subset of rows).
# If this returns zero results, that's the obfuscation winning, not a bug.
# ---------------------------------------------------------------------------
def fetch_spys_one() -> Iterable[Proxy]:
    from bs4 import BeautifulSoup

    url = "https://spys.one/free-proxy-list/IR/"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for row in soup.find_all("tr", class_=re.compile("spy1")):
        text = row.get_text(" ", strip=True)
        low = text.lower()
        if "socks5" in low:
            proto = "socks5"
        elif "socks4" in low:
            proto = "socks4"
        else:
            continue
        ip_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
        # Only usable when the port happens to render as plain digits
        # right after the IP (no <script> substitution needed).
        port_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\s*:?\s*(\d{2,5})\b", text)
        if ip_match and port_match:
            ip = ip_match.group(1)
            port = port_match.group(2)
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="spys.one")


SOURCES = [fetch_proxyscrape, fetch_geonode, fetch_geonix, fetch_spys_one]


def fetch_all() -> list[Proxy]:
    seen: dict[tuple, Proxy] = {}
    for src in SOURCES:
        for proxy in _safe(src):
            seen[proxy.key()] = proxy  # de-dupe, last source wins on conflict
        time.sleep(1)  # be polite between sources
    return list(seen.values())


if __name__ == "__main__":
    proxies = fetch_all()
    json.dump([asdict(p) for p in proxies], sys.stdout, indent=2)
    log.info("Total unique raw proxies: %d", len(proxies))
