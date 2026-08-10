#!/usr/bin/env python3
"""
Aggregates SOCKS4/SOCKS5 proxies geolocated to Iran (IR) from several free,
public proxy-list sources. Each source is best-effort and wrapped so that
one source failing (rate limit, layout change, downtime) never kills the run.

Sources:
  1. ProxyScrape  - real text API, most reliable. (primary)
  2. Geonode       - real JSON API, best-effort (endpoint moves around).
  3. Proxifly      - real JSON file per country, hosted on GitHub + jsDelivr
                      CDN, refreshed every 5 minutes. Very reliable.
  4. monosans/proxy-list - real JSON file (all countries/protocols in one
                      file, filtered locally), hosted on GitHub, refreshed
                      hourly. Very reliable.
  5. Geonix        - HTML table scrape (free.geonix.com).
  6. Spys.one      - HTML scrape, BEST EFFORT ONLY. Spys.one obfuscates port
                      numbers with per-page JavaScript, so this only works
                      when the page happens to use one of the encoding
                      patterns we know how to reverse. If it returns nothing,
                      that's expected sometimes -- see README for why.
  7. jetkai/proxy-list - real JSON (advanced view, has a "country" field per
                      proxy), hosted on GitHub, refreshed ~hourly.
  8. ProxyGenerator (proxygenerator1) - per-country txt files
                      (Stable/country/Iran/socks{4,5}.txt and the
                      MostStable/ equivalents), hosted on GitHub.
  9. ProxyDB.net   - HTML scrape, filtered to country=IR server-side.
  10. Proxifly (all-countries protocol files) - same source as #3 but pulls
                      every country instead of the pre-filtered IR file,
                      filtered to IR locally. Refreshes every 5 minutes, so
                      it often surfaces new IR entries before the dedicated
                      per-country file does.
  11. Geo-filtered bulk lists - several large, frequently-updated proxy
                      dumps (TheSpeedX, hookzof, roosterkid, prxchk,
                      ShiftyTR, VPSLabCloud, clarketm) publish thousands of
                      SOCKS4/5 proxies total with NO country tag at all. We
                      pull those and batch-geolocate the IPs with
                      ip-api.com's free batch endpoint (100 IPs/request,
                      rate-limited client side), keeping only entries that
                      resolve to Iran. This is usually the single biggest
                      source of new IR proxies, since most big dumps never
                      publish a per-country file.

Nothing here filters proxies out by "does it work" -- that's a deliberate
choice, see check_proxies.py and main.py. This module only de-duplicates
identical (ip, port, protocol) entries seen from more than one source.

Output: a flat, de-duplicated list of proxy dicts written to stdout as JSON
(the caller decides what to do with it -- see main.py).
"""
from __future__ import annotations

import os
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
# Source 3: Proxifly (real JSON file per country, via jsDelivr CDN mirror of
# github.com/proxifly/free-proxy-list, refreshed every 5 minutes)
# ---------------------------------------------------------------------------
def fetch_proxifly() -> Iterable[Proxy]:
    url = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/IR/data.json"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    for item in data:
        proto = str(item.get("protocol", "")).lower()
        ip = item.get("ip")
        port = item.get("port")
        if proto in ("socks4", "socks5") and ip and port:
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="proxifly")


# ---------------------------------------------------------------------------
# Source 4: monosans/proxy-list (real JSON, all countries/protocols in one
# file on GitHub, refreshed hourly; we filter to IR + socks4/socks5 locally)
# ---------------------------------------------------------------------------
def fetch_monosans() -> Iterable[Proxy]:
    url = "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies.json"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    for item in data:
        proto = str(item.get("protocol", "")).lower()
        if proto not in ("socks4", "socks5"):
            continue
        geo = item.get("geolocation") or {}
        country = (geo.get("country") or {}).get("iso_code")
        if country != "IR":
            continue
        ip = item.get("host")
        port = item.get("port")
        if ip and port:
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="monosans")


