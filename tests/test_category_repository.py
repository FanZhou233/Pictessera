"""自动分类 JSON 仓库测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from photo_manager.infrastructure import CategoryRepository
from photo_manager.services.classification import ClassificationService
from photo_manager.services.classification_rules import MediaRule

from test_classification import make_item


class CategoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 9, 12, 0, 0)
        self.service = ClassificationService([MediaRule()])

    def test_round_trip_restores_stable_relations(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = CategoryRepository(Path(folder))
            item = make_item(
                item_id="runtime-one",
                stable_key="stable-file",
                file_signature="signature",
            )
            snapshot = self.service.classify_batch([item], now=self.now)
            self.assertTrue(repository.save(snapshot))
            loaded = repository.load()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(
                loaded.stable_key_category_ids["stable-file"],
                snapshot.stable_key_category_ids["stable-file"],
            )
            self.assertEqual(
                loaded.item_signatures["stable-file"], "signature"
            )
            rescanned = make_item(
                item_id="runtime-two",
                stable_key="stable-file",
                file_signature="signature",
            )
            restored = self.service.classify_incremental(
                [rescanned], loaded, now=self.now
            )
            self.assertEqual(
                restored.item_category_ids["runtime-two"],
                snapshot.stable_key_category_ids["stable-file"],
            )
            self.assertTrue((Path(folder) / "auto_categories.json").exists())
            self.assertTrue(
                (Path(folder) / "item_category_relations.json").exists()
            )

    def test_corrupt_main_files_fall_back_to_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = CategoryRepository(root)
            first = self.service.classify_batch(
                [make_item(item_id="first")], now=self.now
            )
            second = self.service.classify_batch(
                [make_item(item_id="second")], now=self.now
            )
            self.assertTrue(repository.save(first))
            self.assertTrue(repository.save(second))
            repository.categories_path.write_text("{bad", encoding="utf-8")
            repository.relations_path.write_text("{bad", encoding="utf-8")
            loaded = repository.load()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertIn("stable-first", loaded.stable_key_category_ids)
            self.assertTrue(list(root.glob("*.corrupt.*")))

    def test_version_zero_documents_are_migrated(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = CategoryRepository(Path(folder))
            repository.state_dir.mkdir(parents=True, exist_ok=True)
            repository.categories_path.write_text(
                json.dumps(
                    {
                        "categories": [],
                        "classified_date": "2026-07-09",
                        "rule_versions": {},
                    }
                ),
                encoding="utf-8",
            )
            repository.relations_path.write_text(
                json.dumps({"items": {}, "relations": []}),
                encoding="utf-8",
            )
            loaded = repository.load()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.classified_date, "2026-07-09")


if __name__ == "__main__":
    unittest.main()
