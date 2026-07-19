"""P2 Plus/AI 模块分类规则。"""

from __future__ import annotations

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem
from photo_manager.services.plus_analysis import clean_label, stable_token


def _category_id(prefix: str, label: str) -> str:
    return f"{prefix}:{stable_token(label)}"


class PlusAIRule:
    """把 P2 分析/人工元数据映射为虚拟分类。"""

    rule_version = 1

    def base_categories(self) -> list[AutoCategory]:
        return [
            AutoCategory("event", CategoryType.EVENT, "事件", sort_key="70"),
            AutoCategory("burst", CategoryType.BURST, "连拍", sort_key="71"),
            AutoCategory(
                "burst:all",
                CategoryType.BURST,
                "连拍照片",
                "burst",
                "10",
                self.rule_version,
            ),
            AutoCategory(
                "screenshot",
                CategoryType.SCREENSHOT,
                "屏幕截图",
                sort_key="72",
            ),
            AutoCategory(
                "screenshot:detected",
                CategoryType.SCREENSHOT,
                "屏幕截图",
                "screenshot",
                "10",
                self.rule_version,
            ),
            AutoCategory(
                "duplicate",
                CategoryType.DUPLICATE,
                "重复照片",
                sort_key="73",
            ),
            AutoCategory(
                "duplicate:all",
                CategoryType.DUPLICATE,
                "重复照片",
                "duplicate",
                "10",
                self.rule_version,
            ),
            AutoCategory("similar", CategoryType.SIMILAR, "相似照片", sort_key="74"),
            AutoCategory(
                "similar:all",
                CategoryType.SIMILAR,
                "相似照片",
                "similar",
                "10",
                self.rule_version,
            ),
            AutoCategory("quality", CategoryType.QUALITY, "质量", sort_key="75"),
            AutoCategory(
                "quality:blurry",
                CategoryType.QUALITY,
                "模糊照片",
                "quality",
                "10",
                self.rule_version,
            ),
            AutoCategory("face", CategoryType.FACE, "人脸聚类", sort_key="76"),
            AutoCategory("content", CategoryType.CONTENT, "内容识别", sort_key="77"),
            AutoCategory("custom", CategoryType.CUSTOM, "自定义分类", sort_key="78"),
            AutoCategory("user", CategoryType.USER, "我的标记", sort_key="79"),
            AutoCategory(
                "user:favorite",
                CategoryType.USER,
                "收藏",
                "user",
                "10",
                self.rule_version,
            ),
            *[
                AutoCategory(
                    f"user:rating:{rating}",
                    CategoryType.USER,
                    f"{rating} 星",
                    "user",
                    f"20:{rating}",
                    self.rule_version,
                )
                for rating in range(5, 0, -1)
            ],
            AutoCategory(
                "user:tags",
                CategoryType.USER,
                "人工标签",
                "user",
                "30",
                self.rule_version,
            ),
        ]

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        categories: list[AutoCategory] = []
        if item.p2_event_id:
            categories.append(
                AutoCategory(
                    item.p2_event_id,
                    CategoryType.EVENT,
                    item.p2_event_name or "事件",
                    "event",
                    item.p2_event_id,
                    self.rule_version,
                )
            )
        if item.p2_burst_group_id:
            categories.append(self._static("burst:all", CategoryType.BURST, "连拍照片", "burst", "10"))
            categories.append(
                AutoCategory(
                    item.p2_burst_group_id,
                    CategoryType.BURST,
                    item.p2_burst_name or "连拍组",
                    "burst:all",
                    item.p2_burst_group_id,
                    self.rule_version,
                )
            )
        if item.p2_is_screenshot:
            categories.append(
                self._static(
                    "screenshot:detected",
                    CategoryType.SCREENSHOT,
                    "屏幕截图",
                    "screenshot",
                    "10",
                )
            )
        if item.p2_duplicate_group_id:
            categories.append(
                self._static(
                    "duplicate:all",
                    CategoryType.DUPLICATE,
                    "重复照片",
                    "duplicate",
                    "10",
                )
            )
            categories.append(
                AutoCategory(
                    item.p2_duplicate_group_id,
                    CategoryType.DUPLICATE,
                    item.p2_duplicate_name or "重复组",
                    "duplicate:all",
                    item.p2_duplicate_group_id,
                    self.rule_version,
                )
            )
        if item.p2_similar_group_id:
            categories.append(
                self._static(
                    "similar:all",
                    CategoryType.SIMILAR,
                    "相似照片",
                    "similar",
                    "10",
                )
            )
            categories.append(
                AutoCategory(
                    item.p2_similar_group_id,
                    CategoryType.SIMILAR,
                    item.p2_similar_name or "相似组",
                    "similar:all",
                    item.p2_similar_group_id,
                    self.rule_version,
                )
            )
        if item.p2_is_blurry:
            categories.append(
                self._static(
                    "quality:blurry",
                    CategoryType.QUALITY,
                    "模糊照片",
                    "quality",
                    "10",
                )
            )
        categories.extend(
            self._label_categories(
                values=item.p2_face_clusters,
                prefix="face:cluster",
                category_type=CategoryType.FACE,
                parent_id="face",
                sort_prefix="10",
            )
        )
        categories.extend(
            self._label_categories(
                values=item.p2_content_labels,
                prefix="content:label",
                category_type=CategoryType.CONTENT,
                parent_id="content",
                sort_prefix="10",
            )
        )
        categories.extend(
            self._label_categories(
                values=item.p2_custom_categories,
                prefix="custom:category",
                category_type=CategoryType.CUSTOM,
                parent_id="custom",
                sort_prefix="10",
            )
        )
        if item.p2_favorite:
            categories.append(
                self._static(
                    "user:favorite",
                    CategoryType.USER,
                    "收藏",
                    "user",
                    "10",
                )
            )
        rating = max(0, min(5, int(item.p2_rating or 0)))
        if rating:
            categories.append(
                self._static(
                    f"user:rating:{rating}",
                    CategoryType.USER,
                    f"{rating} 星",
                    "user",
                    f"20:{rating}",
                )
            )
        for category in self._label_categories(
            values=item.p2_manual_tags,
            prefix="user:tag",
            category_type=CategoryType.USER,
            parent_id="user:tags",
            sort_prefix="30",
        ):
            categories.append(
                self._static(
                    "user:tags",
                    CategoryType.USER,
                    "人工标签",
                    "user",
                    "30",
                )
            )
            categories.append(category)
        return categories

    def _static(
        self,
        category_id: str,
        category_type: CategoryType,
        name: str,
        parent_id: str,
        sort_key: str,
    ) -> AutoCategory:
        return AutoCategory(
            category_id,
            category_type,
            name,
            parent_id,
            sort_key,
            self.rule_version,
        )

    def _label_categories(
        self,
        *,
        values: list[str],
        prefix: str,
        category_type: CategoryType,
        parent_id: str,
        sort_prefix: str,
    ) -> list[AutoCategory]:
        categories: list[AutoCategory] = []
        for value in values or []:
            label = clean_label(value)
            if not label:
                continue
            categories.append(
                AutoCategory(
                    _category_id(prefix, label),
                    category_type,
                    label,
                    parent_id,
                    f"{sort_prefix}:{label.casefold()}",
                    self.rule_version,
                )
            )
        return categories
