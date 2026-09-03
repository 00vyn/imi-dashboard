#!/usr/bin/env python3
"""Collect approved sources into static JSON for GitHub Pages.

Only title, link, date, numeric market values, and configured exchange-rate
fields are persisted. Source pages and article bodies are never stored.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx
from selectolax.parser import HTMLParser

import sources


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "data"
USER_AGENT = "IMI-Dashboard/0.1 (public research dashboard)"
NUM = r"([0-9]{1,3}(?:[ ,\u00a0][0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"


class Fetcher:
    """Small HTTP client with a robots.txt cache; never raises to callers."""

    def __init__(self, timeout: int) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT,
                     "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/html;q=0.8"},
            timeout=timeout, follow_redirects=True, max_redirects=5)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def get(self, url: str) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self.client.get(url)
            return {"ok": True, "status": response.status_code, "url": str(response.url),
                    "content": response.content, "text": response.text,
                    "ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:
            return {"ok": False, "status": None, "url": url, "content": b"", "text": "",
                    "ms": round((time.monotonic() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"}

    def robots_allows(self, url: str) -> tuple[bool | None, str]:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            response = self.get(f"{origin}/robots.txt")
            if response["ok"] and response["status"] == 200 and response["text"].strip():
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response["text"].splitlines())
                self._robots[origin] = parser
            else:
                self._robots[origin] = None
        parser = self._robots[origin]
        if parser is None:
            return None, "robots.txt unavailable"
        allowed = parser.can_fetch(USER_AGENT, url)
        return allowed, "allowed by robots.txt" if allowed else "disallowed by robots.txt"

    def close(self) -> None:
        self.client.close()


def extract_near_label(text: str, anchor: str) -> float | None:
    """Return the first numeric value near an approved anchor."""
    for match in re.finditer(re.escape(anchor), text, flags=re.IGNORECASE):
        number = re.search(NUM, text[match.end():match.end() + 160])
        if number:
            try:
                return float(number.group(1).replace(",", "").replace(" ", "").replace("\u00a0", ""))
            except ValueError:
                pass
    return None


def feed_items(content: bytes, item_limit: int) -> list[dict[str, str]]:
    """Convert a feed to the dashboard's minimal headline record shape."""
    parsed = feedparser.parse(content)
    items: list[dict[str, str]] = []
    for entry in (parsed.entries or [])[:item_limit]:
        title, link = str(entry.get("title", "")).strip(), str(entry.get("link", "")).strip()
        if not title or not link:
            continue
        item: dict[str, str] = {"title": title, "link": link}
        published = entry.get("published") or entry.get("updated")
        if published:
            item["published"] = str(published)
        items.append(item)
    return items


def compact_api_payload(endpoint: dict[str, Any], payload: Any) -> Any:
    """Keep only configured currency/rate fields, never a raw API response."""
    expected = set(endpoint["expect_keys"])
    if endpoint["id"] == "currencies":
        if isinstance(payload, dict):
            return {key: payload[key] for key in sorted(expected) if key in payload}
        if isinstance(payload, list):
            compact: dict[str, str] = {}
            for item in payload:
                if isinstance(item, dict):
                    code = item.get("iso_code") or item.get("code")
                    if code in expected:
                        compact[str(code)] = str(item.get("name") or code)
            return compact
        return {}
    quotes = set()
    for value in parse_qs(urlparse(endpoint["url"]).query).get("quotes", []):
        quotes.update(value.split(","))
    if isinstance(payload, list):
        return [{key: item[key] for key in ("date", "base", "quote", "rate") if key in item}
                for item in payload if isinstance(item, dict) and item.get("quote") in quotes]
    if isinstance(payload, dict):
        rates = payload.get("rates", payload)
        if isinstance(rates, dict):
            return {key: rates[key] for key in sorted(quotes) if key in rates}
    return {}


def _base_record(source: dict[str, Any]) -> dict[str, Any]:
    return {key: source[key] for key in ("id", "name", "kind", "region", "tier")}


def _failure(source: dict[str, Any], message: str, status: str = "error") -> dict[str, Any]:
    return {**_base_record(source), "status": status, "message": message}


