# Walmart US Location Crawler

A Python crawler that collects product and category data for configurable US
ZIP codes. It uses `curl_cffi` with independent guest sessions; no browser or
Walmart account is needed to run it.

## How it works

```mermaid
flowchart TD
    A["main.py: load config, connection settings and product IDs"]
    B["For each ZIP: create a guest session and load a product page"]
    C["UpdatePostalCode GraphQL: set the ZIP"]
    D["Verify ZIP, store and SHIPPING intent in the product HTML"]
    E["Read category pages, then products; validate and extract HTML JSON"]
    F["Write journal, CSV files, summary and report"]
    A --> B --> C --> D --> E --> F
```

Product and category data comes from the HTML's `__NEXT_DATA__` JSON.
GraphQL is used for location selection. HTML reads were chosen because they
proved more reliable in sustained tests. Each page must match the selected
ZIP and store before its data is accepted. Failed reads use bounded retries
and session recovery.

## Request flow

| Step | Request | Purpose |
| --- | --- | --- |
| 1 | `GET /ip/{id}` → `301` → `/ip/{slug}/{id}` | Start a guest session; Walmart sets its cookies |
| 2 | `POST /orchestra/home/graphql/UpdatePostalCode/{hash}` | Store the ZIP in the session |
| 3 | `GET /ip/{slug}/{id}` | Verify ZIP, store ID and `SHIPPING` intent on a product page |
| 4 | `GET /browse/.../{category_id}?page=N` | Read category product cards |
| 5 | `GET /ip/{slug}/{id}` | Read product fields |

The location mutation sends only the ZIP:

```json
{"variables": {"postalAddress": {"postalCode": "10001", "zipLocated": false,
  "stateOrProvinceCode": "", "stateOrProvinceName": "", "countryCode": "",
  "addressType": "", "isPoBox": false}}}
```

**Required headers.** The crawler first sent 18 headers copied from the
browser request. Each was removed one at a time; only these five are needed
by `UpdatePostalCode`. `curl_cffi` adds the normal Chrome headers:

| Header | Status when removed |
| --- | --- |
| `content-type: application/json` | 415 |
| `x-apollo-operation-name: UpdatePostalCode` | 418 |
| `x-o-platform: rweb` | 429 |
| `x-o-platform-version` | 429 |
| `x-o-segment: oaoh` | 429 |

**Dynamic values.** No login or auth token is needed. Two values come from
Walmart's web app and change when Walmart deploys: the persisted-query hash
in the mutation URL and `x-o-platform-version`. Both are constants at the top
of [crawler/walmart_us.py](crawler/walmart_us.py) (observed 2026-10-03). If
location setup starts failing, open walmart.com in Chrome DevTools, change the
ZIP, and copy both values from the `UpdatePostalCode` request.

**Cookies and session.** Each ZIP gets its own `curl_cffi` session with an
in-memory cookie jar; cookies are never written by hand, shared or saved.
Cookies seen in captured traffic:

| Cookie | Observed role |
| --- | --- |
| `ACID`, `hasACID` | Guest identity |
| `hasLocData`, `assortmentStoreId` | Location state; `assortmentStoreId` holds the selected store (`3081` default Sacramento, `3520` New York, `3180` Los Angeles) |
| `_px*`, `pxcts` | PerimeterX bot protection |
| `TS*`, `vtc`, `bstc`, `xpa`, `xpm`, `exp-ck` | Load balancing, tracking and experiments |

The store verified first for a ZIP is pinned for the whole run. Setting the
location by writing cookies directly was not tested; the crawler always uses
`UpdatePostalCode` and then checks the page.

**Location checks.** HTTP 200 does not mean the page is for the right ZIP. In
the 10k run, 111 responses had the wrong location: 85 showed the default
Sacramento store (`95829` / `3081`), 23 showed ZIP `07030`, and 3 showed
`33197`. These were rejected and retried; all reads succeeded in the end.

**Why HTML instead of product GraphQL.** In a longer test of the product
GraphQL endpoint, 1,247 of 1,290 attempts returned a CAPTCHA. HTML pages
completed the 10k run without one. See [experiment notes](docs/experiment-log.md).

## Anti-bot findings

- Walmart uses PerimeterX (`_px*` cookies). The crawler treats these as blocks:
  a redirect to `/blocked`, a "Robot or human?" page or `px-captcha` markup,
  JSON with `blockScript`, and HTTP 429. A block starts a new guest session;
  CAPTCHAs are never solved.
- Default `httpx` was blocked on its first request. A browser User-Agent was
  enough in short tests, so TLS fingerprinting alone was not shown to be
  decisive. `curl_cffi` (Chrome profile) was used for all long runs.
