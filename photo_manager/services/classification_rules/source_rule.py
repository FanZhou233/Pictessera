"""源文件夹和一级子目录分类规则。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem


def _folder_id(prefix: str, path: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(str(path)))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"source:{prefix}:{digest}"


class SourceRule:
    rule_version = 1
    root_id = "source"

    def base_categories(self) -> list[AutoCategory]:
        return [AutoCategory(self.root_id, CategoryType.SOURCE, "来源", sort_key="60")]

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        folder = item.source_folder
        root = item.library_root or folder
        try:
            parts = folder.resolve().relative_to(root.resolve()).parts
        except Exception:
            parts = ()
        if not parts:
            return [
                AutoCategory(
                    _folder_id("folder", folder),
                    CategoryType.SOURCE,
                    folder.name or str(folder),
                    self.root_id,
                    f"10:{str(folder).casefold()}",
                )
            ]
        first_folder = root / parts[0]
        first_id = _folder_id("first", first_folder)
        categories = [
            AutoCategory(first_id, CategoryType.SOURCE, parts[0], self.root_id, f"10:{parts[0].casefold()}")
        ]
        if folder != first_folder:
            categories.append(
                AutoCategory(
                    _folder_id("folder", folder),
                    CategoryType.SOURCE,
                    folder.name or str(folder),
                    first_id,
                    f"20:{str(folder).casefold()}",
                )
            )
        return categories
