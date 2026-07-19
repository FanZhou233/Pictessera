"""P2 Plus/AI 轻量分析模块。

本模块只生成可复算的虚拟分类特征，不修改、移动或重命名原始文件。
需要重型 AI 模型的能力以“外部标签/人工标签”形式接入，避免强制增加轻量版体积。
"""

from __future__ import annotations

import hashlib
import fnmatch
import re
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageFilter, ImageOps, ImageStat

from photo_manager.domain import (
    CustomRuleDefinition,
    ImageFeatureRecord,
    PhotoItem,
    UserItemMetadata,
)
from photo_manager.services.content_analysis import ContentAnalysisProvider, reliable_labels


def stable_token(value: str, length: int = 12) -> str:
    """把任意文本转为稳定、短小、适合 category_id 的 token。"""

    text = str(value or "").strip()
    if not text:
        return "unknown"
    digest = hashlib.sha1(text.encode("utf-8", errors="surrogatepass")).hexdigest()
    return digest[: max(6, length)]


def clean_label(value: str) -> str:
    """清理用户/分析标签，避免产生空分类。"""

    label = re.sub(r"\s+", " ", str(value or "")).strip()
    return label[:80]


def clone_items_for_classification(items: list[PhotoItem]) -> list[PhotoItem]:
    """为后台分类创建浅拷贝，避免 worker 直接写 UI 正在持有的对象。"""

    clones: list[PhotoItem] = []
    for item in items:
        clone = replace(item)
        clone.files = list(item.files)
        clone.bound_image_paths = list(item.bound_image_paths)
        clone.p2_content_labels = list(item.p2_content_labels)
        clone.p2_face_clusters = list(item.p2_face_clusters)
        clone.p2_custom_categories = list(item.p2_custom_categories)
        clone.p2_manual_tags = list(item.p2_manual_tags)
        clones.append(clone)
    return clones


