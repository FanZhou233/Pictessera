"""与 GUI 解耦的自动分类服务。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Iterable, Protocol

from photo_manager.domain import AutoCategory, CategoryType, ItemCategoryRelation, PhotoItem


class ClassificationRule(Protocol):
    rule_version: int

    def base_categories(self) -> list[AutoCategory]: ...

    def classify(self, item: PhotoItem, now: datetime) -> list[AutoCategory]: ...


@dataclass
class ClassificationSnapshot:
    categories: dict[str, AutoCategory] = field(default_factory=dict)
    category_item_ids: dict[str, set[str]] = field(default_factory=dict)
    item_category_ids: dict[str, set[str]] = field(default_factory=dict)
    relations: list[ItemCategoryRelation] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    stable_key_category_ids: dict[str, set[str]] = field(default_factory=dict)
    item_signatures: dict[str, str] = field(default_factory=dict)
    rule_versions: dict[str, int] = field(default_factory=dict)
    classified_date: str = ""
    item_search_fields: dict[str, list[str]] = field(default_factory=dict)

    def item_ids_for(self, category_id: str) -> set[str]:
        return set(self.category_item_ids.get(category_id, set()))


class ClassificationService:
    """注册规则并生成可供 UI 直接筛选的分类快照。"""

    def __init__(self, rules: Iterable[ClassificationRule] | None = None):
        self._rules: list[ClassificationRule] = []
        for rule in rules or ():
            self.register_rule(rule)

    @property
    def rules(self) -> tuple[ClassificationRule, ...]:
        return tuple(self._rules)

    def register_rule(self, rule: ClassificationRule) -> None:
        if rule not in self._rules:
            self._rules.append(rule)

    def base_categories(self) -> dict[str, AutoCategory]:
        categories: dict[str, AutoCategory] = {}
        for rule in self._rules:
            for category in rule.base_categories():
                categories[category.category_id] = category
        categories["classification:error"] = AutoCategory(
            "classification:error",
            category_type=CategoryType.ERROR,
            name="分类异常",
            sort_key="99",
        )
        return categories

    def rule_versions(self) -> dict[str, int]:
        return {
            f"{type(rule).__module__}.{type(rule).__qualname__}": int(
                getattr(rule, "rule_version", 1)
            )
            for rule in self._rules
        }

    def classify_item(
        self,
        item: PhotoItem,
        now: datetime | None = None,
    ) -> tuple[dict[str, AutoCategory], set[str], list[ItemCategoryRelation]]:
        current_time = now or datetime.now()
        categories: dict[str, AutoCategory] = {}
        category_ids: set[str] = set()
        classified_at = current_time.isoformat(timespec="seconds")
        stable_key = item.stable_key or item.item_id
        signature = item.file_signature or ""
        for rule in self._rules:
            for category in rule.classify(item, current_time):
                categories[category.category_id] = category
                category_ids.add(category.category_id)
        relations = [
            ItemCategoryRelation(stable_key, category_id, classified_at, signature)
            for category_id in sorted(category_ids)
        ]
        return categories, category_ids, relations

    def classify_batch(
        self,
        items: Iterable[PhotoItem],
        now: datetime | None = None,
        progress_callback: Callable[[ClassificationSnapshot, int, int], None] | None = None,
        progress_batch_size: int = 250,
    ) -> ClassificationSnapshot:
        current_time = now or datetime.now()
        item_list = list(items)
        snapshot = ClassificationSnapshot(
            categories=self.base_categories(),
            rule_versions=self.rule_versions(),
            classified_date=current_time.date().isoformat(),
        )
        total = len(item_list)
        for index, item in enumerate(item_list, 1):
            stable_key = item.stable_key or item.item_id
            try:
                categories, category_ids, relations = self.classify_item(item, current_time)
                snapshot.categories.update(categories)
                snapshot.item_category_ids[item.item_id] = category_ids
                snapshot.stable_key_category_ids[stable_key] = set(category_ids)
                snapshot.item_signatures[stable_key] = item.file_signature or ""
                snapshot.item_search_fields[item.item_id] = self._search_fields_for_item(item)
                snapshot.relations.extend(relations)
                for category_id in category_ids:
                    snapshot.category_item_ids.setdefault(category_id, set()).add(item.item_id)
            except Exception as exc:
                snapshot.errors[item.item_id] = str(exc)
                snapshot.item_search_fields[item.item_id] = self._search_fields_for_item(item)
                self._record_error(snapshot, item, current_time)
            self._emit_progress(
                snapshot,
                index,
                total,
                progress_callback,
                progress_batch_size,
            )

        self._finalize_snapshot(snapshot)
        self._prune_unused_dynamic_categories(snapshot)
        return snapshot

    def classify_incremental(
        self,
        items: Iterable[PhotoItem],
        previous: ClassificationSnapshot | None,
        now: datetime | None = None,
        progress_callback: Callable[[ClassificationSnapshot, int, int], None] | None = None,
        progress_batch_size: int = 250,
    ) -> ClassificationSnapshot:
        """复用签名未变化项目，仅重新分类新增或变化项目。"""

        if previous is None:
            return self.classify_batch(
                items,
                now,
                progress_callback=progress_callback,
                progress_batch_size=progress_batch_size,
            )
        current_time = now or datetime.now()
        current_date = current_time.date().isoformat()
        item_list = list(items)
        snapshot = ClassificationSnapshot(
            categories={
                **self.base_categories(),
                **{
                    category_id: replace(category)
                    for category_id, category in previous.categories.items()
                },
            },
            rule_versions=self.rule_versions(),
            classified_date=current_date,
        )
        previous_relations: dict[str, list[ItemCategoryRelation]] = {}
        for relation in previous.relations:
            previous_relations.setdefault(relation.stable_key, []).append(relation)

        can_reuse = (
            previous.classified_date == current_date
            and previous.rule_versions == snapshot.rule_versions
        )
        total = len(item_list)
        for index, item in enumerate(item_list, 1):
            stable_key = item.stable_key or item.item_id
            signature = item.file_signature or ""
            unchanged = (
                can_reuse
                and stable_key in previous.stable_key_category_ids
                and previous.item_signatures.get(stable_key, "") == signature
            )
            if unchanged:
                category_ids = set(previous.stable_key_category_ids[stable_key])
                snapshot.item_category_ids[item.item_id] = category_ids
                snapshot.stable_key_category_ids[stable_key] = set(category_ids)
                snapshot.item_signatures[stable_key] = signature
                snapshot.item_search_fields[item.item_id] = self._search_fields_for_item(item)
                snapshot.relations.extend(previous_relations.get(stable_key, []))
                for category_id in category_ids:
                    snapshot.category_item_ids.setdefault(category_id, set()).add(item.item_id)
                self._emit_progress(
                    snapshot,
                    index,
                    total,
                    progress_callback,
                    progress_batch_size,
                )
                continue
            try:
                categories, category_ids, relations = self.classify_item(item, current_time)
                snapshot.categories.update(categories)
                snapshot.item_category_ids[item.item_id] = category_ids
                snapshot.stable_key_category_ids[stable_key] = set(category_ids)
                snapshot.item_signatures[stable_key] = signature
                snapshot.item_search_fields[item.item_id] = self._search_fields_for_item(item)
                snapshot.relations.extend(relations)
                for category_id in category_ids:
                    snapshot.category_item_ids.setdefault(category_id, set()).add(item.item_id)
            except Exception as exc:
                snapshot.errors[item.item_id] = str(exc)
                snapshot.item_search_fields[item.item_id] = self._search_fields_for_item(item)
                self._record_error(snapshot, item, current_time)
            self._emit_progress(
                snapshot,
                index,
                total,
                progress_callback,
                progress_batch_size,
            )

        self._finalize_snapshot(snapshot)
        self._prune_unused_dynamic_categories(snapshot)
        return snapshot

    def _prune_unused_dynamic_categories(
        self, snapshot: ClassificationSnapshot
    ) -> None:
        """删除已无项目的动态年月、设备、位置和来源节点。"""
        base_ids = set(self.base_categories())
        for category_id in list(snapshot.categories):
            if (
                category_id not in base_ids
                and not snapshot.category_item_ids.get(category_id)
            ):
                snapshot.categories.pop(category_id, None)

    @staticmethod
    def _record_error(
        snapshot: ClassificationSnapshot,
        item: PhotoItem,
        current_time: datetime,
    ) -> None:
        category_id = "classification:error"
        stable_key = item.stable_key or item.item_id
        signature = item.file_signature or ""
        snapshot.item_category_ids[item.item_id] = {category_id}
        snapshot.stable_key_category_ids[stable_key] = {category_id}
        snapshot.item_signatures[stable_key] = signature
        snapshot.category_item_ids.setdefault(category_id, set()).add(item.item_id)
        snapshot.relations.append(
            ItemCategoryRelation(
                stable_key,
                category_id,
                current_time.isoformat(timespec="seconds"),
                signature,
            )
        )

    @classmethod
    def _emit_progress(
        cls,
        snapshot: ClassificationSnapshot,
        processed: int,
        total: int,
        callback: Callable[[ClassificationSnapshot, int, int], None] | None,
        batch_size: int,
    ) -> None:
        if callback is None or processed >= total or processed % max(1, batch_size) != 0:
            return
        partial = cls._clone_snapshot(snapshot)
        cls._finalize_snapshot(partial)
        callback(partial, processed, total)

    @staticmethod
    def _clone_snapshot(snapshot: ClassificationSnapshot) -> ClassificationSnapshot:
        return ClassificationSnapshot(
            categories={
                category_id: replace(category)
                for category_id, category in snapshot.categories.items()
            },
            category_item_ids={
                category_id: set(item_ids)
                for category_id, item_ids in snapshot.category_item_ids.items()
            },
            item_category_ids={
                item_id: set(category_ids)
                for item_id, category_ids in snapshot.item_category_ids.items()
            },
            relations=list(snapshot.relations),
            errors=dict(snapshot.errors),
            stable_key_category_ids={
                stable_key: set(category_ids)
                for stable_key, category_ids in snapshot.stable_key_category_ids.items()
            },
            item_signatures=dict(snapshot.item_signatures),
            rule_versions=dict(snapshot.rule_versions),
            classified_date=snapshot.classified_date,
            item_search_fields={
                item_id: list(fields)
                for item_id, fields in snapshot.item_search_fields.items()
            },
        )

    @staticmethod
    def _search_fields_for_item(item: PhotoItem) -> list[str]:
        fields = [
            item.p2_event_name,
            item.p2_burst_name,
            item.p2_duplicate_name,
            item.p2_similar_name,
            "屏幕截图" if item.p2_is_screenshot else "",
            "模糊照片" if item.p2_is_blurry else "",
            "收藏" if item.p2_favorite else "",
            f"{item.p2_rating} 星" if item.p2_rating else "",
        ]
        fields.extend(item.p2_content_labels)
        fields.extend(item.p2_face_clusters)
        fields.extend(item.p2_custom_categories)
        fields.extend(item.p2_manual_tags)
        return [str(field) for field in fields if field]

    @staticmethod
    def _finalize_snapshot(snapshot: ClassificationSnapshot) -> None:
        """补齐父分类聚合与计数。"""

        # 父分类包含全部后代项目，使点击“时间/媒体类型/文件状态”也有结果。
        for category_id, item_ids in list(snapshot.category_item_ids.items()):
            parent_id = snapshot.categories.get(category_id).parent_id if category_id in snapshot.categories else None
            visited: set[str] = set()
            while parent_id and parent_id not in visited:
                visited.add(parent_id)
                snapshot.category_item_ids.setdefault(parent_id, set()).update(item_ids)
                parent = snapshot.categories.get(parent_id)
                parent_id = parent.parent_id if parent else None

        for category_id, category in snapshot.categories.items():
            category.item_count = len(snapshot.category_item_ids.get(category_id, set()))
