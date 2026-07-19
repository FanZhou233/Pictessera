"""媒体类型分类规则。"""

from __future__ import annotations

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem


class MediaRule:
    rule_version = 1
    root_id = "media"

    _EXTENSIONS = {
        ".heic": ("media:heic", "HEIC/HEIF", "30"),
        ".heif": ("media:heic", "HEIC/HEIF", "30"),
        ".jpg": ("media:jpeg", "JPEG", "40"),
        ".jpeg": ("media:jpeg", "JPEG", "40"),
        ".png": ("media:png", "PNG", "50"),
    }

    def base_categories(self) -> list[AutoCategory]:
        values = [
            ("media:live", "实况照片", "10"),
            ("media:still", "静态照片", "20"),
            ("media:heic", "HEIC/HEIF", "30"),
            ("media:jpeg", "JPEG", "40"),
            ("media:png", "PNG", "50"),
            ("media:unbound-mov", "未绑定 MOV", "60"),
        ]
        return [
            AutoCategory(self.root_id, CategoryType.MEDIA, "媒体类型", sort_key="20"),
            *[
                AutoCategory(
                    category_id,
                    CategoryType.MEDIA,
                    name,
                    self.root_id,
                    sort_key,
                    self.rule_version,
                )
                for category_id, name, sort_key in values
            ],
        ]

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        is_unbound = item.needs_binding or item.item_kind == "mov_only"
        categories: list[AutoCategory] = []
        if is_unbound:
            categories.append(
                AutoCategory(
                    "media:unbound-mov",
                    CategoryType.MEDIA,
                    "未绑定 MOV",
                    self.root_id,
                    "60",
                    self.rule_version,
                )
            )
        elif item.is_live:
            categories.append(
                AutoCategory(
                    "media:live",
                    CategoryType.MEDIA,
                    "实况照片",
                    self.root_id,
                    "10",
                    self.rule_version,
                )
            )
        else:
            categories.append(
                AutoCategory(
                    "media:still",
                    CategoryType.MEDIA,
                    "静态照片",
                    self.root_id,
                    "20",
                    self.rule_version,
                )
            )

        suffixes = {path.suffix.lower() for path in item.files}
        if item.representative_image:
            suffixes.add(item.representative_image.suffix.lower())
        seen: set[str] = set()
        for suffix in suffixes:
            definition = self._EXTENSIONS.get(suffix)
            if definition is None or definition[0] in seen:
                continue
            seen.add(definition[0])
            categories.append(
                AutoCategory(
                    definition[0],
                    CategoryType.MEDIA,
                    definition[1],
                    self.root_id,
                    definition[2],
                    self.rule_version,
                )
            )
        return categories