class PlusFeatureAnalyzer:
    """给 PhotoItem 批量补充 P2 派生字段。"""

    def __init__(
        self,
        *,
        event_gap_minutes: int = 240,
        burst_gap_seconds: float = 3.0,
        blur_threshold: float = 90.0,
        similar_hamming_threshold: int = 6,
        content_provider: ContentAnalysisProvider | None = None,
        content_confidence_threshold: float = 0.35,
    ):
        self.event_gap_seconds = max(60, int(event_gap_minutes * 60))
        self.burst_gap_seconds = max(0.5, float(burst_gap_seconds))
        self.blur_threshold = float(blur_threshold)
        self.similar_hamming_threshold = int(similar_hamming_threshold)
        self.content_provider = content_provider
        self.content_confidence_threshold = max(0.0, min(1.0, float(content_confidence_threshold)))

    def enrich_items(
        self,
        items: list[PhotoItem],
        *,
        user_metadata: Mapping[str, UserItemMetadata] | None = None,
        feature_cache: Mapping[str, ImageFeatureRecord] | None = None,
        custom_rules: list[CustomRuleDefinition] | None = None,
        read_pixels: bool = True,
        stop_event=None,
    ) -> dict[str, ImageFeatureRecord]:
        """批量分析并返回需要写回缓存的图像特征。"""

        metadata_map = user_metadata or {}
        cache_map = feature_cache or {}
        records_to_save: dict[str, ImageFeatureRecord] = {}
        raw_signatures: dict[str, str] = {}

        for item in items:
            if self._stopped(stop_event):
                break
            stable_key = item.stable_key or item.item_id
            raw_signature = item.file_signature or ""
            raw_signatures[stable_key] = raw_signature
            self._reset_p2_fields(item)
            self._apply_user_metadata(item, metadata_map.get(stable_key))
            record = self._feature_record_for_item(
                item,
                self._feature_cache_signature(raw_signature),
                cache_map.get(stable_key),
                read_pixels=read_pixels,
            )
            self._apply_feature_record(item, record)
            self._apply_custom_rules(item, custom_rules or [])
            records_to_save[stable_key] = record

        if not self._stopped(stop_event):
            self._assign_events(items)
            self._assign_bursts(items)
            self._assign_duplicate_groups(items)
            self._assign_similar_groups(items)

        for item in items:
            stable_key = item.stable_key or item.item_id
            base_signature = raw_signatures.get(stable_key, item.file_signature or "")
            item.file_signature = (
                f"{base_signature}|p2:{self._classification_fingerprint(item)}"
            )
        return records_to_save

    def _feature_cache_signature(self, file_signature: str) -> str:
        if self.content_provider is None:
            return file_signature
        provider_key = str(
            getattr(self.content_provider, "cache_key", None)
            or getattr(self.content_provider, "name", type(self.content_provider).__name__)
        )
        payload = f"{provider_key}|threshold:{self.content_confidence_threshold:.4f}"
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{file_signature}|content:{digest}"

    @staticmethod
    def _stopped(stop_event) -> bool:
        try:
            return bool(stop_event and stop_event.is_set())
        except Exception:
            return False

    @staticmethod
    def _reset_p2_fields(item: PhotoItem) -> None:
        item.p2_event_id = ""
        item.p2_event_name = ""
        item.p2_burst_group_id = ""
        item.p2_burst_name = ""
        item.p2_is_screenshot = False
        item.p2_duplicate_group_id = ""
        item.p2_duplicate_name = ""
        item.p2_similar_group_id = ""
        item.p2_similar_name = ""
        item.p2_blur_score = None
        item.p2_is_blurry = False
        item.p2_perceptual_hash = ""
        item.p2_content_labels = []
        item.p2_face_clusters = []
        item.p2_custom_categories = []
        item.p2_favorite = False
        item.p2_rating = 0
        item.p2_manual_tags = []

    @staticmethod
    def _apply_user_metadata(
        item: PhotoItem,
        metadata: UserItemMetadata | None,
    ) -> None:
        if metadata is None:
            return
        item.p2_favorite = bool(metadata.favorite)
        item.p2_rating = max(0, min(5, int(metadata.rating or 0)))
        item.p2_manual_tags = PlusFeatureAnalyzer._unique_labels(metadata.tags)
        item.p2_face_clusters = PlusFeatureAnalyzer._unique_labels(
            metadata.face_clusters
        )
        item.p2_content_labels = PlusFeatureAnalyzer._unique_labels(
            metadata.content_labels
        )
        item.p2_custom_categories = PlusFeatureAnalyzer._unique_labels(
            metadata.custom_categories
        )

    def _feature_record_for_item(
        self,
        item: PhotoItem,
        raw_signature: str,
        cached: ImageFeatureRecord | None,
        *,
        read_pixels: bool = True,
    ) -> ImageFeatureRecord:
        stable_key = item.stable_key or item.item_id
        if cached and cached.signature == raw_signature:
            return ImageFeatureRecord(
                stable_key=stable_key,
                signature=raw_signature,
                perceptual_hash=cached.perceptual_hash,
                blur_score=cached.blur_score,
                is_blurry=bool(cached.is_blurry),
                is_screenshot=bool(cached.is_screenshot),
                content_labels=list(cached.content_labels),
                updated_at=cached.updated_at,
            )
        is_screenshot = self._detect_screenshot(item)
        labels = self._labels_from_dimensions(item)
        if is_screenshot:
            labels.append("截图")

        perceptual_hash = ""
        blur_score: float | None = None
        image_path = item.representative_image
        if (
            read_pixels
            and
            getattr(item, "item_kind", "photo") != "mov_only"
            and image_path
            and Path(image_path).exists()
        ):
            try:
                features = self._read_image_features(Path(image_path))
                perceptual_hash = features["perceptual_hash"]
                blur_score = features["blur_score"]
                labels.extend(features["content_labels"])
                if not item.image_width:
                    item.image_width = int(features["width"])
                if not item.image_height:
                    item.image_height = int(features["height"])
            except Exception:
                pass
        is_blurry = blur_score is not None and blur_score < self.blur_threshold
        return ImageFeatureRecord(
            stable_key=stable_key,
            signature=raw_signature,
            perceptual_hash=perceptual_hash,
            blur_score=blur_score,
            is_blurry=is_blurry,
            is_screenshot=is_screenshot,
            content_labels=self._unique_labels(labels),
            updated_at=time.time(),
        )

    @staticmethod
    def _apply_feature_record(item: PhotoItem, record: ImageFeatureRecord) -> None:
        item.p2_perceptual_hash = str(record.perceptual_hash or "")
        item.p2_blur_score = record.blur_score
        item.p2_is_blurry = bool(record.is_blurry)
        item.p2_is_screenshot = bool(record.is_screenshot)
        item.p2_content_labels = PlusFeatureAnalyzer._unique_labels(
            [*item.p2_content_labels, *record.content_labels]
        )

    @staticmethod
    def _apply_custom_rules(
        item: PhotoItem,
        rules: list[CustomRuleDefinition],
    ) -> None:
        for rule in rules:
            if not rule.enabled:
                continue
            if PlusFeatureAnalyzer._custom_rule_matches(item, rule):
                label = clean_label(rule.category or rule.name)
                if label:
                    item.p2_custom_categories = PlusFeatureAnalyzer._unique_labels(
                        [*item.p2_custom_categories, label]
                    )

    @staticmethod
    def _custom_rule_matches(item: PhotoItem, rule: CustomRuleDefinition) -> bool:
        values = PlusFeatureAnalyzer._custom_rule_values(item, rule.field)
        operator = str(rule.operator or "contains").strip().lower()
        needle = str(rule.value or "").strip()
        if operator == "exists":
            return any(str(value).strip() for value in values)
        if operator in {"min", "max"}:
            try:
                target = float(needle)
            except ValueError:
                return False
            numbers: list[float] = []
            for value in values:
                try:
                    numbers.append(float(value))
                except (TypeError, ValueError):
                    pass
            if not numbers:
                return False
            return (
                any(number >= target for number in numbers)
                if operator == "min"
                else any(number <= target for number in numbers)
            )
        needle_folded = needle.casefold()
        for value in values:
            text = str(value or "")
            folded = text.casefold()
            if operator == "equals" and folded == needle_folded:
                return True
            if operator == "contains" and needle_folded in folded:
                return True
            if operator == "glob" and fnmatch.fnmatchcase(folded, needle_folded):
                return True
            if operator == "regex":
                try:
                    if re.search(needle, text, flags=re.IGNORECASE):
                        return True
                except re.error:
                    return False
        return False

    @staticmethod
    def _custom_rule_values(item: PhotoItem, field_name: str) -> list[str]:
        field = str(field_name or "").strip().lower()
        mapping = {
            "name": [item.display_name],
            "display_name": [item.display_name],
            "filename": [item.display_name, *(path.name for path in item.files)],
            "path": [*(str(path) for path in item.files)],
            "source_folder": [str(item.source_folder)],
            "item_type": [item.item_type],
            "camera_make": [item.camera_make],
            "camera_model": [item.camera_model],
            "content_label": list(item.p2_content_labels),
            "manual_tag": list(item.p2_manual_tags),
            "face_cluster": list(item.p2_face_clusters),
            "rating": [str(int(item.p2_rating or 0))],
            "favorite": [str(bool(item.p2_favorite)).lower()],
            "is_screenshot": [str(bool(item.p2_is_screenshot)).lower()],
            "is_blurry": [str(bool(item.p2_is_blurry)).lower()],
            "suffix": [path.suffix for path in item.files],
        }
        return [str(value) for value in mapping.get(field, []) if value is not None]

    def _read_image_features(self, path: Path) -> dict[str, object]:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            width, height = int(image.width), int(image.height)
            grayscale = ImageOps.grayscale(image)
            perceptual_hash = self._average_hash(grayscale)
            blur_score = self._blur_score(grayscale)
            labels = self._labels_from_image(image, grayscale)
        if self.content_provider is not None:
            try:
                labels.extend(
                    reliable_labels(
                        self.content_provider.analyze(path),
                        threshold=self.content_confidence_threshold,
                    )
                )
            except Exception:
                # A missing/corrupt optional model must never disable the lightweight
                # image features or ordinary library browsing.
                pass
        return {
            "width": width,
            "height": height,
            "perceptual_hash": perceptual_hash,
            "blur_score": blur_score,
            "content_labels": labels,
        }

    @staticmethod
    def _average_hash(grayscale: Image.Image) -> str:
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        small = grayscale.resize((8, 8), resampling)
        pixels = [int(pixel) for pixel in small.getdata()]
        mean = sum(pixels) / max(1, len(pixels))
        value = 0
        for pixel in pixels:
            value = (value << 1) | (1 if pixel >= mean else 0)
        return f"{value:016x}"

    @staticmethod
    def _blur_score(grayscale: Image.Image) -> float:
        resampling = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
        width, height = grayscale.size
        longest = max(width, height)
        if longest > 256:
            scale = 256 / float(longest)
            grayscale = grayscale.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                resampling,
            )
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        return float(sum(stat.var) / max(1, len(stat.var)))

    @staticmethod
    def _labels_from_image(image: Image.Image, grayscale: Image.Image) -> list[str]:
        labels: list[str] = []
        gray_stat = ImageStat.Stat(grayscale)
        brightness = float(gray_stat.mean[0]) if gray_stat.mean else 0.0
        if brightness < 55:
            labels.append("偏暗照片")
        elif brightness > 210:
            labels.append("高亮照片")

        try:
            hsv = image.convert("HSV")
            saturation = float(ImageStat.Stat(hsv).mean[1])
            if saturation < 28 and brightness > 145:
                labels.append("文档倾向")
            elif saturation > 120:
                labels.append("高饱和")
        except Exception:
            pass
        return labels

    @staticmethod
    def _labels_from_dimensions(item: PhotoItem) -> list[str]:
        width = int(item.image_width or 0)
        height = int(item.image_height or 0)
        if width <= 0 or height <= 0:
            return []
        if width >= height * 1.2:
            return ["横图", "风景构图"]
        if height >= width * 1.2:
            return ["竖图"]
        return ["方图"]

    @staticmethod
    def _detect_screenshot(item: PhotoItem) -> bool:
        text = " ".join(
            [
                item.display_name,
                *(path.name for path in item.files),
            ]
        ).casefold()
        keywords = (
            "screenshot",
            "screen shot",
            "screen-shot",
            "截屏",
            "截图",
            "屏幕快照",
            "屏幕截图",
        )
        if any(keyword.casefold() in text for keyword in keywords):
            return True
        width = int(item.image_width or 0)
        height = int(item.image_height or 0)
        if width <= 0 or height <= 0:
            return False
        dims = {width, height}
        common_edges = {
            750,
            828,
            1080,
            1125,
            1170,
            1242,
            1284,
            1440,
            1620,
            1920,
            2208,
            2436,
            2532,
            2556,
            2560,
            2688,
            2778,
            2796,
            2960,
            3040,
        }
        has_camera = bool((item.camera_make or item.camera_model or "").strip())
        suffixes = {path.suffix.lower() for path in item.files}
        return not has_camera and bool(dims & common_edges) and (
            ".png" in suffixes or ".webp" in suffixes or ".jpg" in suffixes
        )

    def _assign_events(self, items: list[PhotoItem]) -> None:
        dated_items = [
            item
            for item in items
            if isinstance(item.shot_time, datetime) and item.shot_time != datetime.min
        ]
        dated_items.sort(key=lambda item: (item.shot_time, item.display_name.lower()))
        groups: list[list[PhotoItem]] = []
        current: list[PhotoItem] = []
        for item in dated_items:
            if not current:
                current = [item]
                continue
            previous = current[-1]
            same_day = item.shot_time.date() == previous.shot_time.date()
            gap = abs((item.shot_time - previous.shot_time).total_seconds())
            if same_day and gap <= self.event_gap_seconds:
                current.append(item)
            else:
                if len(current) >= 2:
                    groups.append(current)
                current = [item]
        if len(current) >= 2:
            groups.append(current)

        for index, group in enumerate(groups, 1):
            first, last = group[0], group[-1]
            seed = "|".join(
                [
                    first.shot_time.date().isoformat(),
                    first.shot_time.isoformat(timespec="seconds"),
                    last.shot_time.isoformat(timespec="seconds"),
                    str(len(group)),
                    stable_token(group[0].source_folder.as_posix()),
                ]
            )
            event_id = f"event:{stable_token(seed)}"
            if first.shot_time.date() == last.shot_time.date():
                name = (
                    f"{first.shot_time:%Y-%m-%d} 事件 {index} "
                    f"{first.shot_time:%H:%M}-{last.shot_time:%H:%M}"
                )
            else:
                name = f"{first.shot_time:%Y-%m-%d} 至 {last.shot_time:%Y-%m-%d} 事件 {index}"
            for item in group:
                item.p2_event_id = event_id
                item.p2_event_name = name

    def _assign_bursts(self, items: list[PhotoItem]) -> None:
        by_context: dict[tuple[str, str, str], list[PhotoItem]] = defaultdict(list)
        for item in items:
            if item.shot_time == datetime.min:
                continue
            context = (
                str(item.source_folder).casefold(),
                item.shot_time.date().isoformat(),
                (item.camera_model or "").casefold(),
            )
            by_context[context].append(item)

        group_index = 0
        for context_items in by_context.values():
            context_items.sort(key=lambda item: (item.shot_time, item.display_name.lower()))
            current: list[PhotoItem] = []
            for item in context_items:
                if not current:
                    current = [item]
                    continue
                gap = abs((item.shot_time - current[-1].shot_time).total_seconds())
                if gap <= self.burst_gap_seconds:
                    current.append(item)
                else:
                    group_index = self._commit_burst_group(current, group_index)
                    current = [item]
            self._commit_burst_group(current, group_index)
            if len(current) >= 3:
                group_index += 1

    def _commit_burst_group(self, group: list[PhotoItem], group_index: int) -> int:
        if len(group) < 3:
            return group_index
        next_index = group_index + 1
        seed = "|".join(
            [
                group[0].shot_time.isoformat(timespec="seconds"),
                group[-1].shot_time.isoformat(timespec="seconds"),
                str(len(group)),
                group[0].display_name,
            ]
        )
        group_id = f"burst:{stable_token(seed)}"
        name = f"连拍组 {next_index}（{len(group)} 项）"
        for item in group:
            item.p2_burst_group_id = group_id
            item.p2_burst_name = name
        return next_index

    def _assign_duplicate_groups(self, items: list[PhotoItem]) -> None:
        by_hash: dict[str, list[PhotoItem]] = defaultdict(list)
        for item in items:
            if item.p2_perceptual_hash:
                by_hash[item.p2_perceptual_hash].append(item)
        duplicate_index = 0
        for hash_value, group in sorted(by_hash.items()):
            if len(group) < 2:
                continue
            duplicate_index += 1
            group_id = f"duplicate:{stable_token(hash_value)}"
            name = f"重复组 {duplicate_index}（{len(group)} 项）"
            for item in group:
                item.p2_duplicate_group_id = group_id
                item.p2_duplicate_name = name

    def _assign_similar_groups(self, items: list[PhotoItem]) -> None:
        hash_items = [
            item
            for item in items
            if item.p2_perceptual_hash and not item.p2_duplicate_group_id
        ]
        if len(hash_items) < 2:
            return
        hashes = sorted({item.p2_perceptual_hash for item in hash_items})
        hash_to_items: dict[str, list[PhotoItem]] = defaultdict(list)
        for item in hash_items:
            hash_to_items[item.p2_perceptual_hash].append(item)

        parent = {hash_value: hash_value for hash_value in hashes}

        def find(value: str) -> str:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left: str, right: str) -> None:
            lroot, rroot = find(left), find(right)
            if lroot != rroot:
                parent[rroot] = lroot

        if len(hashes) <= 2000:
            for index, left in enumerate(hashes):
                for right in hashes[index + 1 :]:
                    if self._hamming_distance(left, right) <= self.similar_hamming_threshold:
                        union(left, right)
        else:
            buckets: dict[str, list[str]] = defaultdict(list)
            for hash_value in hashes:
                buckets[hash_value[:4]].append(hash_value)
                buckets[hash_value[-4:]].append(hash_value)
            for bucket in buckets.values():
                unique_bucket = sorted(set(bucket))
                for index, left in enumerate(unique_bucket):
                    for right in unique_bucket[index + 1 :]:
                        if self._hamming_distance(left, right) <= self.similar_hamming_threshold:
                            union(left, right)

        components: dict[str, list[str]] = defaultdict(list)
        for hash_value in hashes:
            components[find(hash_value)].append(hash_value)

        similar_index = 0
        for component_hashes in components.values():
            group: list[PhotoItem] = []
            for hash_value in component_hashes:
                group.extend(hash_to_items[hash_value])
            if len(group) < 2:
                continue
            similar_index += 1
            seed = "|".join(sorted(component_hashes))
            group_id = f"similar:{stable_token(seed)}"
            name = f"相似组 {similar_index}（{len(group)} 项）"
            for item in group:
                item.p2_similar_group_id = group_id
                item.p2_similar_name = name

    @staticmethod
    def _hamming_distance(left: str, right: str) -> int:
        try:
            return bin(int(left, 16) ^ int(right, 16)).count("1")
        except Exception:
            return 64

    @staticmethod
    def _classification_fingerprint(item: PhotoItem) -> str:
        payload = "|".join(
            [
                item.p2_event_id,
                item.p2_burst_group_id,
                "screenshot" if item.p2_is_screenshot else "",
                item.p2_duplicate_group_id,
                item.p2_similar_group_id,
                "blurry" if item.p2_is_blurry else "",
                item.p2_perceptual_hash,
                ",".join(sorted(item.p2_content_labels)),
                ",".join(sorted(item.p2_face_clusters)),
                ",".join(sorted(item.p2_custom_categories)),
                "favorite" if item.p2_favorite else "",
                str(int(item.p2_rating or 0)),
                ",".join(sorted(item.p2_manual_tags)),
            ]
        )
        return stable_token(payload, 16)

    @staticmethod
    def _unique_labels(values) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            label = clean_label(str(value))
            key = label.casefold()
            if label and key not in seen:
                labels.append(label)
                seen.add(key)
        return labels
