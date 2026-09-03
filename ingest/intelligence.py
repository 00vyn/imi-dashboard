#!/usr/bin/env python3
"""Phase 5 intelligence layer.

Reads the raw collected feed data (docs/data/latest.json and
docs/data/status.json) plus the prior intelligence history
(docs/data/intelligence.json, if one already exists), and produces a
richer static dataset for the dashboard: a consistent item schema,
deduplication by canonical URL, a rule-based category, a mechanical
relevance score with its reasons, an importance label, and structured
learning prompts.

Nothing here claims analyst judgement. Every field comes from a fixed,
readable rule. Category keywords live in CATEGORY_KEYWORDS, scoring
weights in the *_WEIGHT / *_BONUS constants, and the four learning
fields in TEMPLATES. Change the rule, not the output.

Only feed-type sources (items with a title and link) enter this layer.
HTML market-value sources and the exchange-rate API are unaffected and
keep flowing through latest.json / status.json exactly as before.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "docs" / "data"
SCHEMA_VERSION = 1

MAX_AGE_DAYS = 30
MAX_ITEMS = 400

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid",
}

CATEGORY_LABELS = {
    "regulation": "Regulation",
    "corporate_finance_deals": "Corporate Finance / Deals",
    "investment_management": "Investment Management",
    "macro_central_banks": "Macro / Central Banks",
    "markets": "Markets",
    "other": "Other",
}

# Checked in this order; the first category with a keyword hit wins.
CATEGORY_PRIORITY = [
    "regulation", "corporate_finance_deals", "investment_management",
    "macro_central_banks", "markets",
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "regulation": [
        "enforcement action", "consent order", "cease and desist", "civil penalty",
        "regulator", "regulatory", "compliance", "sanction", "sanctions", "fine",
        "penalty", "anti-money laundering", "money laundering", "kyc", "fatf",
        "supervisory", "licence revoked", "license revoked", "settlement",
    ],
    "corporate_finance_deals": [
        "acquisition", "acquires", "acquire ", "merger", "takeover", "buyback",
        "ipo", "initial public offering", "stake", "valuation", "divest",
        "spin-off", "leveraged buyout", "earnings", "quarterly results",
        "revenue guidance", "delisting", "rights issue",
    ],
    "investment_management": [
        "asset management", "fund manager", "fund management", "portfolio manager",
        "hedge fund", "mutual fund", "private equity", "venture capital", "etf",
        "exchange-traded fund", "assets under management", "wealth management",
        "institutional investor", "pension fund", "sovereign wealth", "index fund",
    ],
    "macro_central_banks": [
        "federal reserve", "interest rate", "rate hike", "rate cut", "rate decision",
        "monetary policy", "inflation", "gdp", "central bank", "european central bank",
        "fomc", "discount rate", "repo rate", "quantitative easing", "recession",
        "unemployment rate", "consumer price index", "key rate",
    ],
    "markets": [
        "stocks", "shares", "equities", "bond yield", "yield curve", "dow jones",
        "nasdaq", "s&p 500", "ftse", "semdex", "sem-asi", "market close",
        "trading session", "volatility", "index fell", "index rose",
    ],
}

# Fallback when no keyword matches: what a source is, absent other signal.
SOURCE_DEFAULT_CATEGORY = {
    "fed-press": "macro_central_banks",
    "ecb-press": "macro_central_banks",
    "bom-feed": "macro_central_banks",
    "marketwatch-top": "markets",
    "globenewswire-ma": "corporate_finance_deals",
    "globenewswire-funds": "investment_management",
}

MAURITIUS_KEYWORDS = [
    "mauritius", "mauritian", "port louis", "stock exchange of mauritius",
    "bank of mauritius", "semdex", "sem-asi", "semtri", "sem10", "mcb", "rupee",
    "fsc mauritius",
]

TIER_WEIGHT = {1: 30, 2: 15, 3: 5}
CATEGORY_WEIGHT = {
    "investment_management": 20,
    "macro_central_banks": 20,
    "corporate_finance_deals": 15,
    "regulation": 15,
    "markets": 10,
    "other": 0,
}
MAURITIUS_BONUS = 15
KEYWORD_HIT_WEIGHT = 5
KEYWORD_HIT_CAP = 3  # only the first 3 keyword hits earn points
RECENCY_24H_BONUS = 10
RECENCY_7D_BONUS = 5

IMPORTANCE_THRESHOLDS = [("Critical", 80), ("High", 60), ("Medium", 35)]  # else Low

TEMPLATES: dict[str, dict[str, str]] = {
    "regulation": {
        "why_it_matters": "Categorised as regulation: it changes a rule, or reports an enforcement or compliance action, for the firms involved.",
        "investment_management_angle": "Regulatory shifts change compliance cost and risk for firms and funds you may later analyse.",
        "cfa_connection": "Relates to CFA Ethical and Professional Standards, and to industry regulation more broadly.",
        "suggested_action": "Identify the rule or standard being enforced and check whether it applies to a firm already in your research queue.",
    },
    "corporate_finance_deals": {
        "why_it_matters": "Categorised as corporate finance / deals: it concerns a transaction, acquisition, buyback, listing, or other corporate-finance event.",
        "investment_management_angle": "Deals show how companies are being priced and financed right now, a live input for valuation work.",
        "cfa_connection": "Relates to CFA Corporate Issuers: capital structure, M&A, and corporate actions.",
        "suggested_action": "Log the parties, structure, and stated valuation in your research queue for later comparison.",
    },
    "investment_management": {
        "why_it_matters": "Categorised as investment management: it concerns how funds, portfolios, or asset managers operate or are positioned.",
        "investment_management_angle": "Read it for what it implies about flows, positioning, or strategy among professional investors, not only the headline fact.",
        "cfa_connection": "Relates to CFA Portfolio Management and Wealth Planning.",
        "suggested_action": "Note the fund, manager, or strategy named here and add it to your research queue if you do not already track it.",
    },
    "macro_central_banks": {
        "why_it_matters": "Categorised as macro / central banks: it is a policy release or macro data point that can move rates, currencies, and broad asset prices.",
        "investment_management_angle": "Central-bank language and rate decisions set the discount rate every valuation in your coverage ultimately depends on.",
        "cfa_connection": "Relates to CFA Economics: monetary policy, business cycles, and macroeconomic analysis.",
        "suggested_action": "Note the policy rate or stance mentioned and compare it against the previous reading.",
    },
    "markets": {
        "why_it_matters": "Categorised as markets: it reports a market move or trading update, an index level, a share price, or trading activity.",
        "investment_management_angle": "A short-term move matters less on its own than the reason behind it, so look past the headline number.",
        "cfa_connection": "Relates to CFA Equity Investments and market organisation.",
        "suggested_action": "Check what is driving the move before treating it as a signal worth acting on.",
    },
    "other": {
        "why_it_matters": "No category keyword matched. Kept for context, mainly because of its source.",
        "investment_management_angle": "This bucket is a catch-all, not a curated signal. Skim only if the headline itself looks relevant.",
        "cfa_connection": "No direct CFA curriculum link identified by keyword.",
        "suggested_action": "Skip unless the headline itself catches your attention.",
    },
}

MAURITIUS_SUFFIX = " Also tagged for Mauritius Watch because it concerns Mauritius directly."

_KEYWORD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    category: [re.compile(r"\b" + re.escape(keyword.strip()) + r"\b") for keyword in keywords]
    for category, keywords in CATEGORY_KEYWORDS.items()
}
_MAURITIUS_PATTERNS = [re.compile(r"\b" + re.escape(keyword.strip()) + r"\b") for keyword in MAURITIUS_KEYWORDS]


# --------------------------------------------------------------------------
# URL canonicalisation and stable ids
# --------------------------------------------------------------------------

def canonicalize_url(url: str) -> str:
    """Return a stable form of a URL for deduplication: no tracking params, no fragment."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    kept = sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    )
    query = urlencode(kept)
    return urlunsplit((scheme, netloc, path, query, ""))


