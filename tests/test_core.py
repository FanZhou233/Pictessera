"""Fast tests for framework-independent application code."""

import unittest
from datetime import datetime
from pathlib import Path

from photo_manager.domain import PhotoItem
from photo_manager.services.search import (
    searchable_fields_for_item,
    wildcard_patterns,
    wildcard_query_matches,
)


class SearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.item = PhotoItem(
            item_id="one",
            display_name="IMG_0001.HEIC",
            files=[Path("IMG_0001.HEIC"), Path("IMG_0001.MOV")],
            size_bytes=42,
            representative_image=Path("IMG_0001.HEIC"),
            is_live=True,
            item_type="Live Photo",
            shot_time=datetime(2026, 1, 2, 3, 4, 5),
            time_source="EXIF",
            source_folder=Path("photos"),
        )

    def test_plain_text_becomes_contains_pattern(self):
        self.assertEqual(wildcard_patterns("IMG_"), ["*img_*"])

    def test_semicolon_separates_patterns(self):
        self.assertEqual(wildcard_patterns("*.heic;*.jpg"), ["*.heic", "*.jpg"])

    def test_item_fields_are_searchable(self):
        fields = searchable_fields_for_item(self.item)
        self.assertTrue(wildcard_query_matches("*.mov", fields))
        self.assertTrue(wildcard_query_matches("2026-01-02", fields))
        self.assertFalse(wildcard_query_matches("vacation", fields))


if __name__ == "__main__":
    unittest.main()
