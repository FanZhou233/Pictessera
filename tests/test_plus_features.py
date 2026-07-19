"""P2 Plus/AI 轻量模块测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from photo_manager.domain import (
    CustomRuleDefinition,
    ImageFeatureRecord,
    UserItemMetadata,
)
from photo_manager.infrastructure import CategoryRepository, PhotoManagerDatabase
from photo_manager.services.classification import ClassificationService
from photo_manager.services.classification_rules import PlusAIRule
from photo_manager.services.plus_analysis import (
    PlusFeatureAnalyzer,
    clone_items_for_classification,
)

from test_classification import make_item


class PlusFeatureTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 9, 12, 0, 0)
        self.analyzer = PlusFeatureAnalyzer()
        self.service = ClassificationService([PlusAIRule()])

    def test_plus_analysis_feeds_all_p2_categories(self):
        base_time = datetime(2026, 7, 8, 10, 0, 0)
        burst_items = [
            make_item(
                item_id=f"burst-{index}",
                stable_key=f"burst-{index}",
                file_signature=f"sig-burst-{index}",
                shot_time=base_time + timedelta(seconds=index),
            )
            for index in range(3)
        ]
        screenshot = make_item(
            item_id="shot",
            suffix=".png",
            stable_key="shot",
            file_signature="sig-shot",
            shot_time=base_time + timedelta(minutes=3),
        )
        screenshot.display_name = "Screenshot 2026-07-08.png"
        screenshot.files = [Path("Screenshot 2026-07-08.png")]
        screenshot.image_width = 1170
        screenshot.image_height = 2532

        duplicate_a = make_item(
            item_id="duplicate-a",
            stable_key="duplicate-a",
            file_signature="sig-duplicate-a",
            shot_time=base_time + timedelta(minutes=4),
        )
        duplicate_b = make_item(
            item_id="duplicate-b",
            stable_key="duplicate-b",
            file_signature="sig-duplicate-b",
            shot_time=base_time + timedelta(minutes=5),
        )
        similar_a = make_item(
            item_id="similar-a",
            stable_key="similar-a",
            file_signature="sig-similar-a",
            shot_time=base_time + timedelta(minutes=6),
        )
        similar_b = make_item(
            item_id="similar-b",
            stable_key="similar-b",
            file_signature="sig-similar-b",
            shot_time=base_time + timedelta(minutes=7),
        )
        items = [
            *burst_items,
            screenshot,
            duplicate_a,
            duplicate_b,
            similar_a,
            similar_b,
        ]
        features = {
            "duplicate-a": ImageFeatureRecord(
                "duplicate-a",
                "sig-duplicate-a",
                perceptual_hash="aaaaaaaaaaaaaaaa",
                blur_score=20.0,
                is_blurry=True,
                content_labels=["文档倾向"],
            ),
            "duplicate-b": ImageFeatureRecord(
                "duplicate-b",
                "sig-duplicate-b",
                perceptual_hash="aaaaaaaaaaaaaaaa",
                blur_score=160.0,
            ),
            "similar-a": ImageFeatureRecord(
                "similar-a",
                "sig-similar-a",
                perceptual_hash="0000000000000000",
                blur_score=160.0,
            ),
            "similar-b": ImageFeatureRecord(
                "similar-b",
                "sig-similar-b",
                perceptual_hash="000000000000000f",
                blur_score=160.0,
            ),
        }
        metadata = {
            "duplicate-a": UserItemMetadata(
                "duplicate-a",
                favorite=True,
                rating=5,
                tags=["旅行", "精选"],
                face_clusters=["人物 A"],
                content_labels=["食物"],
                custom_categories=["客户交付"],
            )
        }

        self.analyzer.enrich_items(
            items,
            user_metadata=metadata,
            feature_cache=features,
            custom_rules=[
                CustomRuleDefinition(
                    "screenshots",
                    "截图自定义规则",
                    "is_screenshot",
                    "equals",
                    "true",
                    "自定义截图",
                )
            ],
        )
        snapshot = self.service.classify_batch(items, now=self.now)

        self.assertIn("event", snapshot.categories)
        self.assertGreaterEqual(len(snapshot.item_ids_for("event")), len(items))
        self.assertIn("burst:all", snapshot.item_category_ids["burst-0"])
        self.assertIn("screenshot:detected", snapshot.item_category_ids["shot"])
        self.assertIn("duplicate:all", snapshot.item_category_ids["duplicate-a"])
        self.assertIn("duplicate:all", snapshot.item_category_ids["duplicate-b"])
        self.assertIn("similar:all", snapshot.item_category_ids["similar-a"])
        self.assertIn("similar:all", snapshot.item_category_ids["similar-b"])
        self.assertIn("quality:blurry", snapshot.item_category_ids["duplicate-a"])
        self.assertIn("user:favorite", snapshot.item_category_ids["duplicate-a"])
        self.assertIn("user:rating:5", snapshot.item_category_ids["duplicate-a"])
        self.assertTrue(
            any(category_id.startswith("face:cluster:") for category_id in snapshot.item_category_ids["duplicate-a"])
        )
        self.assertTrue(
            any(category_id.startswith("content:label:") for category_id in snapshot.item_category_ids["duplicate-a"])
        )
        self.assertTrue(
            any(category_id.startswith("custom:category:") for category_id in snapshot.item_category_ids["duplicate-a"])
        )
        self.assertTrue(
            any(category_id.startswith("custom:category:") for category_id in snapshot.item_category_ids["shot"])
        )
        self.assertTrue(
            any(category_id.startswith("user:tag:") for category_id in snapshot.item_category_ids["duplicate-a"])
        )
        self.assertIn("旅行", snapshot.item_search_fields["duplicate-a"])

    def test_p2_signature_invalidates_incremental_cache_when_user_metadata_changes(self):
        item = make_item(
            item_id="one",
            stable_key="same",
            file_signature="same-signature",
        )
        first_items = clone_items_for_classification([item])
        self.analyzer.enrich_items(first_items)
        previous = self.service.classify_batch(first_items, now=self.now)
        self.assertNotIn("user:favorite", previous.item_category_ids["one"])

        second_items = clone_items_for_classification([item])
        self.analyzer.enrich_items(
            second_items,
            user_metadata={
                "same": UserItemMetadata("same", favorite=True),
            },
        )
        incremental = self.service.classify_incremental(
            second_items,
            previous,
            now=self.now,
        )
        self.assertIn("user:favorite", incremental.item_category_ids["one"])


class PhotoManagerDatabaseTests(unittest.TestCase):
    def test_user_metadata_and_feature_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            database = PhotoManagerDatabase(Path(folder))
            metadata = UserItemMetadata(
                "stable",
                favorite=True,
                rating=4,
                tags=["家庭"],
                face_clusters=["人物 B"],
                content_labels=["宠物"],
                custom_categories=["待整理"],
            )
            self.assertTrue(database.save_user_metadata(metadata))
            loaded = database.load_user_metadata(["stable"])["stable"]
            self.assertTrue(loaded.favorite)
            self.assertEqual(loaded.rating, 4)
            self.assertEqual(loaded.tags, ["家庭"])
            self.assertEqual(loaded.face_clusters, ["人物 B"])

            record = ImageFeatureRecord(
                "stable",
                "sig",
                perceptual_hash="ffffffffffffffff",
                blur_score=12.5,
                is_blurry=True,
                is_screenshot=True,
                content_labels=["截图"],
            )
            self.assertTrue(database.save_feature_cache({"stable": record}))
            loaded_record = database.load_feature_cache(["stable"])["stable"]
            self.assertEqual(loaded_record.perceptual_hash, "ffffffffffffffff")
            self.assertTrue(loaded_record.is_blurry)
            self.assertTrue(loaded_record.is_screenshot)
            self.assertEqual(loaded_record.content_labels, ["截图"])

            rule = CustomRuleDefinition(
                "rule-one",
                "PNG 自定义规则",
                "suffix",
                "equals",
                ".png",
                "PNG 文件",
            )
            self.assertTrue(database.save_custom_rule(rule))
            loaded_rules = database.load_custom_rules()
            self.assertEqual(len(loaded_rules), 1)
            self.assertEqual(loaded_rules[0].category, "PNG 文件")
            self.assertTrue(database.delete_custom_rule("rule-one"))
            self.assertEqual(database.load_custom_rules(), [])

    def test_large_category_repository_snapshot_can_restore_from_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = PhotoManagerDatabase(root)
            repository = CategoryRepository(
                root,
                database=database,
                sqlite_migration_item_threshold=2,
            )
            service = ClassificationService([PlusAIRule()])
            items = [
                make_item(item_id="first", stable_key="first", file_signature="sig-first"),
                make_item(item_id="second", stable_key="second", file_signature="sig-second"),
            ]
            items[0].p2_favorite = True
            items[1].p2_rating = 5
            snapshot = service.classify_batch(items, now=datetime(2026, 7, 9, 12, 0))
            self.assertTrue(repository.save(snapshot))
            self.assertTrue((root / "photo_manager.db").exists())

            (root / "auto_categories.json").unlink()
            (root / "item_category_relations.json").unlink()
            restored = repository.load()
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertIn("user:favorite", restored.stable_key_category_ids["first"])
            self.assertIn("user:rating:5", restored.stable_key_category_ids["second"])


if __name__ == "__main__":
    unittest.main()