def hash_id(canonical_url: str) -> str:
    """A short, stable identifier for an item, derived from its canonical URL."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------

def parse_published(value: Any) -> datetime | None:
    """Parse an RSS/Atom-style date string; return None rather than raising."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Categorisation, Mauritius flag, scoring
# --------------------------------------------------------------------------

def categorize(title: str, source: dict[str, Any]) -> tuple[str, int]:
    """Return (category, keyword_hit_count). Falls back to a source default, then 'other'."""
    for category in CATEGORY_PRIORITY:
        hits = sum(1 for pattern in _KEYWORD_PATTERNS[category] if pattern.search(title.lower()))
        if hits:
            return category, hits
    default = SOURCE_DEFAULT_CATEGORY.get(source.get("id", ""))
    if default:
        return default, 0
    return "other", 0


def is_mauritius(title: str, source: dict[str, Any]) -> bool:
    if source.get("region") == "MU":
        return True
    lowered = title.lower()
    return any(pattern.search(lowered) for pattern in _MAURITIUS_PATTERNS)


def score_item(source: dict[str, Any], category: str, keyword_hits: int,
               mauritius: bool, published: datetime | None, now: datetime) -> tuple[int, list[str]]:
    """A fully mechanical 0-100 score, with the reasons that produced it."""
    reasons: list[str] = []
    total = 0

    tier = source.get("tier", 3)
    tier_points = TIER_WEIGHT.get(tier, 5)
    total += tier_points
    reasons.append(f"Tier {tier} source (+{tier_points})")

    category_points = CATEGORY_WEIGHT.get(category, 0)
    if category_points:
        total += category_points
        reasons.append(f"Category: {CATEGORY_LABELS[category]} (+{category_points})")

    if mauritius:
        total += MAURITIUS_BONUS
        reasons.append(f"Mauritius-linked (+{MAURITIUS_BONUS})")

    if keyword_hits:
        counted_hits = min(keyword_hits, KEYWORD_HIT_CAP)
        keyword_points = counted_hits * KEYWORD_HIT_WEIGHT
        total += keyword_points
        reasons.append(f"Matched {keyword_hits} category keyword(s) (+{keyword_points})")

    if published is not None:
        age = now - published
        if age <= timedelta(hours=24):
            total += RECENCY_24H_BONUS
            reasons.append(f"Published within last 24 hours (+{RECENCY_24H_BONUS})")
        elif age <= timedelta(days=7):
            total += RECENCY_7D_BONUS
            reasons.append(f"Published within last 7 days (+{RECENCY_7D_BONUS})")

    return max(0, min(100, total)), reasons


