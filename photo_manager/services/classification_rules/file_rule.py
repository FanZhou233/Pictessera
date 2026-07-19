"""文件特征分类规则。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem

ImageSizeResolver = Callable[[Path], Optional[tuple[int, int]]]


def _default_image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


class FileRule:
    rule_version = 1
    root_id = "file"
    small_image_max_pixels = 2_000_000
    large_image_min_pixels = 12_000_000
    large_file_min_bytes = 100 * 1024 * 1024

    def __init__(self, image_size_resolver: ImageSizeResolver | None = None):
        self.image_size_resolver = image_size_resolver or _default_image_size

    def base_categories(self) -> list[AutoCategory]:
        values = [
            ("file:small-image", "小图片", "10"),
            ("file:large-image", "大图片", "20"),
            ("file:large-file", "大文件", "30"),
            ("file:metadata-missing", "元数据缺失", "40"),
        ]
        return [
            AutoCategory(self.root_id, CategoryType.FILE, "文件状态", sort_key="30"),
            *[
                AutoCategory(
                    category_id,
                    CategoryType.FILE,
                    name,
                    self.root_id,
                    sort_key,
                    self.rule_version,
                )
                for category_id, name, sort_key in values
            ],
        ]

    @staticmethod
    def _metadata_missing(item: PhotoItem) -> bool:
        source = (item.time_source or "").strip().lower()
        verified = any(token in source for token in ("exif", "heif", "metadata"))
        return item.shot_time == datetime.min or not verified

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        categories: list[AutoCategory] = []
        if item.size_bytes >= self.large_file_min_bytes:
            categories.append(
                AutoCategory(
                    "file:large-file",
                    CategoryType.FILE,
                    "大文件",
                    self.root_id,
                    "30",
                    self.rule_version,
                )
            )
        if self._metadata_missing(item):
            categories.append(
                AutoCategory(
                    "file:metadata-missing",
                    CategoryType.FILE,
                    "元数据缺失",
                    self.root_id,
                    "40",
                    self.rule_version,
                )
            )

        if item.item_kind != "mov_only" and item.representative_image:
            size = (
                (item.image_width, item.image_height)
                if item.image_width > 0 and item.image_height > 0
                else self.image_size_resolver(item.representative_image)
            )
            if size:
                pixels = max(0, size[0]) * max(0, size[1])
                if pixels <= self.small_image_max_pixels:
                    categories.append(
                        AutoCategory(
                            "file:small-image",
                            CategoryType.FILE,
                            "小图片",
                            self.root_id,
                            "10",
                            self.rule_version,
                        )
                    )
                elif pixels >= self.large_image_min_pixels:
                    categories.append(
                        AutoCategory(
                            "file:large-image",
                            CategoryType.FILE,
                            "大图片",
                            self.root_id,
                            "20",
                            self.rule_version,
                        )
                    )
        return categories
