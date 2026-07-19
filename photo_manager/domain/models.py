"""Framework-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class PhotoItem:
    """A still photo, Live Photo pair, or unbound MOV component."""

    item_id: str
    display_name: str
    files: list[Path]
    size_bytes: int
    representative_image: Path
    is_live: bool
    item_type: str
    shot_time: datetime
    time_source: str
    source_folder: Path
    stable_key: str = ""
    file_signature: str = ""
    meta_cached: bool = False
    item_kind: str = "photo"
    bound_image_paths: list[Path] = field(default_factory=list)
    needs_binding: bool = False
    camera_make: str = ""
    camera_model: str = ""
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    image_width: int = 0
    image_height: int = 0
    library_root: Optional[Path] = None
    p2_event_id: str = ""
    p2_event_name: str = ""
    p2_burst_group_id: str = ""
    p2_burst_name: str = ""
    p2_is_screenshot: bool = False
    p2_duplicate_group_id: str = ""
    p2_duplicate_name: str = ""
    p2_similar_group_id: str = ""
    p2_similar_name: str = ""
    p2_blur_score: Optional[float] = None
    p2_is_blurry: bool = False
    p2_perceptual_hash: str = ""
    p2_content_labels: list[str] = field(default_factory=list)
    p2_face_clusters: list[str] = field(default_factory=list)
    p2_custom_categories: list[str] = field(default_factory=list)
    p2_favorite: bool = False
    p2_rating: int = 0
    p2_manual_tags: list[str] = field(default_factory=list)
