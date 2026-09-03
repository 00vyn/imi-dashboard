#!/usr/bin/env python3
"""Load and validate the active Step 2 source registry.

Usage:
    python ingest/sources.py --validate
    python ingest/sources.py --list
    python ingest/sources.py --show bom-rates
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = Path(__file__).with_name("sources.yml")
VALID_KINDS = {"feed", "html", "api"}
PLACEHOLDER_RE = re.compile(r"\b(todo|tbd|your[-_ ]|example\.com)\b", re.IGNORECASE)


class SourceValidationError(ValueError):
    """Raised when the source registry is unsafe or structurally invalid."""


def load_sources(path: Path | str = DEFAULT_REGISTRY) -> dict[str, Any]:
    """Read, validate, and return a source registry."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceValidationError(f"could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SourceValidationError(f"invalid YAML in {path}: {exc}") from exc

    errors = validate_sources(data)
    if errors:
        raise SourceValidationError("\n".join(errors))
    return data


def validate_sources(registry: Any) -> list[str]:
    """Return every validation error without making network requests."""
    errors: list[str] = []
    if not isinstance(registry, dict):
        return ["registry must be a YAML mapping"]
    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    sources = registry.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["sources must be a non-empty list"]

    ids: set[str] = set()
    for index, source in enumerate(sources, start=1):
        label = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label} must be a mapping")
            continue

        source_id = source.get("id")
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source_id):
            errors.append(f"{label}.id must be lowercase kebab-case")
            source_id = label
        elif source_id in ids:
            errors.append(f"duplicate source id: {source_id}")
        else:
            ids.add(source_id)

        _required_string(source, "name", label, errors)
        _required_string(source, "region", label, errors)
        kind = source.get("kind")
        if kind not in VALID_KINDS:
            errors.append(f"{label}.kind must be one of: {', '.join(sorted(VALID_KINDS))}")
        tier = source.get("tier")
        if not isinstance(tier, int) or tier not in (1, 2, 3):
            errors.append(f"{label}.tier must be 1, 2, or 3")

        ingest = source.get("ingest")
        if not isinstance(ingest, dict):
            errors.append(f"{label}.ingest must be a mapping")
            continue
        expected_mode = {"feed": "feed", "html": "html", "api": "json"}.get(kind)
        if ingest.get("mode") != expected_mode:
            errors.append(f"{label}.ingest.mode must be {expected_mode!r}")

        if kind == "feed":
            _validate_url(source.get("url"), f"{label}.url", errors)
            item_limit = ingest.get("item_limit")
            if not isinstance(item_limit, int) or item_limit < 1:
                errors.append(f"{label}.ingest.item_limit must be a positive integer")
        elif kind == "html":
            _validate_url(source.get("url"), f"{label}.url", errors)
            _validate_checks(ingest.get("checks"), f"{label}.ingest.checks", errors)
        elif kind == "api":
            _validate_endpoints(ingest.get("endpoints"), f"{label}.ingest.endpoints", errors)

    return errors


def _required_string(source: dict[str, Any], field: str, label: str, errors: list[str]) -> None:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")
    elif PLACEHOLDER_RE.search(value):
        errors.append(f"{label}.{field} contains an unfilled placeholder")


def _validate_url(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty URL")
        return
    if PLACEHOLDER_RE.search(value):
        errors.append(f"{label} contains an unfilled placeholder")
        return
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        errors.append(f"{label} must be an absolute HTTPS URL")


def _validate_checks(checks: Any, label: str, errors: list[str]) -> None:
    if not isinstance(checks, list):
        errors.append(f"{label} must be a list")
        return
    seen_labels: set[str] = set()
    for index, check in enumerate(checks, start=1):
        check_label = f"{label}[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{check_label} must be a mapping")
            continue
        item_name = check.get("label")
        if not isinstance(item_name, str) or not item_name.strip():
            errors.append(f"{check_label}.label must be a non-empty string")
        elif item_name in seen_labels:
            errors.append(f"duplicate check label: {item_name}")
        else:
            seen_labels.add(item_name)
        _required_string(check, "anchor", check_label, errors)

        minimum, maximum, baseline = check.get("min"), check.get("max"), check.get("baseline")
        if not _is_number(minimum) or not _is_number(maximum):
            errors.append(f"{check_label}.min and .max must be numbers")
        elif minimum >= maximum:
            errors.append(f"{check_label}.min must be less than .max")
        if not _is_number(baseline):
            errors.append(f"{check_label}.baseline must be a number")
        elif _is_number(minimum) and _is_number(maximum) and not minimum <= baseline <= maximum:
            errors.append(f"{check_label}.baseline must be inside its declared range")


def _validate_endpoints(endpoints: Any, label: str, errors: list[str]) -> None:
    if not isinstance(endpoints, list) or not endpoints:
        errors.append(f"{label} must be a non-empty list")
        return
    ids: set[str] = set()
    for index, endpoint in enumerate(endpoints, start=1):
        endpoint_label = f"{label}[{index}]"
        if not isinstance(endpoint, dict):
            errors.append(f"{endpoint_label} must be a mapping")
            continue
        endpoint_id = endpoint.get("id")
        if not isinstance(endpoint_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", endpoint_id):
            errors.append(f"{endpoint_label}.id must be lowercase kebab-case")
        elif endpoint_id in ids:
            errors.append(f"duplicate endpoint id: {endpoint_id}")
        else:
            ids.add(endpoint_id)
        _validate_url(endpoint.get("url"), f"{endpoint_label}.url", errors)
        expect_keys = endpoint.get("expect_keys")
        if not isinstance(expect_keys, list) or not all(isinstance(key, str) and key for key in expect_keys):
            errors.append(f"{endpoint_label}.expect_keys must be a list of non-empty strings")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the active source registry.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--validate", action="store_true", help="validate the registry")
    actions.add_argument("--list", action="store_true", help="list active sources")
    actions.add_argument("--show", metavar="SOURCE_ID", help="show one active source")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="registry path")
    args = parser.parse_args()

    try:
        registry = load_sources(args.registry)
    except SourceValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    sources = registry["sources"]
    if args.validate:
        print(f"VALID: {len(sources)} active sources")
    elif args.list:
        for source in sources:
            print(f"{source['id']:20} {source['kind']:5} {source['region']:6} {source['name']}")
    else:
        source = next((item for item in sources if item["id"] == args.show), None)
        if source is None:
            print(f"Unknown source id: {args.show}", file=sys.stderr)
            return 2
        print(yaml.safe_dump(source, sort_keys=False, allow_unicode=True).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