# ---------------------------------------------------------------------------
# Source 5: Geonix (plain HTML table scrape)
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
# Source 6: Spys.one -- BEST EFFORT ONLY, see module docstring.
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


# ---------------------------------------------------------------------------
# Source 7: jetkai/proxy-list (real JSON, "advanced" view has a country field
# per proxy; hosted on GitHub, refreshed ~hourly)
# ---------------------------------------------------------------------------
def fetch_jetkai() -> Iterable[Proxy]:
    url = (
        "https://raw.githubusercontent.com/jetkai/proxy-list/main/"
        "online-proxies/json/proxies-advanced.json"
    )
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    # Advanced format nests proxies by protocol key; be liberal about shape.
    items = data if isinstance(data, list) else data.get("data", data.get("proxies", []))
    for item in items:
        proto = str(item.get("protocol") or item.get("type") or "").lower()
        if proto not in ("socks4", "socks5"):
            continue
        country = (
            item.get("country_code")
            or item.get("countryCode")
            or (item.get("geolocation") or {}).get("country_code")
            or ""
        )
        if str(country).upper() != "IR":
            continue
        ip = item.get("ip")
        port = item.get("port")
        if ip and port:
            yield Proxy(ip=ip, port=int(port), protocol=proto, source="jetkai")


# ---------------------------------------------------------------------------
# Source 8: ProxyGenerator (proxygenerator1) - per-country text files.
# Pulls both the "Stable" and "MostStable" tiers, socks4 + socks5.
# ---------------------------------------------------------------------------
def fetch_proxygenerator() -> Iterable[Proxy]:
    base = "https://raw.githubusercontent.com/proxygenerator1/ProxyGenerator/main"
    for tier in ("Stable", "MostStable"):
        for proto in ("socks4", "socks5"):
            url = f"{base}/{tier}/country/Iran/{proto}.txt"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                if resp.status_code != 200:
                    continue
            except requests.RequestException:
                continue
            for line in resp.text.splitlines():
                line = line.strip()
                m = re.match(r"^([\d.]+):(\d+)$", line)
                if m:
                    ip, port = m.groups()
                    yield Proxy(
                        ip=ip, port=int(port), protocol=proto,
                        source=f"proxygenerator-{tier.lower()}",
                    )


# ---------------------------------------------------------------------------
# Source 9: ProxyDB.net - HTML scrape, filtered server-side to country=IR.
# ---------------------------------------------------------------------------
def fetch_proxydb() -> Iterable[Proxy]:
    from bs4 import BeautifulSoup

    for proto in ("socks4", "socks5"):
        url = f"http://proxydb.net/?protocol={proto}&country=IR"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.find_all("tr"):
            text = row.get_text(" ", strip=True)
            m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\s*:\s*(\d{2,5})\b", text)
            if m:
                ip, port = m.groups()
                yield Proxy(ip=ip, port=int(port), protocol=proto, source="proxydb")


# ---------------------------------------------------------------------------
# Source 10: Proxifly ALL-COUNTRIES protocol files (as opposed to Source 3,
# which only pulls their pre-filtered IR file). Each entry carries its own
# "country" field, refreshed every 5 minutes -- one of the fastest-updating
# sources here -- so this regularly picks up new IR entries before they'd
# even show up in the per-country file.
# ---------------------------------------------------------------------------
def fetch_proxifly_all() -> Iterable[Proxy]:
    for proto in ("socks4", "socks5"):
        url = f"https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/{proto}/data.json"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for item in data:
            country = str(item.get("country") or item.get("countryCode") or "").upper()
            if country != "IR":
                continue
            ip = item.get("ip")
            port = item.get("port")
            if ip and port:
                yield Proxy(ip=ip, port=int(port), protocol=proto, source="proxifly-all")


