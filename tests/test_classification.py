"""P0 自动分类服务测试，不启动 Qt GUI。"""

from __future__ import annotations

import unittest
import time
from datetime import datetime, timedelta
from pathlib import Path

from photo_manager.domain import CategoryType, PhotoItem
from photo_manager.services.classification import ClassificationService
from photo_manager.services.classification_rules import (
    DeviceRule,
    FileRule,
    LocationRule,
    MediaRule,
    SourceRule,
    TimeRule,
)


def make_item(
    *,
    item_id: str = "one",
    suffix: str = ".heic",
    is_live: bool = False,
    shot_time: datetime | None = None,
    time_source: str = "EXIF",
    size_bytes: int = 1024,
    item_kind: str = "photo",
    needs_binding: bool = False,
    stable_key: str | None = None,
    file_signature: str | None = None,
    camera_make: str = "",
    camera_model: str = "",
    gps_latitude: float | None = None,
    gps_longitude: float | None = None,
    source_folder: Path = Path("photos"),
    library_root: Path | None = None,
) -> PhotoItem:
    image = Path(f"IMG_0001{suffix}")
    files = [image]
    if is_live:
        files.append(Path("IMG_0001.MOV"))
    return PhotoItem(
        item_id=item_id,
        display_name=image.name,
        files=files,
        size_bytes=size_bytes,
        representative_image=image,
        is_live=is_live,
        item_type="Live Photo" if is_live else "照片",
        shot_time=shot_time if shot_time is not None else datetime(2026, 7, 8, 12, 0, 0),
        time_source=time_source,
        source_folder=source_folder,
        stable_key=stable_key or f"stable-{item_id}",
        file_signature=file_signature or f"signature-{item_id}",
        item_kind=item_kind,
        needs_binding=needs_binding,
        camera_make=camera_make,
        camera_model=camera_model,
        gps_latitude=gps_latitude,
        gps_longitude=gps_longitude,
        library_root=library_root,
    )


class ClassificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 9, 12, 0, 0)
        self.service = ClassificationService(
            [
                TimeRule(),
                MediaRule(),
                FileRule(image_size_resolver=lambda _path: (800, 600)),
            ]
        )

    def test_live_heic_gets_multiple_categories(self):
        item = make_item(is_live=True)
        snapshot = self.service.classify_batch([item], now=self.now)
        ids = snapshot.item_category_ids[item.item_id]
        self.assertIn("media:live", ids)
        self.assertIn("media:heic", ids)
        self.assertIn("time:recent:7d", ids)
        self.assertIn("time:recent:30d", ids)
        self.assertIn("time:year:2026", ids)
        self.assertIn("time:month:2026-07", ids)
        self.assertIn("file:small-image", ids)
        self.assertEqual(snapshot.categories["media"].category_type, CategoryType.MEDIA)

    def test_unknown_time_and_metadata_missing(self):
        item = make_item(shot_time=datetime.min, time_source="文件时间")
        snapshot = self.service.classify_batch([item], now=self.now)
        ids = snapshot.item_category_ids[item.item_id]
        self.assertIn("time:unknown", ids)
        self.assertIn("file:metadata-missing", ids)

    def test_old_photo_is_not_recent(self):
        item = make_item(shot_time=self.now - timedelta(days=60))
        snapshot = self.service.classify_batch([item], now=self.now)
        ids = snapshot.item_category_ids[item.item_id]
        self.assertNotIn("time:recent:7d", ids)
        self.assertNotIn("time:recent:30d", ids)

    def test_media_extensions_and_unbound_mov(self):
        jpeg = make_item(item_id="jpeg", suffix=".jpg")
        png = make_item(item_id="png", suffix=".png")
        mov = make_item(
            item_id="mov",
            suffix=".mov",
            item_kind="mov_only",
            needs_binding=True,
        )
        snapshot = self.service.classify_batch([jpeg, png, mov], now=self.now)
        self.assertIn("media:jpeg", snapshot.item_category_ids["jpeg"])
        self.assertIn("media:png", snapshot.item_category_ids["png"])
        self.assertIn("media:unbound-mov", snapshot.item_category_ids["mov"])

    def test_large_image_and_large_file(self):
        service = ClassificationService(
            [
                FileRule(image_size_resolver=lambda _path: (6000, 4000)),
            ]
        )
        item = make_item(size_bytes=150 * 1024 * 1024)
        snapshot = service.classify_batch([item], now=self.now)
        self.assertIn("file:large-image", snapshot.item_category_ids[item.item_id])
        self.assertIn("file:large-file", snapshot.item_category_ids[item.item_id])

    def test_parent_category_contains_descendant_items(self):
        item = make_item()
        snapshot = self.service.classify_batch([item], now=self.now)
        self.assertEqual(snapshot.item_ids_for("time"), {item.item_id})
        self.assertEqual(snapshot.item_ids_for("media"), {item.item_id})
        self.assertEqual(snapshot.item_ids_for("file"), {item.item_id})

    def test_empty_batch_still_has_base_categories(self):
        snapshot = self.service.classify_batch([], now=self.now)
        self.assertIn("time", snapshot.categories)
        self.assertIn("media", snapshot.categories)
        self.assertIn("file", snapshot.categories)
        self.assertEqual(snapshot.categories["time"].item_count, 0)

    def test_incremental_reuses_unchanged_and_drops_deleted_items(self):
        first = make_item(item_id="first")
        deleted = make_item(item_id="deleted")
        previous = self.service.classify_batch([first, deleted], now=self.now)
        incremental = self.service.classify_incremental(
            [first],
            previous,
            now=self.now,
        )
        self.assertEqual(
            incremental.item_category_ids["first"],
            previous.item_category_ids["first"],
        )
        self.assertNotIn("deleted", incremental.item_category_ids)
        self.assertNotIn("deleted", incremental.item_ids_for("media"))

    def test_incremental_reclassifies_changed_signature(self):
        item = make_item(item_id="changed", suffix=".jpg")
        previous = self.service.classify_batch([item], now=self.now)
        item.files = [Path("CHANGED.PNG")]
        item.representative_image = Path("CHANGED.PNG")
        item.file_signature = "new-signature"
        incremental = self.service.classify_incremental([item], previous, now=self.now)
        self.assertIn("media:png", incremental.item_category_ids[item.item_id])
        self.assertNotIn("media:jpeg", incremental.item_category_ids[item.item_id])

    def test_incremental_reuses_stable_key_when_runtime_item_id_changes(self):
        first = make_item(
            item_id="scan-one",
            stable_key="same-file",
            file_signature="same-signature",
        )
        previous = self.service.classify_batch([first], now=self.now)
        rescanned = make_item(
            item_id="scan-two",
            stable_key="same-file",
            file_signature="same-signature",
        )
        incremental = self.service.classify_incremental(
            [rescanned], previous, now=self.now
        )
        self.assertEqual(
            incremental.item_category_ids["scan-two"],
            previous.stable_key_category_ids["same-file"],
        )
        self.assertNotIn("scan-one", incremental.item_category_ids)

    def test_device_location_and_source_rules(self):
        service = ClassificationService(
            [DeviceRule(), LocationRule(), SourceRule()]
        )
        item = make_item(
            camera_make="Apple",
            camera_model="iPhone 15 Pro",
            gps_latitude=31.2304,
            gps_longitude=121.4737,
            source_folder=Path("D:/Photos/Trips/Shanghai"),
            library_root=Path("D:/Photos"),
        )
        snapshot = service.classify_batch([item], now=self.now)
        ids = snapshot.item_category_ids[item.item_id]
        self.assertTrue(any(value.startswith("device:make:") for value in ids))
        self.assertTrue(any(value.startswith("device:model:") for value in ids))
        self.assertIn("location:with-gps", ids)
        self.assertIn("location:grid:+31.2:+121.5", ids)
        self.assertTrue(any(value.startswith("source:first:") for value in ids))
        self.assertTrue(any(value.startswith("source:folder:") for value in ids))

    def test_unknown_device_and_missing_gps(self):
        service = ClassificationService([DeviceRule(), LocationRule()])
        item = make_item()
        ids = service.classify_batch([item], now=self.now).item_category_ids[
            item.item_id
        ]
        self.assertIn("device:unknown", ids)
        self.assertIn("location:without-gps", ids)

    def test_rule_version_change_invalidates_incremental_cache(self):
        class CountingRule:
            rule_version = 1

            def __init__(self):
                self.calls = 0

            def base_categories(self):
                return []

            def classify(self, item, now):
                del item, now
                self.calls += 1
                return []

        rule = CountingRule()
        service = ClassificationService([rule])
        item = make_item(stable_key="stable", file_signature="signature")
        previous = service.classify_batch([item], now=self.now)
        self.assertEqual(rule.calls, 1)
        rule.rule_version = 2
        service.classify_incremental([item], previous, now=self.now)
        self.assertEqual(rule.calls, 2)

    def test_failed_item_enters_error_category(self):
        class BrokenRule:
            rule_version = 1

            def base_categories(self):
                return []

            def classify(self, item, now):
                del item, now
                raise RuntimeError("broken")

        item = make_item()
        snapshot = ClassificationService([BrokenRule()]).classify_batch(
            [item], now=self.now
        )
        self.assertIn(item.item_id, snapshot.errors)
        self.assertIn(
            "classification:error", snapshot.item_category_ids[item.item_id]
        )

    def test_progress_is_submitted_in_batches(self):
        items = [make_item(item_id=f"item-{index}") for index in range(7)]
        updates = []
        self.service.classify_batch(
            items,
            now=self.now,
            progress_callback=lambda snapshot, done, total: updates.append(
                (snapshot, done, total)
            ),
            progress_batch_size=3,
        )
        self.assertEqual([(done, total) for _, done, total in updates], [(3, 7), (6, 7)])

    def test_ten_thousand_items_classify_without_quadratic_slowdown(self):
        service = ClassificationService([MediaRule()])
        items = [
            make_item(
                item_id=f"item-{index}",
                stable_key=f"stable-{index}",
                file_signature=f"signature-{index}",
            )
            for index in range(10_000)
        ]
        started = time.perf_counter()
        snapshot = service.classify_batch(items, now=self.now)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(snapshot.item_category_ids), 10_000)
        self.assertEqual(len(snapshot.item_ids_for("media")), 10_000)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
