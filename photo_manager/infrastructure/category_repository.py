"""自动分类 JSON 缓存仓库。

缓存只保存虚拟分类及文件身份，不修改、移动或重命名任何照片。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from photo_manager.domain import AutoCategory, CategoryType, ItemCategoryRelation
from photo_manager.services.classification import ClassificationSnapshot
from .photo_manager_database import PhotoManagerDatabase


class CategoryRepository:
    VERSION = 1
    CATEGORIES_FILE = "auto_categories.json"
    RELATIONS_FILE = "item_category_relations.json"
    SQLITE_MIGRATION_ITEM_THRESHOLD = 5_000

    def __init__(
        self,
        state_dir: Path,
        database: PhotoManagerDatabase | None = None,
        sqlite_migration_item_threshold: int | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.categories_path = self.state_dir / self.CATEGORIES_FILE
        self.relations_path = self.state_dir / self.RELATIONS_FILE
        self.database = database or PhotoManagerDatabase(self.state_dir)
        self.sqlite_migration_item_threshold = (
            self.SQLITE_MIGRATION_ITEM_THRESHOLD
            if sqlite_migration_item_threshold is None
            else max(1, int(sqlite_migration_item_threshold))
        )

    def save(self, snapshot: ClassificationSnapshot) -> bool:
        categories_document = {
            "version": self.VERSION,
            "classified_date": snapshot.classified_date,
            "rule_versions": dict(snapshot.rule_versions),
            "categories": [
                {
                    "category_id": category.category_id,
                    "category_type": category.category_type.value,
                    "name": category.name,
                    "parent_id": category.parent_id,
                    "sort_key": category.sort_key,
                    "rule_version": int(category.rule_version),
                    "item_count": int(category.item_count),
                }
                for category in snapshot.categories.values()
            ],
        }
        relations_document = {
            "version": self.VERSION,
            "items": {
                stable_key: {
                    "category_ids": sorted(category_ids),
                    "signature": snapshot.item_signatures.get(stable_key, ""),
                }
                for stable_key, category_ids in snapshot.stable_key_category_ids.items()
            },
            "relations": [
                {
                    "stable_key": relation.stable_key,
                    "category_id": relation.category_id,
                    "classified_at": relation.classified_at,
                    "source_signature": relation.source_signature,
                }
                for relation in snapshot.relations
            ],
        }
        try:
            self._atomic_write(self.categories_path, categories_document)
            self._atomic_write(self.relations_path, relations_document)
            if (
                len(snapshot.stable_key_category_ids)
                >= self.sqlite_migration_item_threshold
            ):
                return self.database.save_classification_snapshot(snapshot)
            return True
        except Exception:
            return False

    def load(self) -> ClassificationSnapshot | None:
        categories_document = self._read_document(self.categories_path)
        relations_document = self._read_document(self.relations_path)
        if categories_document is None or relations_document is None:
            return self.database.load_classification_snapshot()
        try:
            categories_document = self._migrate(categories_document)
            relations_document = self._migrate(relations_document)
            categories: dict[str, AutoCategory] = {}
            for raw in categories_document.get("categories", []):
                category = AutoCategory(
                    category_id=str(raw["category_id"]),
                    category_type=CategoryType(str(raw["category_type"])),
                    name=str(raw.get("name", "")),
                    parent_id=(
                        str(raw["parent_id"])
                        if raw.get("parent_id") is not None
                        else None
                    ),
                    sort_key=str(raw.get("sort_key", "")),
                    rule_version=int(raw.get("rule_version", 1)),
                    item_count=int(raw.get("item_count", 0)),
                )
                categories[category.category_id] = category

            stable_key_category_ids: dict[str, set[str]] = {}
            item_signatures: dict[str, str] = {}
            for stable_key, raw in relations_document.get("items", {}).items():
                if not isinstance(raw, dict):
                    continue
                stable_key = str(stable_key)
                stable_key_category_ids[stable_key] = {
                    str(category_id)
                    for category_id in raw.get("category_ids", [])
                }
                item_signatures[stable_key] = str(raw.get("signature", ""))

            relations = [
                ItemCategoryRelation(
                    stable_key=str(raw["stable_key"]),
                    category_id=str(raw["category_id"]),
                    classified_at=str(raw.get("classified_at", "")),
                    source_signature=str(raw.get("source_signature", "")),
                )
                for raw in relations_document.get("relations", [])
                if isinstance(raw, dict)
                and raw.get("stable_key")
                and raw.get("category_id")
            ]
            return ClassificationSnapshot(
                categories=categories,
                stable_key_category_ids=stable_key_category_ids,
                item_signatures=item_signatures,
                relations=relations,
                rule_versions={
                    str(key): int(value)
                    for key, value in categories_document.get(
                        "rule_versions", {}
                    ).items()
                },
                classified_date=str(
                    categories_document.get("classified_date", "")
                ),
            )
        except Exception:
            self._quarantine(self.categories_path)
            self._quarantine(self.relations_path)
            return None

    def _read_document(self, path: Path) -> dict[str, Any] | None:
        for candidate in (path, self._backup_path(path)):
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("JSON 根节点必须是对象")
                return data
            except Exception:
                self._quarantine(candidate)
        return None

    @classmethod
    def _migrate(cls, document: dict[str, Any]) -> dict[str, Any]:
        version = int(document.get("version", 0))
        if version > cls.VERSION:
            raise ValueError(f"不支持的自动分类缓存版本：{version}")
        migrated = dict(document)
        if version == 0:
            migrated["version"] = 1
            migrated.setdefault("rule_versions", {})
            migrated.setdefault("categories", [])
            migrated.setdefault("items", {})
            migrated.setdefault("relations", [])
        return migrated

    def _atomic_write(self, path: Path, document: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        backup_path = self._backup_path(path)
        if path.exists():
            shutil.copy2(path, backup_path)
        payload = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.bak")

    @staticmethod
    def _quarantine(path: Path) -> None:
        if not path.exists():
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.name}.corrupt.{stamp}")
        index = 1
        while target.exists():
            target = path.with_name(f"{path.name}.corrupt.{stamp}.{index}")
            index += 1
        try:
            os.replace(path, target)
        except Exception:
            pass