# ---------------------------------------------------------------------------
# Source 11: Geo-filter bulk, country-agnostic SOCKS dumps against Iran using
# ip-api.com's free batch geolocation endpoint (up to 100 IPs/request,
# ~15 req/min rate limit on the free tier -- we sleep between batches).
# These dumps are large (thousands of entries each) and refresh often, but
# never publish a per-country file, so this is where most *new* IR proxies
# come from. The more dumps we pool here, the bigger the candidate set the
# geoip pass has to work with -- so this list is intentionally broad:
#   - TheSpeedX/PROXY-List      - daily, one of the most-mirrored SOCKS dumps
#   - hookzof/socks5_list        - refreshed every ~20 min
#   - roosterkid/openproxylist   - refreshed every few hours
#   - prxchk/proxy-list          - refreshed every 10 minutes, large
#   - ShiftyTR/Proxy-List        - refreshed hourly, one of the largest dumps
#   - VPSLabCloud/VPSLab-Free-Proxy-List - refreshed every 15 minutes
#   - clarketm/proxy-list        - refreshed every few hours
# ---------------------------------------------------------------------------
BULK_DUMPS = {
    "socks4": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks4.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt",
        "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/socks4_all.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
        "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5.txt",
        "https://raw.githubusercontent.com/prxchk/proxy-list/main/socks5.txt",
        "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
        "https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/socks5_all.txt",
    ],
    # Format unconfirmed (may include trailing metadata per line) -- the
    # ip:port regex below only pulls the leading match, so extra columns are
    # harmless if present.
    "mixed": [
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    ],
}
GEOIP_BATCH_URL = "http://ip-api.com/batch?fields=query,countryCode"
GEOIP_BATCH_SIZE = 100
GEOIP_SLEEP_S = 4  # stay well under ip-api's free-tier rate limit
MAX_GEOIP_LOOKUPS = int(os.environ.get("MAX_GEOIP_LOOKUPS", "4000"))


def fetch_geo_filtered() -> Iterable[Proxy]:
    # ip -> (protocol, port); first source to mention an IP wins. "mixed"
    # dumps default to socks5 when we can't otherwise tell -- if that's
    # wrong for a given entry it just won't connect, and check_proxies.py
    # will mark it not-alive rather than silently mis-tagging good ones.
    ip_port: dict[str, tuple[str, int]] = {}
    for proto, urls in BULK_DUMPS.items():
        effective_proto = "socks5" if proto == "mixed" else proto
        for url in urls:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                log.warning("bulk dump %s failed: %s", url, exc)
                continue
            for line in resp.text.splitlines():
                line = line.strip()
                m = re.match(r"^([\d.]+):(\d+)", line)
                if not m:
                    continue
                ip, port = m.groups()
                ip_port.setdefault(ip, (effective_proto, int(port)))

    ips = list(ip_port.keys())[:MAX_GEOIP_LOOKUPS]
    log.info(
        "geo_filter: %d unique candidate IPs from bulk dumps, checking %d",
        len(ip_port), len(ips),
    )

    for i in range(0, len(ips), GEOIP_BATCH_SIZE):
        batch = ips[i : i + GEOIP_BATCH_SIZE]
        try:
            resp = requests.post(GEOIP_BATCH_URL, json=batch, timeout=TIMEOUT)
            resp.raise_for_status()
            results = resp.json()
        except requests.RequestException as exc:
            log.warning("geoip batch failed: %s", exc)
            continue
        for item in results:
            if item.get("countryCode") == "IR":
                ip = item.get("query")
                if ip in ip_port:
                    proto, port = ip_port[ip]
                    yield Proxy(ip=ip, port=port, protocol=proto, source="geo-filtered")
        time.sleep(GEOIP_SLEEP_S)


SOURCES = [
    fetch_proxyscrape,
    fetch_geonode,
    fetch_proxifly,
    fetch_monosans,
    fetch_geonix,
    fetch_spys_one,
    fetch_jetkai,
    fetch_proxygenerator,
    fetch_proxydb,
    fetch_proxifly_all,
    fetch_geo_filtered,
]


def fetch_all() -> list[Proxy]:
    """De-duplicates identical (ip, port, protocol) tuples seen from more
    than one source. This is the ONLY thing that removes an entry -- every
    proxy any source reports is kept in the output, working or not."""
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
