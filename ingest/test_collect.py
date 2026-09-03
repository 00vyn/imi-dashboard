"""Unit tests for static-data shaping; these never contact the internet."""

from __future__ import annotations

import unittest

import collect


class CollectorTests(unittest.TestCase):
    def test_feed_items_keep_only_dashboard_metadata(self) -> None:
        feed = b'''<?xml version="1.0"?><rss version="2.0"><channel><item><title>Policy update</title><link>https://example.test/policy</link><pubDate>Mon, 01 Sep 2026 12:00:00 GMT</pubDate><description>Article body must not be persisted.</description></item></channel></rss>'''
        self.assertEqual(collect.feed_items(feed, 10), [{"title": "Policy update", "link": "https://example.test/policy", "published": "Mon, 01 Sep 2026 12:00:00 GMT"}])

    def test_value_extraction_handles_commas(self) -> None:
        self.assertEqual(collect.extract_near_label("SEMDEX 2,299.81", "SEMDEX"), 2299.81)

    def test_currency_payload_is_limited_to_configured_codes(self) -> None:
        endpoint = {"id": "currencies", "expect_keys": ["USD", "MUR"]}
        payload = {"USD": "US Dollar", "MUR": "Mauritian Rupee", "JPY": "Yen"}
        self.assertEqual(collect.compact_api_payload(endpoint, payload), {"MUR": "Mauritian Rupee", "USD": "US Dollar"})

    def test_rate_payload_is_limited_to_requested_quotes(self) -> None:
        endpoint = {"id": "usd-rates", "expect_keys": [], "url": "https://api.example.test/rates?base=USD&quotes=EUR,GBP"}
        payload = [{"date": "2026-09-03", "base": "USD", "quote": "EUR", "rate": 0.9, "ignored": "x"}, {"date": "2026-09-03", "base": "USD", "quote": "JPY", "rate": 150}]
        self.assertEqual(collect.compact_api_payload(endpoint, payload), [{"date": "2026-09-03", "base": "USD", "quote": "EUR", "rate": 0.9}])

    def test_public_output_excludes_failed_records(self) -> None:
        class FakeFetcher:
            def robots_allows(self, _url):
                return None, "robots.txt unavailable"

            def get(self, url):
                if url.endswith("good.xml"):
                    return {"ok": True, "status": 200, "url": url, "content": b"<rss><channel><item><title>Good</title><link>https://example.test/good</link></item></channel></rss>"}
                return {"ok": True, "status": 503, "url": url, "content": b""}

        registry = {"sources": [
            {"id": "good", "name": "Good", "kind": "feed", "region": "US", "tier": 1,
             "url": "https://example.test/good.xml", "ingest": {"item_limit": 1}},
            {"id": "bad", "name": "Bad", "kind": "feed", "region": "US", "tier": 1,
             "url": "https://example.test/bad.xml", "ingest": {"item_limit": 1}},
        ]}
        latest, status = collect.collect_all(registry, FakeFetcher())
        self.assertEqual([source["id"] for source in latest["sources"]], ["good"])
        self.assertEqual([source["status"] for source in status["sources"]], ["ok", "error"])


if __name__ == "__main__":
    unittest.main()
