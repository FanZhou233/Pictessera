import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photo_manager.services.content_analysis import (
    ContentPrediction,
    StaticContentProvider,
    SemanticVectorIndex,
    reliable_labels,
)
from photo_manager.services.plus_analysis import PlusFeatureAnalyzer
from photo_manager.services.search import wildcard_query_matches


class ContentAnalysisTests(unittest.TestCase):
    def test_confidence_filter_and_casefold_deduplication(self):
        labels = reliable_labels(
            [
                ContentPrediction("苹果", 0.96),
                ContentPrediction("桌子", 0.91),
                ContentPrediction("苹果", 0.70),
                ContentPrediction("噪声", 0.10),
            ],
            threshold=0.35,
        )
        self.assertEqual(labels, ["苹果", "桌子"])

    def test_provider_is_connected_to_existing_feature_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "apple.jpg"
            Image.new("RGB", (120, 80), "red").save(image_path)
            analyzer = PlusFeatureAnalyzer(
                content_provider=StaticContentProvider(
                    [ContentPrediction("苹果", 0.96), ContentPrediction("桌子", 0.91)]
                )
            )
            features = analyzer._read_image_features(image_path)
            self.assertIn("苹果", features["content_labels"])
            self.assertIn("桌子", features["content_labels"])

    def test_model_or_threshold_change_invalidates_feature_cache(self):
        first = PlusFeatureAnalyzer(
            content_provider=StaticContentProvider([ContentPrediction("苹果", 0.9)]),
            content_confidence_threshold=0.35,
        )
        second = PlusFeatureAnalyzer(
            content_provider=StaticContentProvider([ContentPrediction("苹果", 0.9)]),
            content_confidence_threshold=0.60,
        )
        self.assertNotEqual(
            first._feature_cache_signature("same-photo"),
            second._feature_cache_signature("same-photo"),
        )


class ContentSearchExpressionTests(unittest.TestCase):
    def setUp(self):
        self.fields = ["IMG_1001.HEIC", "苹果", "桌子", "水果", "室内"]

    def test_and_or_exclusion_and_tag_prefix(self):
        self.assertTrue(wildcard_query_matches("苹果 桌子", self.fields))
        self.assertTrue(wildcard_query_matches("梨 | 苹果", self.fields))
        self.assertTrue(wildcard_query_matches("水果 -人物", self.fields))
        self.assertFalse(wildcard_query_matches("水果 -桌子", self.fields))
        self.assertTrue(wildcard_query_matches("标签:苹果", self.fields))

    def test_chinese_query_matches_english_model_labels(self):
        self.assertTrue(wildcard_query_matches("苹果", ["Granny Smith"]))
        self.assertTrue(wildcard_query_matches("桌子", ["desk"]))
        self.assertTrue(wildcard_query_matches("海边", ["seashore, coast, seacoast"]))


class SemanticVectorIndexTests(unittest.TestCase):
    def test_cosine_search_and_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "semantic-index.json"
            index = SemanticVectorIndex(path)
            index.model = "test-model"
            index.upsert("apple-on-table", [1.0, 0.0, 0.0])
            index.upsert("beach", [0.0, 1.0, 0.0])
            self.assertEqual(index.search([0.9, 0.1, 0.0], limit=1)[0][0], "apple-on-table")
            index.save()

            restored = SemanticVectorIndex(path)
            self.assertTrue(restored.load())
            self.assertEqual(restored.model, "test-model")
            self.assertEqual(restored.search([0.0, 1.0, 0.0], limit=1)[0][0], "beach")


if __name__ == "__main__":
    unittest.main()
