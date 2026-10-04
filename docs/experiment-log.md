# Experiment Notes

Findings from the 1–4 October 2026 tests that shaped the current solution.
For setup and running, see the [README](../README.md).

## Why this flow?

- **Product and category data comes from HTML.** The page's `__NEXT_DATA__`
  JSON is used. The product GraphQL endpoint worked in short tests, but in a
  longer rotation test only 33 of 1,290 HTTP attempts returned a verified
  product; 1,247 were CAPTCHA responses. The HTML flow completed the 10k run.
- **GraphQL is used for location.** A new guest session first opens a product
  page, then sets the ZIP with `UpdatePostalCode`. ZIP, store and `SHIPPING`
  intent are then verified in the product HTML.
- **The client is `curl_cffi`.** Default `httpx` was blocked; with a Chrome or
  Firefox User-Agent it worked in short tests. Long runs were validated with
  `curl_cffi`, so that client was kept. TLS fingerprinting alone was not shown
  to be decisive.
- **Every response's location is checked.** Some HTTP 200 responses carried
  a default location instead of the requested ZIP. Reading again in the same
  session usually fixed this. A wrong location never counts as success; the
  crawler uses bounded retries, a ZIP refresh and, if needed, a new session.
- **The rate limit is 55 HTTP requests/minute.** Setup, redirects and retries
  count toward it. In local mode four workers share the limit. Canonical
  product URLs are shared to avoid repeated redirects; product responses and
  session cookies are not shared.

## UpdatePostalCode header test — 4 October 2026

Each of the 18 manually added headers was removed one at a time, twice.
Removing any of these five made the call fail, so they are kept in the code:

| Header | HTTP status when removed |
| --- | --- |
| `content-type` | 415 |
| `x-apollo-operation-name` | 418 |
| `x-o-platform` | 429 |
| `x-o-platform-version` | 429 |
| `x-o-segment` | 429 |

With the other 13 headers removed together, location verification still
succeeded, including with two new guest sessions. Cookies and the Chrome
headers added by `curl_cffi` were kept; the result applies to this endpoint
and the tested setup.

## Completed 10k run — 3 October 2026

Local connection, four workers and a shared 55 HTTP/minute limit. Each of the
10 ZIPs read the same 960 products and 40 category pages.

| Measure | Result |
| --- | ---: |
| Target / successful reads | 10,000 / 10,000 |
| Product / category page reads | 9,600 / 400 |
| Unique products / category URLs | 960 / 40 |
| Duration | 3 h 28 min 9 s |
| Successful reads per minute | 48.04 |
| Total HTTP attempts | 11,117 |
| Retries / ZIP refreshes / session rebuilds | 113 / 2 / 0 |
| HTTP errors / reads failed at the end | 2 / 0 |
| CAPTCHA / blocked responses | 0 / 0 |

10,000 is the number of product and category reads across all locations,
not the number of unique products. The HTTP count also includes setup,
redirects and retries.

Source: [run report](../results/20261003T153841Z-run-467407/report.md)
and [summary](../results/20261003T153841Z-run-467407/summary.json).
