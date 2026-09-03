#!/usr/bin/env python3
"""
probe.py -- source verification harness.

Step 1 of the Phase 1 checklist. Run this before writing any parser.

What it does:
  1. Fetches robots.txt for every host and records whether our path is allowed.
  2. Tests every declared feed URL: does it parse, how many entries, how old.
  3. For unverified sites, runs feed autodiscovery on the homepage
     (<link rel="alternate" type="application/rss+xml">) before trying
     guessed paths, so we find the real feed rather than assume one.
  4. For HTML targets, checks that the numbers we need are reachable in the
     page text and fall inside a plausible range.
  5. Tests keyless JSON APIs, including whether Frankfurter covers MUR.

What it never does:
  - Assume a source works.
  - Download or store article bodies. It reads titles, links and dates only.
  - Fail the whole run because one source is down.

Outputs:
  ingest/probe_results.json   machine readable, feeds the next step
  docs/PROBE_REPORT.md        human readable verdict table
  exit code 0 always, unless invoked with --strict

Usage:
  python ingest/probe.py
  python ingest/probe.py --only businessmag,sem-indices
  python ingest/probe.py --strict          exit 1 if any tier-1 source fails
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import yaml
from selectolax.parser import HTMLParser

ROOT = Path(__file__).resolve().parent.parent
TARGETS = Path(__file__).resolve().parent / "probe_targets.yml"
RESULTS_JSON = Path(__file__).resolve().parent / "probe_results.json"
REPORT_MD = ROOT / "docs" / "PROBE_REPORT.md"

# A feed must clear all three to PASS. These thresholds are deliberate:
# a feed with two entries or nothing published in six weeks is not a
# feed we can build a daily habit on.
MIN_ENTRIES = 3
MAX_AGE_DAYS = 45

FEED_CONTENT_TYPES = ("xml", "rss", "atom", "json")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Fetcher:
    """Wraps httpx with our user agent, timeout and a robots.txt cache."""

    def __init__(self, user_agent: str, timeout: int, max_redirects: int):
        self.user_agent = user_agent
        self.client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "application/rss+xml, application/atom+xml, "
                          "application/xml, text/xml, application/json, text/html;q=0.8",
                "Accept-Language": "en,fr;q=0.8",
            },
            timeout=timeout,
            follow_redirects=True,
            max_redirects=max_redirects,
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def get(self, url: str) -> dict:
        """Returns a dict describing the attempt. Never raises."""
        started = time.monotonic()
        try:
            r = self.client.get(url)
            elapsed = round((time.monotonic() - started) * 1000)
            return {
                "ok": True,
                "status": r.status_code,
                "final_url": str(r.url),
                "content_type": r.headers.get("content-type", ""),
                "bytes": len(r.content),
                "ms": elapsed,
                "text": r.text,
                "content": r.content,
                # Cloudflare and similar bot walls are a real risk for SEM.
                "bot_wall": bool(r.headers.get("cf-ray")) and r.status_code in (403, 429, 503),
            }
        except Exception as exc:  # noqa: BLE001 - probe must survive anything
            return {
                "ok": False,
                "status": None,
                "final_url": url,
                "content_type": "",
                "bytes": 0,
                "ms": round((time.monotonic() - started) * 1000),
                "text": "",
                "content": b"",
                "bot_wall": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def robots_allows(self, url: str) -> tuple[bool | None, str]:
        """
        Returns (allowed, note). allowed is None when robots.txt could not be
        read, which we report rather than silently treating as permission.
        """
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            res = self.get(urljoin(origin, "/robots.txt"))
            if res["ok"] and res["status"] == 200 and res["text"].strip():
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(res["text"].splitlines())
                self._robots[origin] = rp
            else:
                self._robots[origin] = None
        rp = self._robots[origin]
        if rp is None:
            return None, "robots.txt unreadable or absent"
        allowed = rp.can_fetch(self.user_agent, url)
        return allowed, "allowed by robots.txt" if allowed else "DISALLOWED by robots.txt"

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# Feed probing
# ---------------------------------------------------------------------------

def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def probe_feed(fetcher: Fetcher, url: str) -> dict:
    """Fetch a URL and report whether it is a usable feed."""
    allowed, robots_note = fetcher.robots_allows(url)
    res = fetcher.get(url)

    out = {
        "url": url,
        "final_url": res["final_url"],
        "status": res["status"],
        "content_type": res["content_type"],
        "bytes": res["bytes"],
        "ms": res["ms"],
        "robots_allowed": allowed,
        "robots_note": robots_note,
        "entries": 0,
        "latest": None,
        "age_days": None,
        "has_titles": False,
        "has_links": False,
        "has_dates": False,
        "sample_titles": [],
        "verdict": "FAIL",
        "note": "",
    }

    if not res["ok"]:
        out["note"] = res.get("error", "request failed")
        return out
    if res["bot_wall"]:
        out["note"] = "blocked by bot protection"
        return out
    if res["status"] != 200:
        out["note"] = f"HTTP {res['status']}"
        return out
    if allowed is False:
        out["verdict"] = "BLOCKED"
        out["note"] = "robots.txt disallows this path; excluded on principle"
        return out

    parsed = feedparser.parse(res["content"])
    entries = parsed.entries or []
    out["entries"] = len(entries)

    if parsed.bozo and not entries:
        ct = res["content_type"].lower()
        if "html" in ct:
            out["note"] = "returned HTML, not a feed"
        else:
            out["note"] = f"unparseable: {parsed.get('bozo_exception', 'unknown')}"
        return out

    if not entries:
        out["note"] = "parsed but contains zero entries"
        return out

    dates = [d for d in (_entry_datetime(e) for e in entries) if d]
    if dates:
        latest = max(dates)
        out["latest"] = latest.isoformat()
        out["age_days"] = (datetime.now(timezone.utc) - latest).days

    out["has_titles"] = all(e.get("title") for e in entries[:5])
    out["has_links"] = all(e.get("link") for e in entries[:5])
    out["has_dates"] = len(dates) >= min(3, len(entries))
    # Titles only. We never capture article bodies, even during probing.
    out["sample_titles"] = [str(e.get("title", ""))[:90] for e in entries[:3]]

    problems = []
    if out["entries"] < MIN_ENTRIES:
        problems.append(f"only {out['entries']} entries")
    if out["age_days"] is None:
        problems.append("no parseable dates")
    elif out["age_days"] > MAX_AGE_DAYS:
        problems.append(f"stale, newest is {out['age_days']} days old")
    if not out["has_links"]:
        problems.append("entries missing links")

    if problems:
        out["verdict"] = "WEAK"
        out["note"] = "; ".join(problems)
    else:
        out["verdict"] = "PASS"
        out["note"] = f"{out['entries']} entries, newest {out['age_days']}d old"
    return out


def discover_feeds(fetcher: Fetcher, homepage: str) -> list[str]:
    """
    Read <link rel="alternate" type="...xml"> from the homepage.
    This finds the real feed instead of guessing at paths.
    """
    res = fetcher.get(homepage)
    if not res["ok"] or res["status"] != 200 or not res["text"]:
        return []
    found: list[str] = []
    try:
        tree = HTMLParser(res["text"])
        for node in tree.css('link[rel="alternate"]'):
            ctype = (node.attributes.get("type") or "").lower()
            href = node.attributes.get("href")
            if href and any(t in ctype for t in FEED_CONTENT_TYPES):
                full = urljoin(res["final_url"], href)
                if full not in found:
                    found.append(full)
    except Exception:  # noqa: BLE001
        return []
    return found


def probe_discover(fetcher: Fetcher, target: dict) -> dict:
    """Autodiscovery first, then declared candidate paths. First PASS wins."""
    # Normalise to a trailing slash and strip the leading slash off candidate
    # paths, so they join relative to the homepage. Without this, a homepage
    # with a path component (https://site.com/news) would have that component
    # silently dropped by urljoin.
    homepage = target["homepage"]
    base = homepage if homepage.endswith("/") else homepage + "/"
    attempts: list[dict] = []

    candidates = discover_feeds(fetcher, homepage)
    discovered_count = len(candidates)
    for path in target.get("candidate_paths", []):
        url = urljoin(base, path.lstrip("/"))
        if url not in candidates:
            candidates.append(url)

    winner = None
    for url in candidates:
        result = probe_feed(fetcher, url)
        result["via"] = "autodiscovery" if candidates.index(url) < discovered_count else "guessed path"
        attempts.append(result)
        if result["verdict"] == "PASS":
            winner = result
            break

    if winner is None:
        weak = [a for a in attempts if a["verdict"] == "WEAK"]
        winner = weak[0] if weak else None

    return {
        "id": target["id"],
        "name": target["name"],
        "region": target.get("region", ""),
        "tier": target.get("tier", 3),
        "homepage": homepage,
        "autodiscovered": discovered_count,
        "attempts": attempts,
        "chosen_url": winner["url"] if winner else None,
        "verdict": winner["verdict"] if winner else "FAIL",
        "note": winner["note"] if winner else f"no working feed among {len(candidates)} candidates",
    }


# ---------------------------------------------------------------------------
# HTML probing
# ---------------------------------------------------------------------------

NUM = r"([0-9]{1,3}(?:[ ,\u00a0][0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"


def extract_near_label(text: str, anchor: str) -> float | None:
    """
    Find the first number that appears shortly after the anchor text.

    Deliberately not a CSS selector. At probe time we do not yet know the DOM,
    and the question we are answering is 'is this number reachable and sane',
    not 'which selector should the parser use'. Selectors get written from
    the probe output afterwards.
    """
    for m in re.finditer(re.escape(anchor), text, flags=re.IGNORECASE):
        window = text[m.end(): m.end() + 160]
        n = re.search(NUM, window)
        if not n:
            continue
        raw = n.group(1).replace(",", "").replace(" ", "").replace("\u00a0", "")
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def probe_html(fetcher: Fetcher, target: dict) -> dict:
    url = target["url"]
    allowed, robots_note = fetcher.robots_allows(url)
    res = fetcher.get(url)

    out = {
        "id": target["id"],
        "name": target["name"],
        "region": target.get("region", ""),
        "tier": target.get("tier", 1),
        "url": url,
        "final_url": res["final_url"],
        "status": res["status"],
        "bytes": res["bytes"],
        "ms": res["ms"],
        "robots_allowed": allowed,
        "robots_note": robots_note,
        "checks": [],
        "verdict": "FAIL",
        "note": "",
    }

    if not res["ok"]:
        out["note"] = res.get("error", "request failed")
        return out
    if res["bot_wall"]:
        out["note"] = "blocked by bot protection; HTML parsing not viable"
        return out
    if res["status"] != 200:
        out["note"] = f"HTTP {res['status']}"
        return out
    if allowed is False:
        out["verdict"] = "BLOCKED"
        out["note"] = "robots.txt disallows this path; excluded on principle"
        return out

    text = HTMLParser(res["text"]).text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    checks = target.get("checks", [])
    if not checks:
        out["verdict"] = "PASS" if res["bytes"] > 2000 else "WEAK"
        out["note"] = f"reachable, {res['bytes']} bytes, no numeric checks declared"
        return out

    failures = 0
    for chk in checks:
        value = extract_near_label(text, chk["anchor"])
        in_range = value is not None and chk["min"] <= value <= chk["max"]
        if not in_range:
            failures += 1
        out["checks"].append({
            "label": chk["label"],
            "anchor": chk["anchor"],
            "value": value,
            "expected_range": [chk["min"], chk["max"]],
            "ok": in_range,
            # Sanity assertion is the point. A parser that silently returns
            # the wrong number is worse than one that crashes.
            "reason": "ok" if in_range else
                      ("anchor not found or no number after it" if value is None
                       else f"value {value} outside plausible range"),
        })

    if failures == 0:
        out["verdict"] = "PASS"
        out["note"] = f"all {len(checks)} values found and in range"
    elif failures < len(checks):
        out["verdict"] = "WEAK"
        out["note"] = f"{failures} of {len(checks)} checks failed"
    else:
        out["note"] = "no declared value could be extracted"
    return out


# ---------------------------------------------------------------------------
# JSON probing
# ---------------------------------------------------------------------------

def probe_json(fetcher: Fetcher, target: dict) -> dict:
    url = target["url"]
    res = fetcher.get(url)
    out = {
        "id": target["id"],
        "name": target["name"],
        "url": url,
        "status": res["status"],
        "ms": res["ms"],
        "verdict": "FAIL",
        "note": "",
        "mur_supported": None,
    }
    if not res["ok"]:
        out["note"] = res.get("error", "request failed")
        return out
    if res["status"] != 200:
        out["note"] = f"HTTP {res['status']}"
        return out
    try:
        data = json.loads(res["text"])
    except json.JSONDecodeError as exc:
        out["note"] = f"invalid JSON: {exc}"
        return out

    flat = json.dumps(data)
    missing = [k for k in target.get("expect_keys", []) if f'"{k}"' not in flat]
    if missing:
        out["verdict"] = "WEAK"
        out["note"] = f"missing expected keys: {', '.join(missing)}"
    else:
        out["verdict"] = "PASS"
        out["note"] = "reachable, valid JSON, expected keys present"

    if target.get("check_mur"):
        out["mur_supported"] = '"MUR"' in flat
        out["note"] += f"; MUR {'IS' if out['mur_supported'] else 'is NOT'} covered"
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

BADGE = {"PASS": "PASS", "WEAK": "WEAK", "FAIL": "FAIL", "BLOCKED": "BLOCKED"}


def render_report(results: dict) -> str:
    ts = results["generated_at"]
    lines = [
        "# Source probe report",
        "",
        f"Generated: {ts}",
        "",
        "Produced by `ingest/probe.py`. Every row is an observation, not an assumption.",
        "Only PASS rows should be promoted into `ingest/sources.yml`.",
        "",
        "Thresholds: a feed PASSES only with at least "
        f"{MIN_ENTRIES} entries, working links, and something published in the "
        f"last {MAX_AGE_DAYS} days.",
        "",
        "## Summary",
        "",
        "| Verdict | Count |",
        "|---|---|",
    ]
    counts: dict[str, int] = {}
    for section in ("feeds", "discover", "html", "json"):
        for r in results[section]:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    for v in ("PASS", "WEAK", "BLOCKED", "FAIL"):
        if v in counts:
            lines.append(f"| {v} | {counts[v]} |")

    lines += ["", "## Declared feeds", "",
              "| Source | Verdict | Entries | Newest | Robots | Note |",
              "|---|---|---|---|---|---|"]
    for r in results["feeds"]:
        age = "n/a" if r["age_days"] is None else f"{r['age_days']}d"
        rb = {True: "ok", False: "disallowed", None: "unknown"}[r["robots_allowed"]]
        lines.append(f"| {r['name']} | {BADGE[r['verdict']]} | {r['entries']} | {age} | {rb} | {r['note']} |")

    lines += ["", "## Mauritian sources (autodiscovery)", "",
              "| Source | Verdict | Chosen URL | Found via | Note |",
              "|---|---|---|---|---|"]
    for r in results["discover"]:
        chosen = r["chosen_url"] or "none"
        via = next((a.get("via", "") for a in r["attempts"] if a["url"] == r["chosen_url"]), "n/a")
        lines.append(f"| {r['name']} | {BADGE[r['verdict']]} | `{chosen}` | {via} | {r['note']} |")

    lines += ["", "### Every attempt", ""]
    for r in results["discover"]:
        lines.append(f"**{r['name']}** — autodiscovery found {r['autodiscovered']} feed link(s)")
        lines.append("")
        lines.append("| URL | Via | Status | Verdict | Note |")
        lines.append("|---|---|---|---|---|")
        for a in r["attempts"]:
            lines.append(f"| `{a['url']}` | {a.get('via','')} | {a['status']} | "
                         f"{BADGE[a['verdict']]} | {a['note']} |")
        lines.append("")

    lines += ["## HTML targets", "",
              "| Source | Verdict | Status | Robots | Note |",
              "|---|---|---|---|---|"]
    for r in results["html"]:
        rb = {True: "ok", False: "disallowed", None: "unknown"}[r["robots_allowed"]]
        lines.append(f"| {r['name']} | {BADGE[r['verdict']]} | {r['status']} | {rb} | {r['note']} |")

    lines += ["", "### Value extraction detail", ""]
    for r in results["html"]:
        if not r["checks"]:
            continue
        lines.append(f"**{r['name']}**")
        lines.append("")
        lines.append("| Value | Anchor | Extracted | Plausible range | OK | Reason |")
        lines.append("|---|---|---|---|---|---|")
        for c in r["checks"]:
            lines.append(f"| {c['label']} | `{c['anchor']}` | {c['value']} | "
                         f"{c['expected_range'][0]}–{c['expected_range'][1]} | "
                         f"{'yes' if c['ok'] else 'no'} | {c['reason']} |")
        lines.append("")

    lines += ["## JSON APIs", "",
              "| Source | Verdict | Status | Note |",
              "|---|---|---|---|"]
    for r in results["json"]:
        lines.append(f"| {r['name']} | {BADGE[r['verdict']]} | {r['status']} | {r['note']} |")

    lines += ["", "## What to do with this", "",
              "1. Promote PASS rows into `ingest/sources.yml`.",
              "2. For WEAK rows, read the note and decide whether the source is worth the maintenance.",
              "3. FAIL and BLOCKED rows stay out. Record them in `status.json` as unavailable, with the reason.",
              "4. For HTML targets that PASS, write the real parser using the extracted values as the assertion baseline.",
              ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Verify every candidate source.")
    ap.add_argument("--only", help="comma separated list of ids to probe")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any tier-1 source fails")
    args = ap.parse_args()

    cfg = yaml.safe_load(TARGETS.read_text(encoding="utf-8"))
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    fetcher = Fetcher(cfg["user_agent"], cfg["timeout_seconds"], cfg["max_redirects"])
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_agent": cfg["user_agent"],
        "feeds": [], "discover": [], "html": [], "json": [],
    }

    def wanted(t): return only is None or t["id"] in only

    print("== declared feeds ==")
    for t in cfg.get("feeds", []):
        if not wanted(t):
            continue
        r = probe_feed(fetcher, t["url"])
        r.update(id=t["id"], name=t["name"], region=t.get("region", ""), tier=t.get("tier", 2))
        results["feeds"].append(r)
        print(f"  {r['verdict']:8} {t['name']:40} {r['note']}")

    print("== mauritian sources (autodiscovery) ==")
    for t in sorted(cfg.get("discover", []), key=lambda x: x.get("priority", 99)):
        if not wanted(t):
            continue
        r = probe_discover(fetcher, t)
        results["discover"].append(r)
        print(f"  {r['verdict']:8} {t['name']:40} {r['note']}")
        for a in r["attempts"]:
            print(f"           - {a['url']} [{a['status']}] {a['verdict']}: {a['note']}")

    print("== html targets ==")
    for t in cfg.get("html_targets", []):
        if not wanted(t):
            continue
        r = probe_html(fetcher, t)
        results["html"].append(r)
        print(f"  {r['verdict']:8} {t['name']:40} {r['note']}")
        for c in r["checks"]:
            print(f"           - {c['label']}: {c['value']} ({c['reason']})")

    print("== json apis ==")
    for t in cfg.get("json_targets", []):
        if not wanted(t):
            continue
        r = probe_json(fetcher, t)
        results["json"].append(r)
        print(f"  {r['verdict']:8} {t['name']:40} {r['note']}")

    fetcher.close()

    RESULTS_JSON.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(render_report(results), encoding="utf-8")
    print(f"\nwrote {RESULTS_JSON}")
    print(f"wrote {REPORT_MD}")

    if args.strict:
        failed = [r for sec in ("feeds", "discover", "html")
                  for r in results[sec]
                  if r.get("tier") == 1 and r["verdict"] in ("FAIL", "BLOCKED")]
        if failed:
            print(f"\nstrict mode: {len(failed)} tier-1 source(s) failed")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