- 55 HTTP requests/minute per IP is the highest rate tested over long runs.
  Higher rates were not tested.
- `/ip/` and `/browse/` are allowed by `robots.txt`; `/search` is not, so the
  workload uses only browse pages.

## Failures and retries

A page read has up to three attempts:

| Error | What happens |
| --- | --- |
| Timeout, connection error, HTTP 5xx | Wait 2 s, then 4 s, retry in the same session |
| Wrong ZIP or store | Wait and retry; before the third attempt, set the ZIP again |
| Block, unexpected redirect or invalid page | Start a new guest session and set the ZIP again |
| Three failed session rebuilds | Stop that ZIP only; other ZIPs continue |

Ctrl+C or the time limit stops the run cleanly and still writes the report.

## Quick start

Use Python 3.11+ and run these commands from the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file for local mode:

```dotenv
USE_PROXY=false
PROXIES=
```

Review [config.json](config.json), then start a new crawl:

```bash
python main.py
```

The supplied configuration targets **10,000 reads**: 10 ZIP codes, each with
960 product reads and 40 category pages. The same **960 unique product IDs**
are used across locations, and the 40 category URLs cover different departments.

## Configuration

- `config.json`: edit `locations` to add/remove ZIPs and set their
  `product_requests` and `category_requests`. The category count is the total
  number of pages per ZIP. `categories` holds the URLs; `max_seconds` sets the
  time limit, up to 18,000 seconds.
- `product_ids.json`: the shared product pool referenced by `product_file`.
  It must contain enough unique IDs for the largest per-location product count.
- `.env`: `USE_PROXY=false` runs four workers sharing **55 HTTP requests/minute**.
  `USE_PROXY=true` runs one worker per proxy, each limited to 55 HTTP/minute.
  Set `PROXIES` to comma-separated URLs such as `http://user:pass@host:port`.
  Use sticky proxies; an empty proxy list stops the run before crawling.
  Keep credentials in `.env`, which is ignored by Git.

To use another config, run `python main.py --config path/to/config.json`.
The HTTP limit includes setup, redirects and retries.

## Output fields

- **Products:** `product_price` is the current price and `product_price_2` is
  the crossed-out "was" price (empty if none). `in_stock` is shipping
  availability for that ZIP; pickup stock is not collected. `location_id` is
  the Walmart store ID and `location_name` its city.
- **Categories:** `position` is the 1-based order on the page, including
  sponsored cards (marked in the `sponsored` column); ad placeholders are
  skipped. `brand` is empty when Walmart does not send it: it was present on
  3,265 of 19,254 cards (7 of 40 categories). `brand`, `productBrand` and
  `manufacturerName` were all checked.

## Product discovery and results

The included product pool is ready to use. To sample a new pool from the
configured categories, run `python -m common.discovery`. Keeping discovery
separate lets every ZIP read the same products for comparison.

Each run creates `results/<run_id>/`:

| File | Purpose |
| --- | --- |
| `products.csv` | Product details, prices and shipping availability per ZIP |
| `categories.csv` | Category product cards, brand, price and position |
| `journal.jsonl` | Request results, retries and recovery events |
| `summary.json` / `report.md` | Run totals and per-location metrics |
| `run.log` | Progress and start/end logs |

Follow progress with `tail -f results/<run_id>/run.log` using the printed run ID.
Every crawl starts a new run; there is no automatic resume. To rebuild reports
and CSVs from an existing journal, run `python -m common.journal results/<run_id>`.

## Recorded result

The local run on **2026-10-03** completed **10,000/10,000 reads in 3 h 28 m 9 s**:
9,600 product reads and 400 category pages, with 113 retries, zero final failed
reads and no detected CAPTCHA responses. Setup, redirects and retries brought
the actual HTTP count to 11,117.

**Location differences.** Of the 960 products read at all 10 ZIPs, 129 had a
different price and 119 a different shipping stock status between ZIPs (222 in
total). Comparing only reads taken within 10 minutes of each other still shows
214 products with a difference, so this is not just price changes over time.
For example, a Hanes T-shirt cost $8.67 in Chicago and $3.49 at the other nine
ZIPs.

See the [run report](results/20261003T153841Z-run-467407/report.md) for metrics
and [experiment notes](docs/experiment-log.md) for the investigation history.

## Limitations

- The scale run used a single local IP. Proxy mode was only tested on small runs.
- The 10k run used the code from 2026-10-03. The later change removed the 13
  unneeded location headers and was verified with live location checks.
- The hash and platform version must be updated after Walmart deploys.
- There is no resume; a stopped run starts again from the beginning.

## Checks

```bash
python -m pytest -q
python -m ruff check .
```
