"""自动分类领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CategoryType(str, Enum):
    """P0 自动分类类型。"""

    TIME = "time"
    MEDIA = "media"
    FILE = "file"
    DEVICE = "device"
    LOCATION = "location"
    SOURCE = "source"
    ERROR = "error"
    EVENT = "event"
    BURST = "burst"
    SCREENSHOT = "screenshot"
    DUPLICATE = "duplicate"
    SIMILAR = "similar"
    QUALITY = "quality"
    FACE = "face"
    CONTENT = "content"
    CUSTOM = "custom"
    USER = "user"


@dataclass
class AutoCategory:
    """一个稳定、可嵌套的虚拟分类。"""

    category_id: str
    category_type: CategoryType
    name: str
    parent_id: str | None = None
    sort_key: str = ""
    rule_version: int = 1
    item_count: int = 0


@dataclass(frozen=True)
class ItemCategoryRelation:
    """照片项目与虚拟分类之间的多对多关系。"""

    stable_key: str
    category_id: str
    classified_at: str
    source_signature: str
