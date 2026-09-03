"""Unit tests for the Phase 5 intelligence layer; these never contact the internet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import intelligence as intel


def make_source(**overrides):
    source = {"id": "fed-press", "name": "Federal Reserve press releases", "kind": "feed",
              "region": "US", "tier": 1}
    source.update(overrides)
    return source


class CanonicalizeUrlTests(unittest.TestCase):
    def test_strips_tracking_params_and_fragment(self) -> None:
        url = "https://Example.test/Article?utm_source=x&id=7#top"
        self.assertEqual(intel.canonicalize_url(url), "https://example.test/Article?id=7")

    def test_trailing_slash_is_ignored(self) -> None:
        self.assertEqual(intel.canonicalize_url("https://example.test/a/"),
                          intel.canonicalize_url("https://example.test/a"))

    def test_query_param_order_does_not_matter(self) -> None:
        self.assertEqual(intel.canonicalize_url("https://example.test/a?b=2&a=1"),
                          intel.canonicalize_url("https://example.test/a?a=1&b=2"))


class HashIdTests(unittest.TestCase):
    def test_stable_and_distinct(self) -> None:
        one = intel.hash_id("https://example.test/a")
        two = intel.hash_id("https://example.test/a")
        three = intel.hash_id("https://example.test/b")
        self.assertEqual(one, two)
        self.assertNotEqual(one, three)


class CategorizeTests(unittest.TestCase):
    def test_regulation_keyword_wins(self) -> None:
        category, hits = intel.categorize(
            "Federal Reserve Board issues enforcement action with former employee",
            make_source())
        self.assertEqual(category, "regulation")
        self.assertGreaterEqual(hits, 1)

    def test_deals_keyword_wins(self) -> None:
        category, _ = intel.categorize("Company announces acquisition of rival", make_source())
        self.assertEqual(category, "corporate_finance_deals")

    def test_falls_back_to_source_default(self) -> None:
        category, hits = intel.categorize(
            "Klaus Muller to join the Governing Council in October",
            make_source(id="ecb-press", region="EU"))
        self.assertEqual(category, "macro_central_banks")
        self.assertEqual(hits, 0)

    def test_no_default_falls_back_to_other(self) -> None:
        category, hits = intel.categorize(
            "Riviere-Noire faces reduced water supply", make_source(id="ionnews", region="MU"))
        self.assertEqual(category, "other")
        self.assertEqual(hits, 0)

    def test_globenewswire_ma_defaults_to_deals(self) -> None:
        # Real-world case: UK Takeover Code dealing disclosures carry no
        # "acquisition"/"merger" keyword but are exactly what this feed is for.
        category, hits = intel.categorize(
            "Form 8.3 - Tate & Lyle plc", make_source(id="globenewswire-ma", region="GLOBAL"))
        self.assertEqual(category, "corporate_finance_deals")
        self.assertEqual(hits, 0)

    def test_globenewswire_funds_defaults_to_investment_management(self) -> None:
        category, hits = intel.categorize(
            "Westwood Announces Monthly Income Distributions",
            make_source(id="globenewswire-funds", region="GLOBAL"))
        self.assertEqual(category, "investment_management")
        self.assertEqual(hits, 0)


class MauritiusTests(unittest.TestCase):
    def test_region_mu_is_flagged(self) -> None:
        self.assertTrue(intel.is_mauritius("Anything at all", make_source(region="MU")))

    def test_keyword_outside_mu_region_is_flagged(self) -> None:
        self.assertTrue(intel.is_mauritius("Bank of Mauritius raises key rate", make_source(region="US")))

    def test_no_signal_is_not_flagged(self) -> None:
        self.assertFalse(intel.is_mauritius("Federal Reserve holds rates steady", make_source(region="US")))


class ScoreTests(unittest.TestCase):
    def test_score_is_bounded(self) -> None:
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        score, reasons = intel.score_item(make_source(tier=1), "investment_management", 5, True, now, now)
        self.assertLessEqual(score, 100)
        self.assertGreaterEqual(score, 0)
        self.assertTrue(reasons)

    def test_higher_tier_scores_higher_all_else_equal(self) -> None:
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        tier1, _ = intel.score_item(make_source(tier=1), "markets", 0, False, None, now)
        tier3, _ = intel.score_item(make_source(tier=3), "markets", 0, False, None, now)
        self.assertGreater(tier1, tier3)

    def test_recency_bonus_applies_within_24h(self) -> None:
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        fresh, reasons_fresh = intel.score_item(make_source(), "other", 0, False, now - timedelta(hours=2), now)
        stale, reasons_stale = intel.score_item(make_source(), "other", 0, False, now - timedelta(days=20), now)
        self.assertGreater(fresh, stale)
        self.assertTrue(any("24 hours" in reason for reason in reasons_fresh))


class ImportanceLabelTests(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(intel.importance_label(85), "Critical")
        self.assertEqual(intel.importance_label(80), "Critical")
        self.assertEqual(intel.importance_label(79), "High")
        self.assertEqual(intel.importance_label(60), "High")
        self.assertEqual(intel.importance_label(59), "Medium")
        self.assertEqual(intel.importance_label(35), "Medium")
        self.assertEqual(intel.importance_label(34), "Low")
        self.assertEqual(intel.importance_label(0), "Low")


class NormalizeItemsTests(unittest.TestCase):
    def test_produces_expected_schema(self) -> None:
        latest = {
            "generated_at": "2026-09-03T12:00:00+00:00",
            "sources": [
                {"id": "fed-press", "name": "Federal Reserve press releases", "region": "US", "tier": 1,
                 "items": [{"title": "Federal Reserve Board issues enforcement action",
                            "link": "https://example.test/a?utm_source=x",
                            "published": "Thu, 03 Sep 2026 10:00:00 GMT"}]},
                {"id": "sem-indices", "name": "SEM indices", "region": "MU", "tier": 1},  # no items key: HTML source
            ],
        }
        items = intel.normalize_items(latest)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["category"], "regulation")
        self.assertEqual(item["canonical_link"], "https://example.test/a")
        self.assertIn("score", item)
        self.assertIn("score_reasons", item)
        self.assertIn("importance", item)
        self.assertIn("why_it_matters", item)
        self.assertIn("investment_management_angle", item)
        self.assertIn("cfa_connection", item)
        self.assertIn("suggested_action", item)

    def test_items_without_title_or_link_are_skipped(self) -> None:
        latest = {"generated_at": "2026-09-03T12:00:00+00:00",
                  "sources": [{"id": "fed-press", "name": "Fed", "region": "US", "tier": 1,
                               "items": [{"title": "", "link": "https://example.test/a"},
                                         {"title": "Ok", "link": ""}]}]}
        self.assertEqual(intel.normalize_items(latest), [])


class MergeHistoryTests(unittest.TestCase):
    def test_first_seen_is_preserved_across_runs(self) -> None:
        now1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
        now2 = datetime(2026, 9, 2, tzinfo=timezone.utc)
        item = {"id": "abc", "published": None}
        first_run = intel.merge_history([dict(item)], [], now1)
        self.assertEqual(first_run[0]["first_seen"], now1.isoformat(timespec="seconds"))
        second_run = intel.merge_history([dict(item)], first_run, now2)
        self.assertEqual(second_run[0]["first_seen"], now1.isoformat(timespec="seconds"))
        self.assertEqual(second_run[0]["last_seen"], now2.isoformat(timespec="seconds"))

    def test_items_older_than_max_age_are_dropped(self) -> None:
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        old_published = (now - timedelta(days=45)).isoformat()
        recent_published = (now - timedelta(days=1)).isoformat()
        prior = [
            {"id": "old", "published": old_published, "first_seen": old_published, "last_seen": old_published},
            {"id": "recent", "published": recent_published, "first_seen": recent_published,
             "last_seen": recent_published},
        ]
        merged = intel.merge_history([], prior, now)
        self.assertEqual([item["id"] for item in merged], ["recent"])

    def test_item_count_is_capped(self) -> None:
        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        prior = [{"id": f"item-{i}", "published": (now - timedelta(hours=i)).isoformat(),
                  "first_seen": now.isoformat(), "last_seen": now.isoformat()}
                 for i in range(intel.MAX_ITEMS + 50)]
        merged = intel.merge_history([], prior, now)
        self.assertEqual(len(merged), intel.MAX_ITEMS)


class BuildSectionsTests(unittest.TestCase):
    def test_sections_respect_filters(self) -> None:
        items = [
            {"id": "a", "score": 90, "published": "2026-09-03", "category": "investment_management",
             "mauritius_watch": False},
            {"id": "b", "score": 80, "published": "2026-09-03", "category": "investment_management",
             "mauritius_watch": True},
            {"id": "c", "score": 70, "published": "2026-09-03", "category": "corporate_finance_deals",
             "mauritius_watch": False},
            {"id": "d", "score": 60, "published": "2026-09-03", "category": "macro_central_banks",
             "mauritius_watch": False},
        ]
        sections = intel.build_sections(items)
        self.assertEqual(sections["mauritius_watch"], ["b"])
        self.assertEqual(sections["global_investment_management"], ["a"])
        self.assertEqual(sections["deals_and_transactions"], ["c"])
        self.assertEqual(sections["central_bank_and_macro"], ["d"])
        self.assertEqual(sections["top_investment_management"][0], "a")


class BuildIntelligenceEndToEndTests(unittest.TestCase):
    def test_end_to_end_with_no_prior_history(self) -> None:
        latest = {"generated_at": "2026-09-03T12:00:00+00:00",
                  "sources": [{"id": "fed-press", "name": "Fed", "region": "US", "tier": 1,
                               "items": [{"title": "Federal Reserve announces acquisition review",
                                          "link": "https://example.test/x",
                                          "published": "Thu, 03 Sep 2026 10:00:00 GMT"}]}]}
        status = {"sources": [{"id": "fed-press", "status": "ok"}]}
        result = intel.build_intelligence(latest, status, {})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertIn("top_investment_management", result["sections"])
        self.assertEqual(result["source_health"], [{"id": "fed-press", "status": "ok"}])


if __name__ == "__main__":
    unittest.main()
