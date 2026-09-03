"""Tests for the source registry loader and validator."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

import sources


REGISTRY = Path(__file__).with_name("sources.yml")


class SourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = sources.load_sources(REGISTRY)

    def test_active_registry_is_valid(self) -> None:
        self.assertEqual(sources.validate_sources(self.registry), [])
        self.assertEqual(len(self.registry["sources"]), 11)

    def test_duplicate_ids_are_rejected(self) -> None:
        invalid = deepcopy(self.registry)
        invalid["sources"].append(deepcopy(invalid["sources"][0]))
        self.assertIn("duplicate source id: fed-press", sources.validate_sources(invalid))

    def test_inverted_range_is_rejected(self) -> None:
        invalid = deepcopy(self.registry)
        check = invalid["sources"][5]["ingest"]["checks"][0]
        check["min"], check["max"] = check["max"], check["min"]
        self.assertTrue(any("min must be less than .max" in error
                            for error in sources.validate_sources(invalid)))

    def test_baseline_outside_range_is_rejected(self) -> None:
        invalid = deepcopy(self.registry)
        invalid["sources"][7]["ingest"]["checks"][0]["baseline"] = 99
        self.assertTrue(any("baseline must be inside" in error
                            for error in sources.validate_sources(invalid)))

    def test_missing_url_is_rejected(self) -> None:
        invalid = deepcopy(self.registry)
        del invalid["sources"][0]["url"]
        self.assertTrue(any("sources[1].url must be a non-empty URL" == error
                            for error in sources.validate_sources(invalid)))

    def test_unfilled_placeholder_is_rejected(self) -> None:
        invalid = deepcopy(self.registry)
        invalid["sources"][0]["url"] = "https://example.com/TODO"
        self.assertTrue(any("contains an unfilled placeholder" in error
                            for error in sources.validate_sources(invalid)))


if __name__ == "__main__":
    unittest.main()
