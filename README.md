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

See the [run report](results/20261003T153841Z-run-467407/report.md) for metrics
and [experiment notes](docs/experiment-log.md) for the investigation history.

## Checks

```bash
python -m pytest -q
python -m ruff check .
```