def importance_label(score: int) -> str:
    for label, threshold in IMPORTANCE_THRESHOLDS:
        if score >= threshold:
            return label
    return "Low"


# --------------------------------------------------------------------------
# Normalisation and merge
# --------------------------------------------------------------------------

def normalize_items(latest: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn every feed item in latest.json into the dashboard's intelligence schema."""
    now = _parse_iso(latest.get("generated_at")) or datetime.now(timezone.utc)
    normalized: dict[str, dict[str, Any]] = {}
    for source in latest.get("sources", []):
        items = source.get("items")
        if not items:
            continue
        for raw in items:
            title = str(raw.get("title", "")).strip()
            link = str(raw.get("link", "")).strip()
            if not title or not link:
                continue
            canonical = canonicalize_url(link)
            item_id = hash_id(canonical)
            published_raw = raw.get("published")
            published = parse_published(published_raw)
            category, keyword_hits = categorize(title, source)
            mauritius = is_mauritius(title, source)
            score, reasons = score_item(source, category, keyword_hits, mauritius, published, now)
            template = TEMPLATES[category]
            why = template["why_it_matters"] + (MAURITIUS_SUFFIX if mauritius else "")
            normalized[item_id] = {
                "id": item_id,
                "source_id": source.get("id"),
                "source_name": source.get("name"),
                "region": source.get("region"),
                "tier": source.get("tier"),
                "title": title,
                "link": link,
                "canonical_link": canonical,
                "published": published.isoformat() if published else None,
                "published_raw": published_raw,
                "category": category,
                "category_label": CATEGORY_LABELS[category],
                "mauritius_watch": mauritius,
                "score": score,
                "score_reasons": reasons,
                "importance": importance_label(score),
                "why_it_matters": why,
                "investment_management_angle": template["investment_management_angle"],
                "cfa_connection": template["cfa_connection"],
                "suggested_action": template["suggested_action"],
            }
    return list(normalized.values())


def _item_age_reference(item: dict[str, Any]) -> datetime | None:
    return _parse_iso(item.get("published")) or _parse_iso(item.get("first_seen"))


def merge_history(new_items: list[dict[str, Any]], prior_items: list[dict[str, Any]],
                   now: datetime) -> list[dict[str, Any]]:
    """Merge freshly normalised items into prior history, then apply retention limits."""
    now_iso = now.isoformat(timespec="seconds")
    by_id: dict[str, dict[str, Any]] = {item["id"]: item for item in prior_items if "id" in item}
    for item in new_items:
        existing = by_id.get(item["id"])
        item["first_seen"] = existing.get("first_seen", now_iso) if existing else now_iso
        item["last_seen"] = now_iso
        by_id[item["id"]] = item

    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    merged = [item for item in by_id.values() if (_item_age_reference(item) or now) >= cutoff]
    merged.sort(key=lambda item: _item_age_reference(item) or now, reverse=True)
    return merged[:MAX_ITEMS]


# --------------------------------------------------------------------------
# Sections and counts
# --------------------------------------------------------------------------

def build_sections(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    def top(predicate, limit: int) -> list[str]:
        candidates = [item for item in items if predicate(item)]
        candidates.sort(key=lambda item: (item["score"], item.get("published") or ""), reverse=True)
        return [item["id"] for item in candidates[:limit]]

    return {
        "top_investment_management": top(lambda item: True, 10),
        "mauritius_watch": top(lambda item: item["mauritius_watch"], 15),
        "global_investment_management": top(
            lambda item: item["category"] == "investment_management" and not item["mauritius_watch"], 12),
        "deals_and_transactions": top(lambda item: item["category"] == "corporate_finance_deals", 12),
        "central_bank_and_macro": top(lambda item: item["category"] == "macro_central_banks", 12),
    }


def build_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    by_importance: dict[str, int] = {}
    for item in items:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
        by_importance[item["importance"]] = by_importance.get(item["importance"], 0) + 1
    return {"total": len(items), "by_category": by_category, "by_importance": by_importance}


# --------------------------------------------------------------------------
# I/O and entry point
# --------------------------------------------------------------------------

def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
        return {}


def build_intelligence(latest: dict[str, Any], status: dict[str, Any],
                        prior_history: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    new_items = normalize_items(latest)
    merged = merge_history(new_items, prior_history.get("items", []), now)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "based_on_collection_at": latest.get("generated_at"),
        "retention": {"max_age_days": MAX_AGE_DAYS, "max_items": MAX_ITEMS},
        "counts": build_counts(merged),
        "sections": build_sections(merged),
        "items": merged,
        "source_health": status.get("sources", []),
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the intelligence layer from collected source data.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    latest = load_json(args.data_dir / "latest.json")
    status = load_json(args.data_dir / "status.json")
    prior_history = load_json(args.data_dir / "intelligence.json")

    result = build_intelligence(latest, status, prior_history)

    if args.dry_run:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        write_json(args.data_dir / "intelligence.json", result)
        print(f"Wrote {args.data_dir / 'intelligence.json'} "
              f"({result['counts']['total']} retained items, "
              f"{len(result['items']) - len(prior_history.get('items', []))} net new since last run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
