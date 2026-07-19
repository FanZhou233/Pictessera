"""P2 SQLite 数据仓库。

数据库用于：
- 用户元数据：收藏、评分、人工标签、人脸/内容/自定义分类标签。
- 图像特征缓存：截图、模糊分数、感知哈希、轻量内容标签。
- 大数据量时的分类快照镜像。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from photo_manager.domain import (
    AutoCategory,
    CategoryType,
    CustomRuleDefinition,
    ImageFeatureRecord,
    ItemCategoryRelation,
    UserItemMetadata,
)
from photo_manager.services.classification import ClassificationSnapshot


class PhotoManagerDatabase:
    DB_FILE = "photo_manager.db"
    SCHEMA_VERSION = 1

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.database_path = self.state_dir / self.DB_FILE

    def load_user_metadata(
        self,
        stable_keys: Iterable[str] | None = None,
    ) -> dict[str, UserItemMetadata]:
        keys = [str(key) for key in stable_keys or [] if key]
        rows = self._select_rows("item_user_metadata", keys)
        return {
            str(row["stable_key"]): UserItemMetadata(
                stable_key=str(row["stable_key"]),
                favorite=bool(row["favorite"]),
                rating=int(row["rating"] or 0),
                tags=self._loads_list(row["tags_json"]),
                face_clusters=self._loads_list(row["face_clusters_json"]),
                content_labels=self._loads_list(row["content_labels_json"]),
                custom_categories=self._loads_list(row["custom_categories_json"]),
                updated_at=float(row["updated_at"] or 0.0),
            )
            for row in rows
        }

    def save_user_metadata(self, metadata: UserItemMetadata) -> bool:
        if not metadata.stable_key:
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO item_user_metadata(
                        stable_key, favorite, rating, tags_json,
                        face_clusters_json, content_labels_json,
                        custom_categories_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET
                        favorite=excluded.favorite,
                        rating=excluded.rating,
                        tags_json=excluded.tags_json,
                        face_clusters_json=excluded.face_clusters_json,
                        content_labels_json=excluded.content_labels_json,
                        custom_categories_json=excluded.custom_categories_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        metadata.stable_key,
                        1 if metadata.favorite else 0,
                        max(0, min(5, int(metadata.rating or 0))),
                        self._dumps_list(metadata.tags),
                        self._dumps_list(metadata.face_clusters),
                        self._dumps_list(metadata.content_labels),
                        self._dumps_list(metadata.custom_categories),
                        float(metadata.updated_at or time.time()),
                    ),
                )
            return True
        except Exception:
            return False

    def set_favorite(self, stable_key: str, favorite: bool) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.favorite = bool(favorite)
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def set_rating(self, stable_key: str, rating: int) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.rating = max(0, min(5, int(rating or 0)))
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def set_tags(self, stable_key: str, tags: list[str]) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.tags = self._clean_list(tags)
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def set_face_clusters(self, stable_key: str, clusters: list[str]) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.face_clusters = self._clean_list(clusters)
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def set_content_labels(self, stable_key: str, labels: list[str]) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.content_labels = self._clean_list(labels)
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def set_custom_categories(self, stable_key: str, categories: list[str]) -> bool:
        metadata = self._metadata_for_update(stable_key)
        metadata.custom_categories = self._clean_list(categories)
        metadata.updated_at = time.time()
        return self.save_user_metadata(metadata)

    def load_feature_cache(
        self,
        stable_keys: Iterable[str] | None = None,
    ) -> dict[str, ImageFeatureRecord]:
        keys = [str(key) for key in stable_keys or [] if key]
        rows = self._select_rows("item_feature_cache", keys)
        return {
            str(row["stable_key"]): ImageFeatureRecord(
                stable_key=str(row["stable_key"]),
                signature=str(row["signature"] or ""),
                perceptual_hash=str(row["perceptual_hash"] or ""),
                blur_score=(
                    float(row["blur_score"])
                    if row["blur_score"] is not None
                    else None
                ),
                is_blurry=bool(row["is_blurry"]),
                is_screenshot=bool(row["is_screenshot"]),
                content_labels=self._loads_list(row["content_labels_json"]),
                updated_at=float(row["updated_at"] or 0.0),
            )
            for row in rows
        }

    def save_feature_cache(
        self,
        records: dict[str, ImageFeatureRecord] | Iterable[ImageFeatureRecord],
    ) -> bool:
        if isinstance(records, dict):
            values = list(records.values())
        else:
            values = list(records)
        if not values:
            return True
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO item_feature_cache(
                        stable_key, signature, perceptual_hash, blur_score,
                        is_blurry, is_screenshot, content_labels_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_key) DO UPDATE SET
                        signature=excluded.signature,
                        perceptual_hash=excluded.perceptual_hash,
                        blur_score=excluded.blur_score,
                        is_blurry=excluded.is_blurry,
                        is_screenshot=excluded.is_screenshot,
                        content_labels_json=excluded.content_labels_json,
                        updated_at=excluded.updated_at
                    """,
                    [
                        (
                            record.stable_key,
                            record.signature,
                            record.perceptual_hash,
                            record.blur_score,
                            1 if record.is_blurry else 0,
                            1 if record.is_screenshot else 0,
                            self._dumps_list(record.content_labels),
                            float(record.updated_at or time.time()),
                        )
                        for record in values
                        if record.stable_key
                    ],
                )
            return True
        except Exception:
            return False

    def load_custom_rules(self) -> list[CustomRuleDefinition]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM custom_classification_rules ORDER BY name, rule_id"
                ).fetchall()
            return [
                CustomRuleDefinition(
                    rule_id=str(row["rule_id"]),
                    name=str(row["name"] or ""),
                    field=str(row["field"] or ""),
                    operator=str(row["operator"] or "contains"),
                    value=str(row["value"] or ""),
                    category=str(row["category"] or ""),
                    enabled=bool(row["enabled"]),
                    updated_at=float(row["updated_at"] or 0.0),
                )
                for row in rows
            ]
        except Exception:
            return []

    def save_custom_rule(self, rule: CustomRuleDefinition) -> bool:
        if not rule.rule_id:
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO custom_classification_rules(
                        rule_id, name, field, operator, value,
                        category, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rule_id) DO UPDATE SET
                        name=excluded.name,
                        field=excluded.field,
                        operator=excluded.operator,
                        value=excluded.value,
                        category=excluded.category,
                        enabled=excluded.enabled,
                        updated_at=excluded.updated_at
                    """,
                    (
                        rule.rule_id,
                        rule.name,
                        rule.field,
                        rule.operator,
                        rule.value,
                        rule.category,
                        1 if rule.enabled else 0,
                        float(rule.updated_at or time.time()),
                    ),
                )
            return True
        except Exception:
            return False

    def delete_custom_rule(self, rule_id: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM custom_classification_rules WHERE rule_id = ?",
                    (str(rule_id or ""),),
                )
            return True
        except Exception:
            return False

    def save_classification_snapshot(self, snapshot: ClassificationSnapshot) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM classification_meta")
                conn.execute("DELETE FROM auto_categories")
                conn.execute("DELETE FROM item_category_state")
                conn.execute("DELETE FROM item_category_relations")
                conn.executemany(
                    """
                    INSERT INTO auto_categories(
                        category_id, category_type, name, parent_id,
                        sort_key, rule_version, item_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            category.category_id,
                            category.category_type.value,
                            category.name,
                            category.parent_id,
                            category.sort_key,
                            int(category.rule_version),
                            int(category.item_count),
                        )
                        for category in snapshot.categories.values()
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO item_category_state(
                        stable_key, category_ids_json, signature
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (
                            stable_key,
                            self._dumps_list(sorted(category_ids)),
                            snapshot.item_signatures.get(stable_key, ""),
                        )
                        for stable_key, category_ids in snapshot.stable_key_category_ids.items()
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO item_category_relations(
                        stable_key, category_id, classified_at, source_signature
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            relation.stable_key,
                            relation.category_id,
                            relation.classified_at,
                            relation.source_signature,
                        )
                        for relation in snapshot.relations
                    ],
                )
                conn.executemany(
                    "INSERT INTO classification_meta(key, value) VALUES (?, ?)",
                    [
                        ("classified_date", snapshot.classified_date),
                        (
                            "rule_versions",
                            json.dumps(
                                snapshot.rule_versions,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        ),
                    ],
                )
            return True
        except Exception:
            return False

    def load_classification_snapshot(self) -> ClassificationSnapshot | None:
        if not self.database_path.exists():
            return None
        try:
            with self._connect() as conn:
                category_rows = conn.execute("SELECT * FROM auto_categories").fetchall()
                state_rows = conn.execute("SELECT * FROM item_category_state").fetchall()
                relation_rows = conn.execute(
                    "SELECT * FROM item_category_relations"
                ).fetchall()
                meta_rows = conn.execute("SELECT key, value FROM classification_meta").fetchall()
            if not category_rows or not state_rows:
                return None
            categories: dict[str, AutoCategory] = {}
            for row in category_rows:
                category = AutoCategory(
                    category_id=str(row["category_id"]),
                    category_type=CategoryType(str(row["category_type"])),
                    name=str(row["name"] or ""),
                    parent_id=(
                        str(row["parent_id"]) if row["parent_id"] is not None else None
                    ),
                    sort_key=str(row["sort_key"] or ""),
                    rule_version=int(row["rule_version"] or 1),
                    item_count=int(row["item_count"] or 0),
                )
                categories[category.category_id] = category
            stable_key_category_ids: dict[str, set[str]] = {}
            item_signatures: dict[str, str] = {}
            for row in state_rows:
                stable_key = str(row["stable_key"])
                stable_key_category_ids[stable_key] = set(
                    self._loads_list(row["category_ids_json"])
                )
                item_signatures[stable_key] = str(row["signature"] or "")
            relations = [
                ItemCategoryRelation(
                    stable_key=str(row["stable_key"]),
                    category_id=str(row["category_id"]),
                    classified_at=str(row["classified_at"] or ""),
                    source_signature=str(row["source_signature"] or ""),
                )
                for row in relation_rows
            ]
            meta = {str(row["key"]): str(row["value"] or "") for row in meta_rows}
            return ClassificationSnapshot(
                categories=categories,
                stable_key_category_ids=stable_key_category_ids,
                item_signatures=item_signatures,
                relations=relations,
                rule_versions={
                    str(key): int(value)
                    for key, value in json.loads(meta.get("rule_versions", "{}")).items()
                },
                classified_date=meta.get("classified_date", ""),
            )
        except Exception:
            return None

    def _metadata_for_update(self, stable_key: str) -> UserItemMetadata:
        stable_key = str(stable_key or "")
        existing = self.load_user_metadata([stable_key]).get(stable_key)
        return existing or UserItemMetadata(stable_key=stable_key)

    def _select_rows(self, table_name: str, keys: list[str]) -> list[sqlite3.Row]:
        if not keys:
            return []
        rows: list[sqlite3.Row] = []
        try:
            with self._connect() as conn:
                for index in range(0, len(keys), 900):
                    chunk = keys[index : index + 900]
                    placeholders = ",".join("?" for _ in chunk)
                    rows.extend(
                        conn.execute(
                            f"SELECT * FROM {table_name} WHERE stable_key IN ({placeholders})",
                            chunk,
                        ).fetchall()
                    )
        except Exception:
            return []
        return rows

    def _connect(self) -> sqlite3.Connection:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_info(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_user_metadata(
                stable_key TEXT PRIMARY KEY,
                favorite INTEGER NOT NULL DEFAULT 0,
                rating INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                face_clusters_json TEXT NOT NULL DEFAULT '[]',
                content_labels_json TEXT NOT NULL DEFAULT '[]',
                custom_categories_json TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_feature_cache(
                stable_key TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL DEFAULT '',
                blur_score REAL,
                is_blurry INTEGER NOT NULL DEFAULT 0,
                is_screenshot INTEGER NOT NULL DEFAULT 0,
                content_labels_json TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS classification_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_classification_rules(
                rule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                field TEXT NOT NULL,
                operator TEXT NOT NULL DEFAULT 'contains',
                value TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_categories(
                category_id TEXT PRIMARY KEY,
                category_type TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT,
                sort_key TEXT NOT NULL DEFAULT '',
                rule_version INTEGER NOT NULL DEFAULT 1,
                item_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_category_state(
                stable_key TEXT PRIMARY KEY,
                category_ids_json TEXT NOT NULL,
                signature TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_category_relations(
                stable_key TEXT NOT NULL,
                category_id TEXT NOT NULL,
                classified_at TEXT NOT NULL DEFAULT '',
                source_signature TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO schema_info(key, value) VALUES('version', ?)
            """,
            (str(self.SCHEMA_VERSION),),
        )

    @staticmethod
    def _loads_list(raw) -> list[str]:
        try:
            data = json.loads(str(raw or "[]"))
            if not isinstance(data, list):
                return []
            return PhotoManagerDatabase._clean_list(data)
        except Exception:
            return []

    @staticmethod
    def _dumps_list(values: list[str] | tuple[str, ...]) -> str:
        return json.dumps(
            PhotoManagerDatabase._clean_list(values),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _clean_list(values) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            text = text[:80]
            key = text.casefold()
            if key not in seen:
                cleaned.append(text)
                seen.add(key)
        return cleaned
