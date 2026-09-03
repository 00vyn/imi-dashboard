# Source probe report

Generated: 2026-09-03T13:30:05+00:00

Produced by `ingest/probe.py`. Every row is an observation, not an assumption.
Only PASS rows should be promoted into `ingest/sources.yml`.

Thresholds: a feed PASSES only with at least 3 entries, working links, and something published in the last 45 days.

## Summary

| Verdict | Count |
|---|---|
| PASS | 10 |
| WEAK | 1 |
| BLOCKED | 1 |
| FAIL | 7 |

## Declared feeds

| Source | Verdict | Entries | Newest | Robots | Note |
|---|---|---|---|---|---|
| Federal Reserve press releases | PASS | 20 | 6d | unknown | 20 entries, newest 6d old |
| European Central Bank press | PASS | 15 | 1d | ok | 15 entries, newest 1d old |
| Bank of England news | FAIL | 0 | n/a | ok | HTTP 404 |
| Bank for International Settlements | FAIL | 0 | n/a | ok | HTTP 404 |
| IMF news | FAIL | 0 | n/a | ok | HTTP 403 |
| Reuters business | FAIL | 0 | n/a | unknown | ConnectError: [Errno -2] Name or service not known |
| CNBC finance | BLOCKED | 0 | n/a | disallowed | robots.txt disallows this path; excluded on principle |
| MarketWatch top stories | PASS | 10 | 0d | unknown | 10 entries, newest 0d old |
| SEC EDGAR 8-K filings | FAIL | 0 | n/a | ok | parsed but contains zero entries |

## Mauritian sources (autodiscovery)

| Source | Verdict | Chosen URL | Found via | Note |
|---|---|---|---|---|
| Business Magazine (business-magazine.mu) | FAIL | `none` | n/a | no working feed among 4 candidates |
| ION News | PASS | `https://ionnews.mu/feed/` | guessed path | 12 entries, newest 0d old |
| l'express (lexpress.mu) | FAIL | `none` | n/a | no working feed among 4 candidates |
| Defi Media | WEAK | `https://defimedia.info/rss.xml` | guessed path | no parseable dates |
| Bank of Mauritius (feed autodiscovery) | PASS | `https://www.bom.mu/rss.xml` | guessed path | 10 entries, newest 3d old |

### Every attempt

**Business Magazine (business-magazine.mu)** — autodiscovery found 0 feed link(s)

| URL | Via | Status | Verdict | Note |
|---|---|---|---|---|
| `https://www.business-magazine.mu/feed/` | guessed path | 403 | FAIL | blocked by bot protection |
| `https://www.business-magazine.mu/rubrique/actualites/feed/` | guessed path | 403 | FAIL | blocked by bot protection |
| `https://www.business-magazine.mu/?feed=rss2` | guessed path | 403 | FAIL | blocked by bot protection |
| `https://www.business-magazine.mu/rubrique/archives/finance/feed/` | guessed path | 403 | FAIL | blocked by bot protection |

**ION News** — autodiscovery found 0 feed link(s)

| URL | Via | Status | Verdict | Note |
|---|---|---|---|---|
| `https://ionnews.mu/feed/` | guessed path | 200 | PASS | 12 entries, newest 0d old |

**l'express (lexpress.mu)** — autodiscovery found 0 feed link(s)

| URL | Via | Status | Verdict | Note |
|---|---|---|---|---|
| `https://www.lexpress.mu/feed` | guessed path | None | FAIL | ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010) |
| `https://www.lexpress.mu/rss` | guessed path | None | FAIL | ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010) |
| `https://www.lexpress.mu/rss.xml` | guessed path | None | FAIL | ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010) |
| `https://www.lexpress.mu/feed/rss` | guessed path | None | FAIL | ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010) |

**Defi Media** — autodiscovery found 0 feed link(s)

| URL | Via | Status | Verdict | Note |
|---|---|---|---|---|
| `https://defimedia.info/rss.xml` | guessed path | 200 | WEAK | no parseable dates |
| `https://defimedia.info/feed` | guessed path | 404 | FAIL | HTTP 404 |
| `https://defimedia.info/taxonomy/term/1/feed` | guessed path | 200 | FAIL | parsed but contains zero entries |

**Bank of Mauritius (feed autodiscovery)** — autodiscovery found 0 feed link(s)

| URL | Via | Status | Verdict | Note |
|---|---|---|---|---|
| `https://www.bom.mu/rss.xml` | guessed path | 200 | PASS | 10 entries, newest 3d old |

## HTML targets

| Source | Verdict | Status | Robots | Note |
|---|---|---|---|---|
| SEM indices | PASS | 200 | ok | all 4 values found and in range |
| SEM announcements | PASS | 200 | ok | reachable, 129160 bytes, no numeric checks declared |
| Bank of Mauritius rates | PASS | 200 | ok | all 2 values found and in range |

### Value extraction detail

**SEM indices**

| Value | Anchor | Extracted | Plausible range | OK | Reason |
|---|---|---|---|---|---|
| SEMDEX | `SEMDEX` | 2299.81 | 500–10000 | yes | ok |
| SEM-ASI | `SEM-ASI` | 2050.6 | 200–10000 | yes | ok |
| SEMTRI | `SEMTRI` | 11323.77 | 1000–100000 | yes | ok |
| SEM10 | `SEM10` | 437.65 | 50–5000 | yes | ok |

**Bank of Mauritius rates**

| Value | Anchor | Extracted | Plausible range | OK | Reason |
|---|---|---|---|---|---|
| KeyRate | `Key Rate` | 4.75 | 0–20 | yes | ok |
| OvernightInterbank | `Overnight Interbank Rate` | 3.45 | 0–20 | yes | ok |

## JSON APIs

| Source | Verdict | Status | Note |
|---|---|---|---|
| Frankfurter currency list | PASS | 200 | reachable, valid JSON, expected keys present; MUR IS covered |
| Frankfurter latest rates (USD base) | PASS | 200 | reachable, valid JSON, expected keys present |

## What to do with this

1. Promote PASS rows into `ingest/sources.yml`.
2. For WEAK rows, read the note and decide whether the source is worth the maintenance.
3. FAIL and BLOCKED rows stay out. Record them in `status.json` as unavailable, with the reason.
4. For HTML targets that PASS, write the real parser using the extracted values as the assertion baseline.
