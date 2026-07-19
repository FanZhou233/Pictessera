"""用户元数据与 P2 图像特征缓存领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserItemMetadata:
    """用户手工维护的虚拟分类元数据。"""

    stable_key: str
    favorite: bool = False
    rating: int = 0
    tags: list[str] = field(default_factory=list)
    face_clusters: list[str] = field(default_factory=list)
    content_labels: list[str] = field(default_factory=list)
    custom_categories: list[str] = field(default_factory=list)
    updated_at: float = 0.0


@dataclass
class ImageFeatureRecord:
    """可缓存、可复算的轻量图像特征。"""

    stable_key: str
    signature: str
    perceptual_hash: str = ""
    blur_score: float | None = None
    is_blurry: bool = False
    is_screenshot: bool = False
    content_labels: list[str] = field(default_factory=list)
    updated_at: float = 0.0


@dataclass
class CustomRuleDefinition:
    """用户自定义分类规则。"""

    rule_id: str
    name: str
    field: str
    operator: str
    value: str = ""
    category: str = ""
    enabled: bool = True
    updated_at: float = 0.0