def collect_feed(fetcher: Fetcher, source: dict[str, Any]) -> dict[str, Any]:
    allowed, robots_note = fetcher.robots_allows(source["url"])
    if allowed is False:
        return _failure(source, robots_note, "skipped")
    response = fetcher.get(source["url"])
    if not response["ok"]:
        return _failure(source, response["error"])
    if response["status"] != 200:
        return _failure(source, f"HTTP {response['status']}")
    items = feed_items(response["content"], source["ingest"]["item_limit"])
    if not items:
        return _failure(source, "feed produced no usable title/link records")
    return {**_base_record(source), "status": "ok", "fetched_url": response["url"],
            "robots": robots_note, "items": items}


def collect_html(fetcher: Fetcher, source: dict[str, Any]) -> dict[str, Any]:
    allowed, robots_note = fetcher.robots_allows(source["url"])
    if allowed is False:
        return _failure(source, robots_note, "skipped")
    response = fetcher.get(source["url"])
    if not response["ok"]:
        return _failure(source, response["error"])
    if response["status"] != 200:
        return _failure(source, f"HTTP {response['status']}")
    checks = source["ingest"]["checks"]
    if not checks:
        return {**_base_record(source), "status": "limited", "fetched_url": response["url"],
                "robots": robots_note, "message": "reachable; no extraction rule is configured yet"}
    text = re.sub(r"\s+", " ", HTMLParser(response["text"]).text(separator=" ", strip=True))
    values: dict[str, float] = {}
    failures: list[str] = []
    for check in checks:
        value = extract_near_label(text, check["anchor"])
        if value is None or not check["min"] <= value <= check["max"]:
            failures.append(check["label"])
        else:
            values[check["label"]] = value
    if failures:
        return _failure(source, f"invalid or missing values: {', '.join(failures)}")
    return {**_base_record(source), "status": "ok", "fetched_url": response["url"],
            "robots": robots_note, "values": values}


def collect_api(fetcher: Fetcher, source: dict[str, Any]) -> dict[str, Any]:
    endpoints: dict[str, Any] = {}
    failures: list[str] = []
    for endpoint in source["ingest"]["endpoints"]:
        response = fetcher.get(endpoint["url"])
        if not response["ok"]:
            failures.append(f"{endpoint['id']}: {response['error']}")
            continue
        if response["status"] != 200:
            failures.append(f"{endpoint['id']}: HTTP {response['status']}")
            continue
        try:
            compact = compact_api_payload(endpoint, json.loads(response["text"]))
        except json.JSONDecodeError:
            failures.append(f"{endpoint['id']}: invalid JSON")
            continue
        if not compact:
            failures.append(f"{endpoint['id']}: required fields missing")
            continue
        endpoints[endpoint["id"]] = compact
    if failures and not endpoints:
        return _failure(source, "; ".join(failures))
    record = {**_base_record(source), "status": "ok" if not failures else "limited", "endpoints": endpoints}
    if failures:
        record["message"] = "; ".join(failures)
    return record


def collect_all(registry: dict[str, Any], fetcher: Fetcher) -> tuple[dict[str, Any], dict[str, Any]]:
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    for source in registry["sources"]:
        record = (collect_feed(fetcher, source) if source["kind"] == "feed" else
                  collect_html(fetcher, source) if source["kind"] == "html" else
                  collect_api(fetcher, source))
        records.append(record)
    latest = {"schema_version": 1, "generated_at": observed_at,
              "sources": [record for record in records if record["status"] in ("ok", "limited")]}
    status = {"schema_version": 1, "generated_at": observed_at,
              "sources": [{key: value for key, value in record.items()
                           if key not in ("items", "values", "endpoints")} for record in records]}
    return latest, status


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect active sources into static dashboard JSON.")
    parser.add_argument("--registry", type=Path, default=sources.DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        registry = sources.load_sources(args.registry)
    except sources.SourceValidationError as exc:
        print(f"Invalid source registry: {exc}", file=sys.stderr)
        return 1
    fetcher = Fetcher(args.timeout)
    try:
        latest, status = collect_all(registry, fetcher)
    finally:
        fetcher.close()
    if args.dry_run:
        print(json.dumps({"latest": latest, "status": status}, indent=2, ensure_ascii=False))
    else:
        write_json(args.output_dir / "latest.json", latest)
        write_json(args.output_dir / "status.json", status)
        ok_count = sum(record["status"] == "ok" for record in status["sources"])
        print(f"Wrote {args.output_dir / 'latest.json'} and {args.output_dir / 'status.json'} ({ok_count} fully updated sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
