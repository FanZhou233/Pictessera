"""Framework-independent photo search helpers."""

from __future__ import annotations

import fnmatch
import shlex
from datetime import datetime
from pathlib import Path
from typing import Iterable

from photo_manager.domain import PhotoItem


SEARCH_SYNONYMS = {
    "苹果": ["apple", "granny smith"],
    "桌子": ["desk", "dining table", "table"],
    "水果": ["fruit", "apple", "orange", "lemon", "banana", "strawberry", "pineapple"],
    "人物": ["person", "man", "woman", "boy", "girl", "groom"],
    "人": ["person", "man", "woman", "boy", "girl"],
    "狗": ["dog", "puppy", "retriever", "terrier", "shepherd"],
    "猫": ["cat", "kitten", "tabby", "siamese"],
    "汽车": ["car", "cab", "taxi", "limousine", "jeep", "minivan"],
    "车": ["car", "cab", "taxi", "bus", "truck", "jeep"],
    "海边": ["seashore", "coast", "lakeside", "beach"],
    "海滩": ["seashore", "coast", "beach"],
    "花": ["flower", "daisy", "rose", "sunflower", "rapeseed"],
    "电脑": ["computer", "desktop computer", "laptop", "notebook", "monitor"],
    "手机": ["cellular telephone", "cellphone", "mobile phone", "smartphone"],
    "建筑": ["building", "palace", "church", "mosque", "monastery"],
}


def wildcard_patterns(query: str) -> list[str]:
    """Build case-insensitive Explorer-style patterns from a user query."""

    text = (query or "").strip().lower()
    if not text:
        return []
    parts = [part.strip() for part in text.replace("|", ";").split(";") if part.strip()]
    return [
        part if any(character in part for character in "*?[") else f"*{part}*"
        for part in (parts or [text])
    ]


def wildcard_query_matches(query: str, fields: Iterable[str]) -> bool:
    """Match Explorer wildcards plus AND, OR and exclusion expressions.

    Examples: ``苹果 桌子`` (AND), ``苹果 | 梨`` (OR), ``水果 -人物``
    and ``标签:苹果``. Semicolons retain their legacy OR behaviour.
    """

    haystacks = [str(field or "").lower() for field in fields if field is not None]
    text = (query or "").strip()
    if not text:
        return False

    # A semicolon has always meant OR in this application.  A bare vertical bar
    # now does the same while whitespace inside each group means AND.
    groups = [group.strip() for group in text.replace(";", "|").split("|") if group.strip()]

    def token_matches(token: str) -> bool:
        token = token.strip()
        if token.casefold().startswith(("标签:", "tag:")):
            token = token.split(":", 1)[1].strip()
        if not token:
            return False
        alternatives = [token, *SEARCH_SYNONYMS.get(token.casefold(), [])]
        for alternative in alternatives:
            pattern = alternative.lower()
            if not any(character in pattern for character in "*?["):
                pattern = f"*{pattern}*"
            for haystack in haystacks:
                candidates = [haystack, Path(haystack).name.lower()]
                if " " in haystack:
                    candidates.extend(haystack.split())
                if any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates):
                    return True
        return False

    for group in groups:
        try:
            tokens = shlex.split(group, posix=False)
        except ValueError:
            tokens = group.split()
        positive = []
        negative = []
        for token in tokens:
            token = token.strip('"\'')
            if token.startswith("-") and len(token) > 1:
                negative.append(token[1:])
            elif token:
                positive.append(token)
        if positive and all(token_matches(token) for token in positive) and not any(
            token_matches(token) for token in negative
        ):
            return True
    return False


def searchable_fields_for_item(
    item: PhotoItem,
    extra_fields: Iterable[str] | None = None,
) -> list[str]:
    """Return normalized fields shared by UI and future CLI search."""

    shot_time = "未知" if item.shot_time == datetime.min else item.shot_time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    fields = [
        item.display_name,
        item.item_type,
        item.time_source,
        shot_time,
        str(item.source_folder),
        item.representative_image.name if item.representative_image else "",
        item.representative_image.suffix if item.representative_image else "",
        "live" if item.is_live else "non-live",
        "待绑定" if item.needs_binding or item.item_kind == "mov_only" else "",
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
    for path in item.files:
        fields.extend((str(path), path.name, path.suffix))
    if extra_fields:
        fields.extend(str(field) for field in extra_fields)
    return fields
