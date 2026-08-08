# ir-socks-proxies

Auto-refreshing feed of free, publicly-listed SOCKS4/SOCKS5 proxies geolocated
to Iran (IR), aggregated from several free proxy-list sites, **liveness-checked**
before publishing, and served as a stable JSON "subscription link" via
GitHub Pages — no server to run, no API keys.

> ⚠️ **Read this before relying on it.** These are open/anonymous proxies run
> by unknown third parties, scraped from public lists. They can be slow,
> short-lived, logged, or actively malicious (traffic injection, MITM). Do not
> send credentials, personal data, or anything sensitive through them.
> For anything that actually matters for safety in a censored/monitored
> environment, use audited, purpose-built circumvention tools (Tor, Snowflake,
> a trusted VPN) instead of random free proxies.

## What it does

1. GitHub Actions runs on a schedule (default: every 3 hours).
2. `scripts/fetch_proxies.py` pulls candidate IR SOCKS proxies from:
   - **ProxyScrape** — real API, most reliable source.
   - **Geonode** — real JSON API, best-effort (endpoint has moved around).
   - **Geonix** (`free.geonix.com`) — HTML table scrape.
   - **Spys.one** — HTML scrape, **best-effort only**. Spys.one encodes port
     numbers with page-specific JavaScript, so a plain scraper can only ever
     recover the subset of rows that happen to render in cleartext. Expect
     this source to return little or nothing most runs — that's the site's
     obfuscation working as designed, not a bug here. If you want full
     spys.one coverage you'd need a headless browser (Selenium/Playwright),
     which is deliberately not included to keep the Action fast and simple.
3. `scripts/check_proxies.py` opens a real connection through every
   candidate proxy and drops anything that doesn't actually work. Survivors
   get a measured latency.
4. The workflow commits the results to `docs/`, which GitHub Pages serves.

## One-time setup

1. Create a new GitHub repo and push this project to it (`main` branch).
2. **Settings → Pages** → Source: "Deploy from a branch" → Branch: `main`,
   folder: `/docs` → Save.
3. **Settings → Actions → General → Workflow permissions** → set to
   "Read and write permissions" (needed so the workflow can commit the
   refreshed proxy list back to the repo).
4. Optionally run the workflow once by hand: **Actions → Update Iran SOCKS
   proxy list → Run workflow**, instead of waiting for the first cron tick.
5. Your subscription links, once Pages finishes deploying:
   - `https://YOUR_USERNAME.github.io/YOUR_REPO/proxies.json`
   - `https://YOUR_USERNAME.github.io/YOUR_REPO/socks.txt`
   - `https://YOUR_USERNAME.github.io/YOUR_REPO/meta.json` (last-updated time, counts)

## Using it from Python

```python
import random
import requests

FEED = "https://YOUR_USERNAME.github.io/YOUR_REPO/proxies.json"

proxies = requests.get(FEED, timeout=10).json()
p = random.choice(proxies)
proxy_url = f"{p['protocol']}://{p['ip']}:{p['port']}"

session = requests.Session()
session.proxies = {"http": proxy_url, "https": proxy_url}

r = session.get("https://httpbin.org/ip", timeout=8)
print(r.json())
```

Requires `pip install "requests[socks]"` (pulls in PySocks, which `requests`
needs to actually speak the socks4/socks5 protocol).

Each entry in `proxies.json` looks like:

```json
{
  "ip": "1.2.3.4",
  "port": 1080,
  "protocol": "socks5",
  "country": "IR",
  "source": "proxyscrape",
  "latency_ms": 842,
  "checked_at": "2026-08-08T12:00:00+00:00"
}
```

## Running locally

```bash
pip install -r requirements.txt
cd scripts
python main.py               # fetch + check + write docs/*.json
SKIP_CHECK=1 python main.py  # skip the liveness check (faster, includes dead IPs)
```

## Tuning

- **Schedule**: edit the cron in `.github/workflows/update.yml`. Free
  proxies churn fast; every 3 hours is a reasonable default without burning
  your Actions minutes budget. Public repos get unlimited Actions minutes.
- **Check strictness**: `scripts/check_proxies.py` — `TIMEOUT`, `CHECK_URL`,
  `MAX_WORKERS`.
- **Add a source**: add a `fetch_x()` generator function to
  `scripts/fetch_proxies.py` yielding `Proxy(...)` objects, and add it to the
  `SOURCES` list at the bottom of the file.

## Why not just scrape spys.one properly?

Spys.one's port obfuscation changes its JS encoding table per page load and
isn't documented — the reliable way to defeat it is executing the page's JS
in a real browser (Selenium/Playwright) and reading the rendered DOM. That's
a much heavier, slower dependency for a scheduled Action (browser binaries,
longer run times, more flaky in CI). This project intentionally trades some
coverage for staying fast, dependency-light, and low-maintenance. If you want
it anyway, it's a natural place to extend `fetch_spys_one()`.

## License

MIT. Do whatever you want with it; no warranty on proxy quality or uptime,
see the disclaimer above.
