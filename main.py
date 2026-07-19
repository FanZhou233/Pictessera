# -*- coding: utf-8 -*-
"""
Pictessera Photos — local photo and Live Photo library for Windows.

Key change versus earlier Qt/QListWidget/QTableWidget builds:
- Uses QListView + QAbstractListModel and QTableView + QAbstractTableModel.
- Switching between photo wall and table view does NOT recreate thousands of Qt items.
- Large folders stay responsive because the views only ask the model for visible cells.
"""

from __future__ import annotations

import os
import sys
import json
import hashlib
import ctypes
import ctypes.wintypes
import atexit
import shutil
import subprocess
import time
import html
import threading
import weakref
import itertools
import queue
import fnmatch
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from io import BytesIO
from typing import Optional

from photo_manager.bootstrap import configure_qt_environment, resource_path

# SettingsService and the settings UI expose Qt signals, so Qt environment
# policy must be installed before importing the remaining application modules.
configure_qt_environment()

from photo_manager.config import (
    ANIMATION_TIMER_MAX_MS,
    ANIMATION_TIMER_MIN_MS,
    ANIMATION_TIMER_OVERSAMPLE,
    APP_BG,
    APP_BG_2,
    APP_BORDER,
    APP_MUTED,
    APP_PANEL,
    APP_PANEL_2,
    APP_STATE_DIR_NAME,
    LEGACY_APP_STATE_DIR_NAMES,
    PRODUCT_DISPLAY_NAME,
    APP_TEXT,
    ACCENT_BLUE,
    ACCENT_BLUE_DARK,
    BUTTON_OUTER_BG,
    CHECK_ANIM_MS,
    DELETED_ITEMS_DIR_NAME,
    DESELECT_ANIM_MS,
    DETAIL_LIVE_FAST_FRAME_COUNT,
    DETAIL_LIVE_FAST_MAX_LONG,
    DETAIL_LIVE_FAST_TIMEOUT,
    DETAIL_LIVE_HQ_FRAME_COUNT,
    DETAIL_LIVE_HQ_MAX_LONG,
    DETAIL_LIVE_HQ_SIZE,
    DETAIL_LIVE_HQ_TIMEOUT,
    DETAIL_LIVE_PLAYBACK_INTERVAL_MS,
    DETAIL_SHADOW_MARGIN,
    DETAIL_VIEW_BG,
    ENABLE_WINDOWS_HIGH_RES_TIMERS,
    GRID_SINGLE_CLICK_SELECTION_DELAY_MS,
    GRID_SIZE,
    GRID_SPACING,
    ICON_SIZE,
    IMAGE_EXTENSIONS,
    IMAGE_PRIORITY,
    ITEM_INFO_CACHE_FILE_NAME,
    JSON_BACKUP_SUFFIX,
    JSON_CORRUPT_SUFFIX,
    LIVE_PREVIEW_DECODE_TIMEOUT,
    LIVE_PREVIEW_FPS,
    LIVE_PREVIEW_FRAME_COUNT,
    LIVE_WORKERS,
    META_WORKERS,
    MOV_BINDINGS_FILE_NAME,
    PRESS_DOWN_ANIM_MS,
    PRESS_EFFECT_ANIM_MS,
    PRESS_PREVIEW_ANIM_MS,
    PRESS_RELEASE_ANIM_MS,
    PROGRESS_BG,
    RECURSIVE,
    SIDEBAR_BG,
    STATE_VERSION,
    SYSTEM_GRAY_1,
    SYSTEM_GRAY_2,
    SYSTEM_GRAY_3,
    SYSTEM_GRAY_4,
    SYSTEM_GRAY_6,
    TABLE_ICON_SIZE,
    TABLE_ROW_HEIGHT,
    TABLE_SINGLE_CLICK_SELECTION_DELAY_MS,
    THUMB_CACHE_DIR_NAME,
    THUMB_FLUSH_BATCH,
    THUMB_WORKERS,
    TOOLBAR_BG,
    TRASH_JOURNAL_FILE_NAME,
    TRASH_STATE_FILE_NAME,
    UNDO_HISTORY_LIMIT,
    UNDO_SESSION_FILE_NAME,
    VIDEO_EXTENSIONS,
    WINDOW_SHADOW_MARGIN,
    CONTENT_BG,
)
from photo_manager.domain import PhotoItem as PhotoItemData
from photo_manager.infrastructure import (
    AppThreadPoolExecutor,
    CategoryRepository,
    PhotoManagerDatabase,
)
from photo_manager.services import (
    ClassificationService,
    ClassificationSnapshot,
    PlusFeatureAnalyzer,
    SettingsService,
    TransformersImageClassifierProvider,
    TranslationService,
    clone_items_for_classification,
    searchable_fields_for_item,
    windows_apps_use_light_theme,
    wildcard_query_matches,
)
from photo_manager.ui import SettingsDialog
from photo_manager.ui.theme_profiles import (
    make_theme_font,
    normalize_theme_id,
    register_optional_theme_fonts,
    resolve_theme_profile,
    theme_display_point_size,
)
from photo_manager.services.classification_rules import (
    DeviceRule,
    FileRule,
    LocationRule,
    MediaRule,
    PlusAIRule,
    SourceRule,
    TimeRule,
)

from PIL import Image, ImageOps, ImageDraw
from PIL.ExifTags import TAGS, GPSTAGS
try:
    from PIL import ExifTags as PIL_ExifTags
except Exception:
    PIL_ExifTags = None

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

# GPU resize is normally not helpful for HEIC folders, because decoding + disk I/O dominate.
# Heavy optional video/GPU libraries are imported lazily.  Importing OpenCV,
# NumPy and imageio at process startup can add noticeable delay on Windows,
# although they are only needed when decoding LIVE MOV files or using optional
# CUDA resize.  Keep the main window startup path free of those imports.
USE_GPU_RESIZE_AUTO = False
CV2_CUDA_AVAILABLE = False
cv2 = None  # lazy module cache
np = None   # lazy module cache
iio = None  # lazy module cache
imageio_ffmpeg = None  # lazy module cache
_CV2_IMPORT_ATTEMPTED = False
_IMAGEIO_IMPORT_ATTEMPTED = False
_IMAGEIO_FFMPEG_IMPORT_ATTEMPTED = False
_OPTIONAL_IMPORT_LOCK = threading.Lock()


def get_cv2_np(require_cuda: bool = False):
    """Lazily import OpenCV/NumPy only when the fallback decoder really needs them."""
    global cv2, np, CV2_CUDA_AVAILABLE, _CV2_IMPORT_ATTEMPTED
    if _CV2_IMPORT_ATTEMPTED:
        return cv2, np
    with _OPTIONAL_IMPORT_LOCK:
        if _CV2_IMPORT_ATTEMPTED:
            return cv2, np
        _CV2_IMPORT_ATTEMPTED = True
        try:
            import cv2 as _cv2  # type: ignore
            import numpy as _np  # type: ignore
            cv2 = _cv2
            np = _np
            if require_cuda and hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                CV2_CUDA_AVAILABLE = True
        except Exception:
            cv2 = None
            np = None
            CV2_CUDA_AVAILABLE = False
    return cv2, np


def get_imageio_v3():
    """Lazily import imageio.v3; used only as a last-resort LIVE MOV decoder."""
    global iio, _IMAGEIO_IMPORT_ATTEMPTED
    if _IMAGEIO_IMPORT_ATTEMPTED:
        return iio
    with _OPTIONAL_IMPORT_LOCK:
        if _IMAGEIO_IMPORT_ATTEMPTED:
            return iio
        _IMAGEIO_IMPORT_ATTEMPTED = True
        try:
            import imageio.v3 as _iio  # type: ignore
            iio = _iio
        except Exception:
            iio = None
    return iio


def get_imageio_ffmpeg_module():
    """Lazily import imageio-ffmpeg; used only when LIVE preview first needs ffmpeg."""
    global imageio_ffmpeg, _IMAGEIO_FFMPEG_IMPORT_ATTEMPTED
    if _IMAGEIO_FFMPEG_IMPORT_ATTEMPTED:
        return imageio_ffmpeg
    with _OPTIONAL_IMPORT_LOCK:
        if _IMAGEIO_FFMPEG_IMPORT_ATTEMPTED:
            return imageio_ffmpeg
        _IMAGEIO_FFMPEG_IMPORT_ATTEMPTED = True
        try:
            import imageio_ffmpeg as _imageio_ffmpeg  # type: ignore
            imageio_ffmpeg = _imageio_ffmpeg
        except Exception:
            imageio_ffmpeg = None
    return imageio_ffmpeg


from PySide6.QtCore import (
    Qt,
    QSize,
    QObject,
    Signal,
    QTimer,
    QPoint,
    QPointF,
    QEvent,
    QMimeData,
    QModelIndex,
    QAbstractListModel,
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QSignalBlocker,
    QRectF,
    QRect,
    QPropertyAnimation,
    QEasingCurve,
    QUrl,
    QByteArray,
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QPen, QPainterPath, QFont, QFontDatabase, QLinearGradient, QRegion, QCursor, QPalette, QKeySequence, QShortcut, QIcon, QTransform, QDesktopServices
try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QProgressBar,
    QComboBox,
    QStackedWidget,
    QListView,
    QAbstractItemView,
    QAbstractScrollArea,
    QTableView,
    QHeaderView,
    QCheckBox,
    QStyledItemDelegate,
    QStyle,
    QToolTip,
    QDialog,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QSizePolicy,
    QMenu,
    QTextEdit,
    QTextBrowser,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QScrollBar,
    QProxyStyle,
    QStyleOptionSlider,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QStyleOptionViewItem,
)


ITEM_ID_ROLE = Qt.UserRole + 1
IS_LIVE_ROLE = Qt.UserRole + 2
THUMB_READY_ROLE = Qt.UserRole + 3
NEEDS_BINDING_ROLE = Qt.UserRole + 4
CATEGORY_ID_ROLE = Qt.UserRole + 5


SIDEBAR_LIBRARY_ICONS = {
    "所有照片": "photo-stack",
    "实况照片": "live-photo",
    "静态照片": "photo",
    "待绑定视频": "video",
    "最近删除": "trash",
}


_ICON_PIXMAP_CACHE: dict[tuple[str, str, int, int, int, int], QPixmap] = {}
TITLEBAR_STYLE = "macos"
RUNTIME_THEME_STYLE = "light"
RUNTIME_THEME_LOCALE = "zh_CN"
RUNTIME_THEME_PROFILE = resolve_theme_profile("light")


def icon_name_for_auto_category(category_id: str) -> str:
    text = str(category_id or "")
    if text.startswith("time"):
        return "clock"
    if text.startswith("media"):
        return "media"
    if text.startswith("device"):
        return "device"
    if text.startswith("location"):
        return "location"
    if text.startswith("source"):
        return "source"
    if text.startswith("file"):
        return "document"
    if text.startswith("event"):
        return "calendar"
    if text.startswith("burst"):
        return "burst"
    if text.startswith("screenshot"):
        return "screenshot"
    if text.startswith("duplicate") or text.startswith("similar"):
        return "duplicate"
    if text.startswith("quality"):
        return "quality"
    if text.startswith("face"):
        return "face"
    if text.startswith("content"):
        return "sparkles"
    if text.startswith("custom"):
        return "tag"
    if text.startswith("user"):
        return "heart"
    if text.startswith("classification:error"):
        return "warning"
    return "tag"


def current_device_pixel_ratio() -> float:
    try:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            return max(1.0, float(screen.devicePixelRatio()))
    except Exception:
        pass
    return 1.0


def render_ui_icon_pixmap(icon_name: str, color: str, size: QSize | int = 20) -> QPixmap:
    icon_size = size if isinstance(size, QSize) else QSize(int(size), int(size))
    dpr = current_device_pixel_ratio()
    physical_size = QSize(
        max(1, int(round(icon_size.width() * dpr))),
        max(1, int(round(icon_size.height() * dpr))),
    )
    cache_key = (
        icon_name,
        color,
        icon_size.width(),
        icon_size.height(),
        physical_size.width(),
        physical_size.height(),
    )
    cached = _ICON_PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    pixmap = QPixmap(physical_size)
    pixmap.fill(Qt.transparent)
    icon_path = resource_path(f"assets/icons/{icon_name}.svg")
    if QSvgRenderer is not None and icon_path.exists():
        try:
            svg = icon_path.read_text(encoding="utf-8").replace("currentColor", color)
            renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing, True)
            renderer.render(painter, QRectF(0, 0, physical_size.width(), physical_size.height()))
            painter.end()
        except Exception:
            pixmap.fill(Qt.transparent)
    try:
        pixmap.setDevicePixelRatio(dpr)
    except Exception:
        pass
    _ICON_PIXMAP_CACHE[cache_key] = pixmap
    return pixmap


def ui_icon(
    icon_name: str,
    normal_color: str = SYSTEM_GRAY_6,
    selected_color: str = "#FFFFFF",
    active_color: str = ACCENT_BLUE,
    size: QSize | int = 20,
    on_color: Optional[str] = None,
) -> QIcon:
    checked_color = selected_color if on_color is None else on_color
    icon = QIcon()
    icon.addPixmap(render_ui_icon_pixmap(icon_name, normal_color, size), QIcon.Normal, QIcon.Off)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, checked_color, size), QIcon.Normal, QIcon.On)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, active_color, size), QIcon.Active, QIcon.Off)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, active_color, size), QIcon.Active, QIcon.On)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, selected_color, size), QIcon.Selected, QIcon.Off)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, selected_color, size), QIcon.Selected, QIcon.On)
    icon.addPixmap(render_ui_icon_pixmap(icon_name, SYSTEM_GRAY_4, size), QIcon.Disabled, QIcon.Off)
    return icon


class SearchResultsModel(QAbstractTableModel):
    HEADERS = ["名称", "类型", "位置 / 输出"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.results)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def set_results(self, results: list[dict]):
        self.beginResetModel()
        self.results = list(results)
        self.endResetModel()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self.results):
            return None
        item = self.results[row]
        if role == ITEM_ID_ROLE:
            return item.get("item_id", "")
        if role == Qt.DisplayRole:
            if col == 0:
                return item.get("name", "")
            if col == 1:
                return item.get("type", "")
            if col == 2:
                return item.get("location", "")
        if role == Qt.ToolTipRole:
            return item.get("tooltip", "") or f"{item.get('name', '')}\n{item.get('location', '')}"
        return None


# ==========================
# File / EXIF helpers
# ==========================


def fast_iter_files(root: Path, stop_event: threading.Event, *, recursive: bool = True, exclude_patterns: list[str] | None = None):
    patterns = [str(pattern).strip() for pattern in (exclude_patterns or []) if str(pattern).strip()]

    def excluded(path: Path) -> bool:
        try:
            relative = str(path.relative_to(root)).replace("\\", "/")
        except Exception:
            relative = path.name
        return any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern) for pattern in patterns)

    stack = [root]
    while stack:
        if stop_event.is_set():
            return
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if stop_event.is_set():
                        return
                    try:
                        path = Path(entry.path)
                        if excluded(path):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                stack.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            yield path
                    except Exception:
                        continue
        except Exception:
            continue


def _clean_exif_datetime_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    text = str(value).strip().strip("\x00")
    # EXIF sometimes separates timezone with a space, e.g. "2024:01:02 03:04:05 +08:00".
    text = " ".join(text.split())
    return text


def parse_exif_datetime(value, offset_value=None):
    """Parse common EXIF/HEIF date strings into a naive local datetime.

    Camera stills should be sorted by actual capture time whenever metadata is
    available.  The old implementation accepted only a few flat EXIF strings and
    could silently fall back to Windows creation time, which is often the copy or
    import time rather than the shooting time.  This parser accepts fractional
    seconds, ISO-ish separators and the separate EXIF OffsetTime* fields.
    """
    text = _clean_exif_datetime_text(value)
    if not text:
        return None
    offset = _clean_exif_datetime_text(offset_value)
    if offset and (offset[0:1] in ("+", "-") or offset.upper() == "Z") and not (text.endswith("Z") or "+" in text[10:] or "-" in text[10:]):
        text = f"{text}{offset}"
    # Remove sub-second fragments because PIL/cameras disagree on the exact separator.
    # Keep timezone suffix if present.
    if "." in text:
        head, tail = text.split(".", 1)
        tz = ""
        for marker in ("+", "-"):
            pos = tail.find(marker)
            if pos >= 0:
                tz = tail[pos:]
                break
        if not tz and tail.upper().endswith("Z"):
            tz = "Z"
        text = head + tz
    # Python's %z handles +0800 and +08:00, but not bare Z in older versions.
    if text.upper().endswith("Z"):
        text = text[:-1] + "+00:00"
    formats = [
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y:%m:%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=None)
        except Exception:
            pass
    return None


def _flatten_exif_dict(exif) -> dict:
    out = {}
    try:
        for tag_id, value in exif.items():
            name = TAGS.get(tag_id, tag_id)
            out[name] = value
            out[tag_id] = value
    except Exception:
        pass
    # Pillow may keep DateTimeOriginal in the EXIF IFD instead of the flat dict,
    # especially for HEIC/HEIF opened through pillow-heif.
    try:
        if PIL_ExifTags is not None and hasattr(exif, "get_ifd") and hasattr(PIL_ExifTags, "IFD"):
            for ifd_name in ("Exif", "IFD0"):
                try:
                    ifd_key = getattr(PIL_ExifTags.IFD, ifd_name)
                    sub = exif.get_ifd(ifd_key)
                except Exception:
                    sub = None
                if isinstance(sub, dict):
                    for tag_id, value in sub.items():
                        name = TAGS.get(tag_id, tag_id)
                        out[name] = value
                        out[tag_id] = value
    except Exception:
        pass
    return out


def get_exif_time(image_path: Path):
    try:
        if not image_path.exists():
            return None
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return None
            exif_dict = _flatten_exif_dict(exif)
            candidates = [
                ("DateTimeOriginal", "OffsetTimeOriginal"),
                (36867, 36881),
                ("DateTimeDigitized", "OffsetTimeDigitized"),
                (36868, 36882),
                ("DateTime", "OffsetTime"),
                (306, 36880),
            ]
            for key, offset_key in candidates:
                dt = parse_exif_datetime(exif_dict.get(key), exif_dict.get(offset_key))
                if dt:
                    return dt
    except Exception:
        return None
    return None


def get_fast_file_time(path: Path):
    """Cheap fallback time used before metadata is available.

    Prefer file modification time over Windows creation time.  Creation time is
    commonly reset when photos are copied/imported, so using it as a shooting
    time made freshly copied old photos sort incorrectly.
    """
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return datetime.min


def get_fast_group_time(files: list[Path]):
    image_files = [f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS]
    candidates = image_files or files
    times = []
    for f in candidates:
        t = get_fast_file_time(f)
        if t != datetime.min:
            times.append(t)
    return min(times) if times else datetime.min

def choose_representative_image(files: list[Path]):
    image_files = [f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS]
    if not image_files:
        return files[0]
    image_files.sort(
        key=lambda p: IMAGE_PRIORITY.index(p.suffix.lower())
        if p.suffix.lower() in IMAGE_PRIORITY else 999
    )
    return image_files[0]


def extract_best_exif_time_for_item(item: PhotoItemData):
    image_files = [f for f in item.files if f.suffix.lower() in IMAGE_EXTENSIONS]
    image_files.sort(
        key=lambda p: IMAGE_PRIORITY.index(p.suffix.lower())
        if p.suffix.lower() in IMAGE_PRIORITY else 999
    )
    for img in image_files:
        dt = get_exif_time(img)
        if dt:
            return dt
    return None


def make_unique_target_paths(target_dir: Path, files: list[Path]):
    representative = choose_representative_image(files)
    base_stem = representative.stem
    idx = 0
    while True:
        suffix = "" if idx == 0 else f"_{idx}"
        target_paths = [target_dir / f"{base_stem}{suffix}{f.suffix}" for f in files]
        if all(not p.exists() for p in target_paths):
            return target_paths
        idx += 1


def safe_file_size(path: Path) -> int:
    try:
        return max(0, path.stat().st_size)
    except Exception:
        return 0


def group_size_bytes(files: list[Path]) -> int:
    return sum(safe_file_size(f) for f in files)


def format_bytes(num: int) -> str:
    num = int(max(0, num))
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"


def format_time(dt: datetime):
    return "未知" if dt == datetime.min else dt.strftime("%Y-%m-%d %H:%M:%S")


# ==========================
# Persistent state / cache helpers
# ==========================

def app_base_dir() -> Path:
    """Return the directory that owns the running program.

    For a normal .py launch this is the script directory.  For a packaged exe
    this is the executable directory.  This keeps trash records, list caches and
    thumbnails beside the application instead of writing into system profile
    folders such as AppData.
    """
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent
    except Exception:
        try:
            return Path.cwd().resolve()
        except Exception:
            return Path(".")


def app_state_dir() -> Path:
    """Return the app-local state/cache directory next to this program.

    On the first Pictessera launch, retain existing settings and caches by
    renaming the old product directory.  If it is temporarily locked, use the
    old location instead of silently creating an empty profile.
    """
    try:
        base_dir = app_base_dir()
        state_dir = base_dir / APP_STATE_DIR_NAME
        if state_dir.exists():
            return state_dir
        for legacy_name in LEGACY_APP_STATE_DIR_NAMES:
            legacy_dir = base_dir / legacy_name
            if not legacy_dir.is_dir():
                continue
            try:
                legacy_dir.rename(state_dir)
                return state_dir
            except OSError:
                return legacy_dir
        return state_dir
    except Exception:
        return Path(".") / APP_STATE_DIR_NAME


def deleted_items_dir() -> Path:
    """Folder used for user-confirmed deletion from the program trash.

    This is intentionally beside the program itself, not under AppData or other
    system profile folders, matching the app-local storage policy.
    """
    try:
        return app_base_dir() / DELETED_ITEMS_DIR_NAME
    except Exception:
        return Path(".") / DELETED_ITEMS_DIR_NAME


def _ensure_deleted_items_dir() -> Path:
    d = deleted_items_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _move_items_to_deleted_folder_worker(entries: list[dict], signals, options: dict | None = None):
    """Move confirmed trash items into the app-local “已删除” directory off the UI thread."""
    total = len(entries)
    done = 0
    deleted_items = 0
    moved_files = 0
    stale_items = 0
    failed: list[str] = []
    success_ids: list[str] = []
    stale_ids: list[str] = []
    failed_ids: list[str] = []

    try:
        target_dir = _ensure_deleted_items_dir()
    except Exception as e:
        msg = f"无法创建‘已删除’文件夹：{e}"
        for entry in entries:
            failed.append(f"{entry.get('display_name') or entry.get('item_id')}: {msg}")
            failed_ids.append(str(entry.get('item_id') or ''))
        try:
            signals.file_op_done.emit({
                "deleted_items": 0, "moved_files": 0, "stale_items": 0,
                "failed": failed, "success_ids": [], "stale_ids": [],
                "failed_ids": failed_ids, "total": total,
                "show_message": bool((options or {}).get("show_message", True)),
                "all_trash": bool((options or {}).get("all_trash", False)),
            })
        except Exception:
            pass
        return

    for entry in list(entries):
        item_id = str(entry.get("item_id") or "")
        display_name = str(entry.get("display_name") or item_id or "未命名项目")
        try:
            files = [Path(x) for x in (entry.get("files") or [])]
            existing_files = [src for src in files if src.exists()]
            if not existing_files:
                stale_items += 1
                stale_ids.append(item_id)
                done += 1
                try:
                    signals.file_op_progress.emit(done, total, f"已清理失效项：{display_name}")
                except Exception:
                    pass
                continue
            target_paths = make_unique_target_paths(target_dir, existing_files)
            moved_pairs: list[tuple[Path, Path]] = []
            moved_this_item = 0
            try:
                for src, dst in zip(existing_files, target_paths):
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    moved_pairs.append((src, dst))
                    moved_files += 1
                    moved_this_item += 1
            except Exception as e:
                for orig, moved in reversed(moved_pairs):
                    try:
                        if moved.exists() and not orig.exists():
                            shutil.move(str(moved), str(orig))
                            moved_files = max(0, moved_files - 1)
                    except Exception:
                        pass
                raise RuntimeError(f"移动到‘已删除’失败：{e}")
            deleted_items += 1
            success_ids.append(item_id)
            done += 1
            try:
                signals.file_op_progress.emit(done, total, f"已移动：{display_name}（{moved_this_item} 个文件）")
            except Exception:
                pass
        except Exception as e:
            failed.append(f"{display_name}: {e}")
            failed_ids.append(item_id)
            done += 1
            try:
                signals.file_op_progress.emit(done, total, f"移动失败：{display_name}")
            except Exception:
                pass

    try:
        signals.file_op_done.emit({
            "deleted_items": deleted_items,
            "moved_files": moved_files,
            "stale_items": stale_items,
            "failed": failed,
            "success_ids": success_ids,
            "stale_ids": stale_ids,
            "failed_ids": failed_ids,
            "total": total,
            "target_dir": str(target_dir),
            "show_message": bool((options or {}).get("show_message", True)),
            "all_trash": bool((options or {}).get("all_trash", False)),
        })
    except Exception:
        pass




def _copy_reordered_export_worker(entries: list[dict], signals, options: dict | None = None):
    """Copy selected items using an iOS-like IMG_#### sequence off the UI thread."""
    total_items = len(entries)
    done = 0
    copied_items = 0
    copied_files = 0
    failed: list[str] = []
    target_dir = str((options or {}).get("target_dir") or "")
    for entry in list(entries):
        display = str(entry.get("display_name") or entry.get("new_base") or "未命名项目")
        try:
            file_pairs = entry.get("file_pairs") or []
            if not file_pairs:
                raise RuntimeError("没有可导出的文件。")
            copied_this = 0
            for pair in file_pairs:
                src = Path(pair.get("src") or "")
                dst = Path(pair.get("dst") or "")
                if not src.exists():
                    raise FileNotFoundError(f"源文件不存在：{src}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                copied_files += 1
                copied_this += 1
            copied_items += 1
            done += 1
            try:
                signals.file_op_progress.emit(done, total_items, f"已重排导出：{display}（{copied_this} 个文件）")
            except Exception:
                pass
        except Exception as e:
            failed.append(f"{display}: {e}")
            done += 1
            try:
                signals.file_op_progress.emit(done, total_items, f"重排导出失败：{display}")
            except Exception:
                pass
    try:
        signals.file_op_done.emit({
            "op": "reorder_export",
            "copied_items": copied_items,
            "copied_files": copied_files,
            "failed": failed,
            "total": total_items,
            "target_dir": target_dir,
            "show_message": bool((options or {}).get("show_message", True)),
        })
    except Exception:
        pass


def _ensure_state_dir() -> Path:
    d = app_state_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / THUMB_CACHE_DIR_NAME).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _fsync_file_object(f) -> None:
    """Best-effort file flush used by persistent state writes."""
    try:
        f.flush()
        os.fsync(f.fileno())
    except Exception:
        pass


def _fsync_directory(path: Path) -> None:
    """Best-effort directory flush for POSIX; harmlessly ignored on Windows."""
    try:
        if os.name == "posix":
            fd = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except Exception:
        pass


def _json_backup_path(path: Path) -> Path:
    return path.with_name(path.name + JSON_BACKUP_SUFFIX)


def _quarantine_corrupt_json(path: Path) -> None:
    """Keep a broken JSON file for debugging instead of repeatedly parsing it."""
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = path.with_name(f"{path.name}{JSON_CORRUPT_SUFFIX}_{stamp}")
        if not target.exists():
            shutil.copy2(path, target)
    except Exception:
        pass


def _read_json_file(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_load(path: Path, default):
    """Load JSON state with .bak fallback.

    The main state file can be interrupted by a power loss, IDE hard stop or OS
    crash.  We first try the primary file, then its last-good backup.  If both
    fail, callers rebuild their view from the scanned folders and start with the
    supplied default.
    """
    expected_type = type(default)
    candidates = [path, _json_backup_path(path)]
    for idx, candidate in enumerate(candidates):
        try:
            if not candidate.exists():
                continue
            data = _read_json_file(candidate)
            if isinstance(data, expected_type):
                # If the backup was the usable copy, opportunistically restore the
                # primary file.  Failure is non-fatal because the backup remains.
                if idx == 1:
                    try:
                        _json_save_atomic(path, data)
                    except Exception:
                        pass
                return data
        except Exception:
            if idx == 0:
                _quarantine_corrupt_json(candidate)
            continue
    return default


def _json_save_atomic(path: Path, data) -> bool:
    """Durably save JSON using temp-file + fsync + os.replace + .bak.

    This keeps app-generated state recoverable even if the program is closed
    from an IDE, killed while saving, or the machine loses power.  A previous
    good copy is retained beside the main file as ``*.bak``.
    """
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(raw)
            f.write("\n")
            _fsync_file_object(f)
        # Validate the temporary file before it can replace the live state.
        _read_json_file(tmp)
        backup = _json_backup_path(path)
        try:
            if path.exists():
                # Only promote the current primary to .bak if it is parseable.
                # If the primary is already corrupt and we are restoring from a
                # backup, never overwrite the last-good backup with bad bytes.
                _read_json_file(path)
                shutil.copy2(path, backup)
                try:
                    with backup.open("r+b") as bf:
                        _fsync_file_object(bf)
                except Exception:
                    pass
        except Exception:
            # Losing or skipping the backup must not block a new known-good primary write.
            pass
        os.replace(tmp, path)
        _fsync_directory(path.parent)
        return True
    except Exception:
        try:
            if tmp is not None and tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def _append_jsonl_durable(path: Path, record: dict) -> bool:
    """Append one JSON record and flush it.

    A crash may leave the last line incomplete; replay code ignores malformed
    tail lines, so earlier operations remain useful for recovery.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            _fsync_file_object(f)
        _fsync_directory(path.parent)
        return True
    except Exception:
        return False


def _load_jsonl_records(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        if not path.exists():
            return records
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
                except Exception:
                    # Ignore a possibly torn final line.
                    continue
    except Exception:
        pass
    return records


def normalize_item_path(path: Path) -> str:
    try:
        text = str(path.resolve())
    except Exception:
        text = str(path.absolute())
    return os.path.normcase(os.path.normpath(text))


def item_paths_for_state(files: list[Path]) -> list[str]:
    return sorted(normalize_item_path(f) for f in files)


def stable_key_for_paths(paths: list[str]) -> str:
    raw = "\n".join(paths).encode("utf-8", errors="surrogatepass")
    return hashlib.sha1(raw).hexdigest()


def stable_key_for_files(files: list[Path]) -> str:
    return stable_key_for_paths(item_paths_for_state(files))


def signature_for_files(files: list[Path]) -> str:
    """Fingerprint current file contents cheaply through path + size + mtime.

    This invalidates stale thumbnail/list-info cache when the folder is updated,
    without blocking the fresh scan from reflecting new files.
    """
    parts = []
    for path in sorted(files, key=lambda p: normalize_item_path(p)):
        npath = normalize_item_path(path)
        try:
            st = path.stat()
            parts.append([npath, int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))])
        except Exception:
            parts.append([npath, -1, -1])
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="surrogatepass")
    return hashlib.sha1(raw).hexdigest()


def iso_from_datetime(dt: datetime) -> str:
    try:
        if dt == datetime.min:
            return ""
        return dt.isoformat(timespec="seconds")
    except Exception:
        return ""


def datetime_from_iso(text: str) -> datetime | None:
    try:
        if not text:
            return None
        return datetime.fromisoformat(text)
    except Exception:
        return None


def is_verified_capture_time_source(source: str) -> bool:
    text = str(source or "")
    return "EXIF" in text or "拍摄时间" in text and "后台补齐" in text


def normalize_meta_state(rec: dict) -> str:
    state = str(rec.get("meta_state") or "").strip().lower()
    if state in {"exif", "no_exif"}:
        return state
    # Backward compatibility for v30-v33 caches.  Only real EXIF-backed cache may
    # suppress a new metadata pass.  The previous versions cached the quick file
    # time too early, which could make copied photos keep an incorrect shooting
    # time forever.
    source = str(rec.get("time_source") or "")
    if "EXIF" in source:
        return "exif"
    return "unknown"


def thumb_cache_path_for_key(stable_key: str) -> Path:
    # Legacy fallback path kept for compatibility with older cache versions.
    return app_state_dir() / THUMB_CACHE_DIR_NAME / f"{stable_key}.png"


def thumb_cache_path_for_item(item: PhotoItemData) -> Path:
    # Include the current file signature in the thumbnail filename so folder
    # updates invalidate stale images without reading a JSON index for each item.
    safe_sig = item.file_signature or "nosig"
    # v2 invalidates placeholders that older builds accidentally persisted when
    # a HEIF/JPEG decoder was unavailable on the target machine.
    return app_state_dir() / THUMB_CACHE_DIR_NAME / f"{item.stable_key}_v2_{safe_sig}.png"


def assign_stable_identity(item: PhotoItemData) -> None:
    item.stable_key = stable_key_for_files(item.files)
    item.file_signature = signature_for_files(item.files)


def live_photo_item_paths_still_exist(paths: list[str]) -> bool:
    """A trash record is valid only while its exact original paths still exist.

    If the user moves/deletes files outside the program, the stale trash entry is
    silently removed on the next scan as requested.
    """
    if not paths:
        return False
    return all(Path(p).exists() for p in paths)


def get_image_display_size(path: Path) -> tuple[int, int] | None:
    """Return the still image's oriented pixel size without full UI-side loading.

    Used by the detail-view LIVE player so navigating while LIVE playback is on
    does not accidentally use the tiny thumbnail/fallback size as the scene size.
    """
    try:
        with Image.open(path) as img:
            w, h = img.size
            try:
                orientation = img.getexif().get(274)
                if orientation in (5, 6, 7, 8):
                    w, h = h, w
            except Exception:
                pass
            if w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        return None
    return None



def clamp01(x: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return 0.0


def ease_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 1.0 - pow(1.0 - t, 3)


def ease_out_quint(t: float) -> float:
    t = clamp01(t)
    return 1.0 - pow(1.0 - t, 5)


def ease_in_out_cubic(t: float) -> float:
    t = clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - pow(-2.0 * t + 2.0, 3) / 2.0


def ease_out_back(t: float, overshoot: float = 1.45) -> float:
    # Back-easing curve with a small overshoot; good for check badges.
    t = clamp01(t) - 1.0
    return 1.0 + (overshoot + 1.0) * t * t * t + overshoot * t * t


def current_screen_refresh_rate(widget=None) -> float:
    """Best-effort display refresh-rate detection for animation timers.

    Qt timers operate in milliseconds.  A fixed 16 ms timer caps animation at
    about 60 FPS, which looks choppy on 120/144/165 Hz displays.  This helper
    asks Qt for the screen refresh rate and falls back safely when unavailable.
    """
    rate = 60.0
    try:
        screen = None
        if widget is not None:
            try:
                handle = widget.window().windowHandle()
                if handle is not None:
                    screen = handle.screen()
            except Exception:
                screen = None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            r = float(screen.refreshRate())
            if 30.0 <= r <= 500.0:
                rate = r
    except Exception:
        pass
    return rate


def adaptive_animation_interval_ms(widget=None) -> int:
    """Return an animation-driver interval that is deliberately faster than vsync.

    A nominal 16 ms QTimer often *feels* below 60 FPS on Windows because timer
    wakeups jitter and paints are coalesced.  The animation uses absolute time,
    so it is safe to tick more often than the display refresh: extra ticks merely
    keep the next repaint ready.

    With the defaults this gives roughly:
      60 Hz  -> 8 ms
      120 Hz -> 4 ms
      144 Hz -> 3 ms
      165 Hz -> 3 ms
    The timer is active only during short press/check animations.
    """
    rate = max(30.0, current_screen_refresh_rate(widget))
    target_hz = min(360.0, rate * ANIMATION_TIMER_OVERSAMPLE)
    interval = int(round(1000.0 / target_hz))
    return max(ANIMATION_TIMER_MIN_MS, min(ANIMATION_TIMER_MAX_MS, interval))


def enable_windows_high_resolution_timers():
    """Reduce Windows timer granularity so short QTimer animations are smooth.

    Without this, many Windows 10 systems wake timers at ~15.6 ms even when a
    PreciseTimer requests 6-8 ms, which is the main reason the animation can feel
    below 60 FPS.
    """
    if os.name != "nt" or not ENABLE_WINDOWS_HIGH_RES_TIMERS:
        return False
    try:
        result = ctypes.windll.winmm.timeBeginPeriod(1)
        return result == 0
    except Exception:
        return False


def disable_windows_high_resolution_timers():
    if os.name != "nt" or not ENABLE_WINDOWS_HIGH_RES_TIMERS:
        return
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


# ==========================
# Thumbnail helpers
# ==========================


def _text_bbox(draw: ImageDraw.ImageDraw, xy, text: str, **kwargs):
    try:
        return draw.textbbox(xy, text, **kwargs)
    except Exception:
        w, h = draw.textsize(text, **kwargs)
        return (xy[0], xy[1], xy[0] + w, xy[1] + h)




def _resample_lanczos():
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _flatten_rgba_on_white(img: Image.Image) -> Image.Image:
    """Convert RGBA to RGB over a white background.

    Direct RGBA -> RGB conversion turns transparent pixels black in Pillow,
    which was the cause of the thick black rounded borders in the grid.
    """
    if img.mode != "RGBA":
        return img.convert("RGB")
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white.alpha_composite(img)
    return white.convert("RGB")


def center_crop_cover(img: Image.Image, target_size: tuple[int, int]):
    """Resize so the shorter side fills target_size, then crop center."""
    tw, th = target_size
    w, h = img.size
    if w <= 0 or h <= 0:
        return Image.new("RGB", target_size, "#F5F5F7")
    scale = max(tw / w, th / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), _resample_lanczos())
    left = max(0, (nw - tw) // 2)
    top = max(0, (nh - th) // 2)
    return resized.crop((left, top, left + tw, top + th))


def resize_gpu_if_possible(img: Image.Image, target_size: tuple[int, int]):
    """Optional GPU resize. HEIC decoding still happens on CPU.

    This returns None when CUDA OpenCV is unavailable so the CPU path is used.
    """
    if not USE_GPU_RESIZE_AUTO:
        return None
    cv2_mod, np_mod = get_cv2_np(require_cuda=True)
    if not (CV2_CUDA_AVAILABLE and cv2_mod is not None and np_mod is not None):
        return None
    try:
        tw, th = target_size
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        scale = max(tw / w, th / h)
        nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        arr = np_mod.array(img.convert("RGB"))
        gpu = cv2_mod.cuda_GpuMat()
        gpu.upload(arr)
        resized_gpu = cv2_mod.cuda.resize(gpu, (nw, nh), interpolation=cv2_mod.INTER_AREA)
        resized = resized_gpu.download()
        preview = Image.fromarray(resized)
        left = max(0, (preview.width - tw) // 2)
        top = max(0, (preview.height - th) // 2)
        return preview.crop((left, top, left + tw, top + th))
    except Exception:
        return None

def add_live_badge(img: Image.Image):
    """PIL fallback badge, mainly used by table-view placeholders.
    The photo wall draws a sharper Qt vector badge in its delegate.
    """
    scale = 3
    base = img.convert("RGBA")
    hi = base.resize((base.width * scale, base.height * scale), _resample_lanczos())
    draw = ImageDraw.Draw(hi)
    badge_w = 54 * scale
    badge_h = 22 * scale
    x1 = hi.width - badge_w - 7 * scale
    y1 = hi.height - badge_h - 7 * scale
    x2 = x1 + badge_w
    y2 = y1 + badge_h
    draw.rounded_rectangle((x1, y1, x2, y2), radius=11 * scale, fill=(18, 18, 20, 190))
    # small live dot
    dot_r = 3 * scale
    dot_cx = x1 + 12 * scale
    dot_cy = y1 + badge_h // 2
    draw.ellipse((dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r), fill=(52, 199, 89, 255))
    draw.text((x1 + 20 * scale, y1 + 4 * scale), "LIVE", fill=(255, 255, 255, 255))
    hi = hi.resize(base.size, _resample_lanczos())
    return hi.convert("RGB")


def make_placeholder_pil(text="读取中", size=(ICON_SIZE, ICON_SIZE), is_live=False):
    """Flat centered placeholder matching loaded thumbnail bounds.

    No rounded card, no border: the placeholder occupies the exact same square
    as a loaded tile, so loaded/unloaded states do not visually change tile size.
    """
    scale = 3
    w, h = size
    hi = Image.new("RGBA", (w * scale, h * scale), (245, 245, 247, 255))
    draw = ImageDraw.Draw(hi)

    cx = w * scale / 2
    cy = h * scale / 2 - 8 * scale
    icon_w = 44 * scale
    icon_h = 34 * scale
    ix1 = cx - icon_w / 2
    iy1 = cy - icon_h / 2
    ix2 = cx + icon_w / 2
    iy2 = cy + icon_h / 2
    glyph = (142, 142, 147, 255)
    draw.rounded_rectangle((ix1, iy1, ix2, iy2), radius=6 * scale, outline=glyph, width=2 * scale)
    draw.ellipse((ix1 + 8 * scale, iy1 + 7 * scale, ix1 + 15 * scale, iy1 + 14 * scale), fill=glyph)
    draw.line((ix1 + 6 * scale, iy2 - 7 * scale, ix1 + 18 * scale, iy2 - 19 * scale, ix1 + 29 * scale, iy2 - 7 * scale), fill=glyph, width=2 * scale, joint="curve")
    draw.line((ix1 + 22 * scale, iy2 - 7 * scale, ix1 + 33 * scale, iy2 - 16 * scale, ix2 - 6 * scale, iy2 - 7 * scale), fill=glyph, width=2 * scale, joint="curve")

    tb = _text_bbox(draw, (0, 0), text)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    draw.text((cx - tw / 2, h * scale / 2 + 20 * scale - th / 2), text, fill=(120, 120, 128, 255))

    img_rgba = hi.resize(size, _resample_lanczos())
    img = _flatten_rgba_on_white(img_rgba)
    if is_live:
        img = add_live_badge(img)
    return img

def pil_to_png_bytes(img: Image.Image):
    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def make_cached_or_fresh_thumbnail_bytes(item: PhotoItemData, size=(ICON_SIZE, ICON_SIZE)):
    """Load a thumbnail from disk cache when the item signature still matches.

    Falls back to generating a fresh thumbnail and updates the PNG cache.  This
    runs in the existing thumbnail executor, so scanning UI stays responsive.
    """
    try:
        if item.stable_key and item.file_signature:
            path = thumb_cache_path_for_item(item)
            if path.exists():
                data = path.read_bytes()
                if data:
                    return data, ""
            # Older v30-pre cache path, only used when the list-info cache proves
            # the file signature is still the same.
            legacy = thumb_cache_path_for_key(item.stable_key)
            if legacy.exists():
                info = _json_load(_ensure_state_dir() / ITEM_INFO_CACHE_FILE_NAME, {})
                rec = (info.get("items", {}) if isinstance(info, dict) else {}).get(item.stable_key, {})
                if isinstance(rec, dict) and rec.get("signature") == item.file_signature:
                    data = legacy.read_bytes()
                    if data:
                        return data, ""
    except Exception:
        pass
    data, error = make_thumbnail_bytes_result(item.representative_image, item.is_live, size)
    try:
        if item.stable_key and not error:
            path = thumb_cache_path_for_item(item)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except Exception:
        pass
    return data, error


def make_thumbnail_bytes(image_path: Path, is_live: bool, size=(ICON_SIZE, ICON_SIZE)):
    return make_thumbnail_bytes_result(image_path, is_live, size)[0]


def make_thumbnail_bytes_result(image_path: Path, is_live: bool, size=(ICON_SIZE, ICON_SIZE)):
    try:
        if not image_path.exists():
            raise FileNotFoundError(str(image_path))
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            preview = resize_gpu_if_possible(img, size)
            if preview is None:
                preview = center_crop_cover(img, size)
            # Do not bake LIVE into the actual thumbnail. The grid delegate draws a
            # crisp vector LIVE badge on top, avoiding double badges and blurry scaling.
            return pil_to_png_bytes(preview), ""
    except Exception as exc:
        ext = image_path.suffix.upper().lstrip(".") or "IMG"
        return (
            pil_to_png_bytes(make_placeholder_pil(ext, size=size, is_live=False)),
            f"{type(exc).__name__}: {exc}",
        )



def find_live_video_file(item: PhotoItemData) -> Optional[Path]:
    for f in item.files:
        if f.suffix.lower() in VIDEO_EXTENSIONS:
            return f
    return None


def image_files_for_item(item: PhotoItemData) -> list[Path]:
    return [f for f in item.files if f.suffix.lower() in IMAGE_EXTENSIONS]


def item_is_mov_only(item: PhotoItemData | None) -> bool:
    return bool(item is not None and getattr(item, "item_kind", "photo") == "mov_only")


def ios_img_basename(seq: int) -> str:
    """Return the common iPhone-like IMG_#### basename for a 1-based sequence."""
    n = ((max(1, int(seq)) - 1) % 9999) + 1
    return f"IMG_{n:04d}"


def ios_dcf_folder_name(seq: int) -> str:
    """Return a DCF/iPhone-like DCIM subfolder such as 100APPLE, 101APPLE.

    A single DCF directory uses file numbers 0001..9999.  To avoid overwriting
    after IMG_9999, batch reorder export advances the directory number and starts
    the basename over at IMG_0001 in the next folder.
    """
    folder_no = 100 + ((max(1, int(seq)) - 1) // 9999)
    return f"{folder_no:03d}APPLE"


def ios_dcf_relative_dir(seq: int) -> Path:
    return Path("DCIM") / ios_dcf_folder_name(seq)


def path_is_under_folder(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return True
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except Exception:
        return False



def _frame_to_png_bytes(frame_rgb, size):
    try:
        img = Image.fromarray(frame_rgb).convert("RGB")
        img = center_crop_cover(img, size)
        return pil_to_png_bytes(img)
    except Exception:
        return None



def _find_ffmpeg_exe() -> Optional[str]:
    """Return a usable ffmpeg executable if available.

    imageio-ffmpeg is preferred because it bundles ffmpeg and is the most reliable
    way to decode iPhone HEVC Live Photo MOV files on Windows. System ffmpeg is
    used as a fallback.
    """
    candidates: list[Optional[str]] = []
    try:
        ffmpeg_mod = get_imageio_ffmpeg_module()
        if ffmpeg_mod is not None:
            candidates.append(ffmpeg_mod.get_ffmpeg_exe())
    except Exception:
        pass
    try:
        candidates.append(shutil.which("ffmpeg"))
    except Exception:
        pass
    for exe in candidates:
        if exe and Path(exe).exists():
            return str(exe)
    return None


def _decode_live_frames_with_ffmpeg(video_path: Path, size=(ICON_SIZE, ICON_SIZE), max_frames: int = LIVE_PREVIEW_FRAME_COUNT, timeout: int = LIVE_PREVIEW_DECODE_TIMEOUT):
    """Decode live-preview frames through ffmpeg rawvideo pipe.

    This is much more reliable for iPhone HEVC MOV than OpenCV's VideoCapture on
    Windows. It scales/crops in ffmpeg and returns fixed-size RGB frames.
    """
    exe = _find_ffmpeg_exe()
    if not exe:
        return []
    w, h = size
    # fps filter samples a short looping preview; scale+crop gives iOS-like cover.
    vf = (
        f"fps={LIVE_PREVIEW_FPS},"
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}"
    )
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-an",
        "-vf", vf,
        "-frames:v", str(max_frames),
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        if proc.returncode != 0 or not proc.stdout:
            return []
        frame_size = w * h * 3
        raw = proc.stdout
        frames: list[bytes] = []
        for off in range(0, len(raw) - frame_size + 1, frame_size):
            chunk = raw[off:off + frame_size]
            if len(chunk) != frame_size:
                break
            img = Image.frombytes("RGB", (w, h), chunk)
            frames.append(pil_to_png_bytes(img))
            if len(frames) >= max_frames:
                break
        return frames
    except Exception:
        return []


def _decode_live_frames_with_cv2(video_path: Path, size=(ICON_SIZE, ICON_SIZE), max_frames: int = LIVE_PREVIEW_FRAME_COUNT, timeout: int = LIVE_PREVIEW_DECODE_TIMEOUT):
    frames: list[bytes] = []
    cv2_mod, _np_mod = get_cv2_np(require_cuda=False)
    if cv2_mod is None:
        return frames
    # Random sampling first.
    cap = None
    try:
        cap = cv2_mod.VideoCapture(str(video_path))
        if cap.isOpened():
            frame_count = int(cap.get(cv2_mod.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count > 2:
                start_pos = max(0, int(frame_count * 0.05))
                end_pos = max(start_pos + 1, int(frame_count * 0.95))
                positions = [start_pos + int((end_pos - start_pos) * i / max(1, max_frames - 1)) for i in range(max_frames)]
                for pos in positions:
                    cap.set(cv2_mod.CAP_PROP_POS_FRAMES, pos)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    frame_rgb = cv2_mod.cvtColor(frame, cv2_mod.COLOR_BGR2RGB)
                    data = _frame_to_png_bytes(frame_rgb, size)
                    if data:
                        frames.append(data)
        if cap is not None:
            cap.release()
    except Exception:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass

    # Sequential fallback.
    if len(frames) < 4:
        frames = []
        cap = None
        try:
            cap = cv2_mod.VideoCapture(str(video_path))
            if cap.isOpened():
                frame_count = int(cap.get(cv2_mod.CAP_PROP_FRAME_COUNT) or 0)
                max_decode = min(frame_count if frame_count > 0 else 240, 240)
                step = max(1, max_decode // max_frames)
                seen = 0
                while seen < max_decode and len(frames) < max_frames:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    if seen % step == 0:
                        frame_rgb = cv2_mod.cvtColor(frame, cv2_mod.COLOR_BGR2RGB)
                        data = _frame_to_png_bytes(frame_rgb, size)
                        if data:
                            frames.append(data)
                    seen += 1
            if cap is not None:
                cap.release()
        except Exception:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
    return frames


def _decode_live_frames_with_imageio(video_path: Path, size=(ICON_SIZE, ICON_SIZE), max_frames: int = LIVE_PREVIEW_FRAME_COUNT, timeout: int = LIVE_PREVIEW_DECODE_TIMEOUT):
    iio_mod = get_imageio_v3()
    if iio_mod is None:
        return []
    frames: list[bytes] = []
    try:
        for i, frame in enumerate(iio_mod.imiter(str(video_path))):
            if i > 240:
                break
            if i % 6 != 0:
                continue
            data = _frame_to_png_bytes(frame, size)
            if data:
                frames.append(data)
            if len(frames) >= max_frames:
                break
    except Exception:
        frames = []
    return frames


def make_live_preview_frames_bytes(video_path: Path, size=(ICON_SIZE, ICON_SIZE), max_frames: int = LIVE_PREVIEW_FRAME_COUNT, timeout: int = LIVE_PREVIEW_DECODE_TIMEOUT):
    """Decode a looping preview from a Live Photo MOV.

    Priority is now ffmpeg pipe -> OpenCV -> imageio. The ffmpeg path fixes the
    common Windows case where iPhone HEVC MOV cannot be decoded by OpenCV, which
    made LIVE thumbnails never animate even though a MOV file was present.
    """
    if not video_path.exists():
        return []
    for decoder in (_decode_live_frames_with_ffmpeg, _decode_live_frames_with_cv2, _decode_live_frames_with_imageio):
        frames = decoder(video_path, size=size, max_frames=max_frames, timeout=timeout)
        if len(frames) >= 2:
            return frames
    return []


def _decode_live_qimages_with_ffmpeg(
    video_path: Path,
    size: tuple[int, int],
    max_frames: int,
    timeout: int,
    fps: int = 12,
):
    """Decode LIVE frames straight into QImage objects.

    This avoids the old slow path:
        ffmpeg raw frame -> PIL Image -> PNG encode -> QPixmap decode

    For the detail viewer, PNG encoding was a major reason why the LIVE preview
    felt much slower than iOS. QImage is created directly from raw RGB bytes and
    detached with copy(), so it is safe to pass back to the GUI thread.
    """
    exe = _find_ffmpeg_exe()
    if not exe or not video_path.exists():
        return []
    w, h = size
    vf = (
        f"fps={fps},"
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}"
    )
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-an",
        "-t", "2.6",
        "-vf", vf,
        "-frames:v", str(max_frames),
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        if proc.returncode != 0 or not proc.stdout:
            return []
        frame_size = w * h * 3
        raw = proc.stdout
        frames: list[QImage] = []
        for off in range(0, len(raw) - frame_size + 1, frame_size):
            chunk = raw[off:off + frame_size]
            if len(chunk) != frame_size:
                break
            qimg = QImage(chunk, w, h, w * 3, QImage.Format_RGB888).copy()
            if not qimg.isNull():
                frames.append(qimg)
            if len(frames) >= max_frames:
                break
        return frames
    except Exception:
        return []


def _bounded_live_size_for_target(target_size: tuple[int, int], max_long: int) -> tuple[int, int]:
    """Return an even-size rectangle with the same aspect ratio as the still photo.

    LIVE video frames are decoded/cropped to this rectangle.  Keeping the same
    aspect as the current still-photo scene prevents the iOS-like preview from
    changing the viewer geometry or resetting zoom/pan.
    """
    tw, th = target_size
    tw = max(2, int(tw))
    th = max(2, int(th))
    if tw >= th:
        w = max(2, int(max_long))
        h = max(2, int(round(w * th / tw)))
    else:
        h = max(2, int(max_long))
        w = max(2, int(round(h * tw / th)))
    # Even dimensions are friendlier to ffmpeg filters and GPU/video paths.
    w = max(2, w - (w % 2))
    h = max(2, h - (h % 2))
    return w, h


def make_detail_live_preview_qimages_fast(video_path: Path, target_size: tuple[int, int] | None = None):
    """Very fast first-stage LIVE preview for the detail viewer."""
    size = _bounded_live_size_for_target(target_size or (640, 640), DETAIL_LIVE_FAST_MAX_LONG)
    frames = _decode_live_qimages_with_ffmpeg(
        video_path,
        size=size,
        max_frames=DETAIL_LIVE_FAST_FRAME_COUNT,
        timeout=DETAIL_LIVE_FAST_TIMEOUT,
        fps=18,
    )
    if len(frames) >= 2:
        return frames

    byte_frames = make_live_preview_frames_bytes(
        video_path,
        size=size,
        max_frames=DETAIL_LIVE_FAST_FRAME_COUNT,
        timeout=DETAIL_LIVE_FAST_TIMEOUT,
    )
    frames = []
    for data in byte_frames:
        qimg = QImage()
        if qimg.loadFromData(data, "PNG") and not qimg.isNull():
            frames.append(qimg)
    return frames


def make_detail_live_preview_qimages_hq(video_path: Path, target_size: tuple[int, int] | None = None):
    """Second-stage higher-quality LIVE preview for the detail viewer."""
    size = _bounded_live_size_for_target(target_size or (1280, 1280), DETAIL_LIVE_HQ_MAX_LONG)
    frames = _decode_live_qimages_with_ffmpeg(
        video_path,
        size=size,
        max_frames=DETAIL_LIVE_HQ_FRAME_COUNT,
        timeout=DETAIL_LIVE_HQ_TIMEOUT,
        fps=24,
    )
    if len(frames) >= 2:
        return frames

    byte_frames = make_live_preview_frames_bytes(
        video_path,
        size=size,
        max_frames=DETAIL_LIVE_HQ_FRAME_COUNT,
        timeout=DETAIL_LIVE_HQ_TIMEOUT,
    )
    frames = []
    for data in byte_frames:
        qimg = QImage()
        if qimg.loadFromData(data, "PNG") and not qimg.isNull():
            frames.append(qimg)
    return frames



def make_detail_live_preview_frames_bytes(video_path: Path):
    """Legacy compatibility wrapper.

    The detail viewer now uses direct QImage functions above. This wrapper is kept
    only for older code paths and returns PNG bytes from the HQ setting.
    """
    return make_live_preview_frames_bytes(
        video_path,
        size=DETAIL_LIVE_HQ_SIZE,
        max_frames=DETAIL_LIVE_HQ_FRAME_COUNT,
        timeout=DETAIL_LIVE_HQ_TIMEOUT,
    )


def _pil_image_has_alpha(img: Image.Image) -> bool:
    """Return True when a PIL image carries real transparency information."""
    try:
        if img.mode in ("RGBA", "LA"):
            return True
        if img.mode == "P" and "transparency" in img.info:
            return True
    except Exception:
        pass
    return False


def _qcolor_to_rgb_tuple(color: QColor | str) -> tuple[int, int, int]:
    q = QColor(color)
    if not q.isValid():
        q = QColor("#DDE3EA")
    return int(q.red()), int(q.green()), int(q.blue())


def _composite_alpha_on_detail_bg(img: Image.Image) -> Image.Image:
    """Flatten transparent pixels onto the detail-view background color.

    Some Windows / Qt graphics paths still display transparent QPixmap pixels over
    a black backing store.  For the detail viewer the desired result is not real
    transparency, but transparency visually matching the preview background, so
    flattening here is more reliable than passing RGBA through Qt.
    """
    if not _pil_image_has_alpha(img):
        return img.convert("RGB")
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (*_qcolor_to_rgb_tuple(DETAIL_VIEW_BG), 255))
    bg.alpha_composite(rgba)
    return bg.convert("RGB")


def make_detail_image_bytes(image_path: Path, max_side: Optional[int] = None):
    """Load the original-resolution image for the detail viewer.

    Transparent pixels are composited onto the same light grey as the preview
    background, so PNG alpha no longer appears black.
    """
    try:
        with Image.open(image_path) as img:
            img = _composite_alpha_on_detail_bg(ImageOps.exif_transpose(img))
            if max_side is not None and max_side > 0:
                w, h = img.size
                if max(w, h) > max_side:
                    scale = max_side / max(w, h)
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample)
            return pil_to_png_bytes(img)
    except Exception:
        return b""


def make_detail_qimage(image_path: Path, max_side: Optional[int] = None) -> QImage:
    """Load detail image as QImage in a worker thread without PNG re-encoding."""
    try:
        with Image.open(image_path) as img:
            img = _composite_alpha_on_detail_bg(ImageOps.exif_transpose(img))
            if max_side is not None and max_side > 0:
                w, h = img.size
                if max(w, h) > max_side:
                    scale = max_side / max(w, h)
                    try:
                        resample = Image.Resampling.LANCZOS
                    except AttributeError:
                        resample = Image.LANCZOS
                    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample)
            w, h = img.size
            raw = img.tobytes("raw", "RGB")
            qimg = QImage(raw, w, h, w * 3, QImage.Format_RGB888)
            return qimg.copy()
    except Exception:
        return QImage()


def make_placeholder_icon(is_live=False):
    data = pil_to_png_bytes(make_placeholder_pil("读取中", (ICON_SIZE, ICON_SIZE), is_live=is_live))
    pix = QPixmap()
    if not pix.loadFromData(data, "PNG"):
        pix = QPixmap(ICON_SIZE, ICON_SIZE)
        pix.fill(QColor("#F5F5F7"))
    return pix


def tooltip_for_item(item: PhotoItemData):
    files_text = "\n".join(str(f) for f in item.files)
    return (
        f"文件名：{item.display_name}\n"
        f"类型：{item.item_type}\n"
        f"时间：{format_time(item.shot_time)}\n"
        f"时间来源：{item.time_source}\n"
        f"来源文件夹：{item.source_folder}\n"
        f"文件数：{len(item.files)}\n"
        f"容量：{format_bytes(item.size_bytes)}\n\n"
        f"包含文件：\n{files_text}"
    )


def _ratio_to_float(value):
    try:
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            return float(value.numerator) / float(value.denominator)
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1])
        return float(value)
    except Exception:
        return None


def _format_exif_value(value):
    if isinstance(value, bytes):
        try:
            return value.decode(errors="ignore")
        except Exception:
            return repr(value)
    if isinstance(value, tuple):
        vals = [_ratio_to_float(v) for v in value]
        if all(v is not None for v in vals):
            return ", ".join(f"{v:.4g}" for v in vals)
    num = _ratio_to_float(value)
    if num is not None and not isinstance(value, str):
        return f"{num:.4g}"
    return str(value)


def _gps_to_decimal(values, ref):
    try:
        d = _ratio_to_float(values[0])
        m = _ratio_to_float(values[1])
        s = _ratio_to_float(values[2])
        if d is None or m is None or s is None:
            return None
        result = d + m / 60.0 + s / 3600.0
        if str(ref).upper() in ("S", "W"):
            result = -result
        return result
    except Exception:
        return None


def metadata_text_for_item(item: PhotoItemData) -> str:
    image_path = item.representative_image
    lines = [
        f"文件名：{item.display_name}",
        f"类型：{item.item_type}",
        f"时间：{format_time(item.shot_time)}",
        f"时间来源：{item.time_source}",
        f"容量：{format_bytes(item.size_bytes)}",
        f"来源文件夹：{item.source_folder}",
        "",
    ]
    try:
        with Image.open(image_path) as img:
            lines.append(f"图像尺寸：{img.width} × {img.height}")
            exif = img.getexif()
            if not exif:
                lines.append("未读取到 EXIF 元数据。")
            else:
                named = {TAGS.get(k, k): v for k, v in exif.items()}
                aperture = named.get("FNumber") or named.get("ApertureValue")
                focal = named.get("FocalLength")
                exposure = named.get("ExposureTime")
                iso = named.get("ISOSpeedRatings") or named.get("PhotographicSensitivity")
                camera_make = named.get("Make")
                camera_model = named.get("Model")
                lens = named.get("LensModel") or named.get("LensMake")
                dt_original = named.get("DateTimeOriginal") or named.get("DateTime")

                lines.append("常用信息：")
                if camera_make or camera_model:
                    lines.append(f"  设备：{_format_exif_value(camera_make or '')} {_format_exif_value(camera_model or '')}".rstrip())
                if lens:
                    lines.append(f"  镜头：{_format_exif_value(lens)}")
                if dt_original:
                    lines.append(f"  原始拍摄时间：{_format_exif_value(dt_original)}")
                if aperture:
                    ap = _ratio_to_float(aperture)
                    lines.append(f"  光圈：f/{ap:.1f}" if ap else f"  光圈：{_format_exif_value(aperture)}")
                if focal:
                    fl = _ratio_to_float(focal)
                    lines.append(f"  焦距：{fl:.1f} mm" if fl else f"  焦距：{_format_exif_value(focal)}")
                if exposure:
                    exp = _ratio_to_float(exposure)
                    if exp and exp < 1:
                        lines.append(f"  快门：1/{round(1/exp)} s")
                    elif exp:
                        lines.append(f"  快门：{exp:.3g} s")
                    else:
                        lines.append(f"  快门：{_format_exif_value(exposure)}")
                if iso:
                    lines.append(f"  ISO：{_format_exif_value(iso)}")

                gps_raw = named.get("GPSInfo")
                if gps_raw:
                    gps = {}
                    try:
                        for gk, gv in gps_raw.items():
                            gps[GPSTAGS.get(gk, gk)] = gv
                    except Exception:
                        gps = gps_raw if isinstance(gps_raw, dict) else {}
                    lat = _gps_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")) if gps else None
                    lon = _gps_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef")) if gps else None
                    if lat is not None and lon is not None:
                        lines.append(f"  地点坐标：{lat:.6f}, {lon:.6f}")
                    elif gps:
                        lines.append("  GPS：存在，但未能转换为经纬度。")

                lines.append("")
                lines.append("全部 EXIF：")
                for key in sorted(named.keys(), key=lambda x: str(x)):
                    if key == "GPSInfo":
                        continue
                    lines.append(f"  {key}: {_format_exif_value(named[key])}")
    except Exception as e:
        lines.append(f"读取元数据失败：{e}")

    lines.append("")
    lines.append("包含文件：")
    lines.extend(f"  {f}" for f in item.files)

    return "\n".join(lines)


def _h(value) -> str:
    return html.escape("" if value is None else str(value))


def _kv_row(label, value, accent=False) -> str:
    val = _h(value)
    if not val:
        val = '<span class="muted">未读取到</span>'
    cls = "kv accent" if accent else "kv"
    return f'<tr class="{cls}"><th>{_h(label)}</th><td>{val}</td></tr>'


def _exif_named_dict(image_path: Path):
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            named = {TAGS.get(k, k): v for k, v in exif.items()} if exif else {}
            return img.width, img.height, named, None
    except Exception as e:
        return None, None, {}, e


def extract_classification_metadata_for_item(item: PhotoItemData) -> dict:
    """一次读取分类所需的拍摄时间、设备、GPS 和像素尺寸。"""
    result = {
        "shot_time": extract_best_exif_time_for_item(item),
        "camera_make": "",
        "camera_model": "",
        "gps_latitude": None,
        "gps_longitude": None,
        "image_width": 0,
        "image_height": 0,
        "metadata_found": False,
    }
    image_files = [
        path for path in item.files if path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    image_files.sort(
        key=lambda path: (
            IMAGE_PRIORITY.index(path.suffix.lower())
            if path.suffix.lower() in IMAGE_PRIORITY
            else 999
        )
    )
    for image_path in image_files:
        width, height, named, error = _exif_named_dict(image_path)
        if error is not None:
            continue
        result["image_width"] = int(width or 0)
        result["image_height"] = int(height or 0)
        result["metadata_found"] = bool(named)
        result["camera_make"] = _format_exif_value(
            named.get("Make", "")
        ).strip()
        result["camera_model"] = _format_exif_value(
            named.get("Model", "")
        ).strip()
        gps_raw = named.get("GPSInfo")
        if gps_raw:
            gps = {}
            try:
                gps = {
                    GPSTAGS.get(key, key): value
                    for key, value in gps_raw.items()
                }
            except Exception:
                if isinstance(gps_raw, dict):
                    gps = gps_raw
            if gps:
                result["gps_latitude"] = _gps_to_decimal(
                    gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")
                )
                result["gps_longitude"] = _gps_to_decimal(
                    gps.get("GPSLongitude"), gps.get("GPSLongitudeRef")
                )
        break
    return result


def metadata_html_for_item(item: PhotoItemData) -> str:
    """Beautiful, directly visible metadata document for the right-click dialog."""
    width, height, named, err = _exif_named_dict(item.representative_image)

    aperture = named.get("FNumber") or named.get("ApertureValue")
    focal = named.get("FocalLength")
    exposure = named.get("ExposureTime")
    iso = named.get("ISOSpeedRatings") or named.get("PhotographicSensitivity")
    camera_make = named.get("Make")
    camera_model = named.get("Model")
    lens = named.get("LensModel") or named.get("LensMake")
    dt_original = named.get("DateTimeOriginal") or named.get("DateTime")
    software = named.get("Software")

    ap_text = ""
    if aperture:
        ap = _ratio_to_float(aperture)
        ap_text = f"f/{ap:.1f}" if ap else _format_exif_value(aperture)

    focal_text = ""
    if focal:
        fl = _ratio_to_float(focal)
        focal_text = f"{fl:.1f} mm" if fl else _format_exif_value(focal)

    exposure_text = ""
    if exposure:
        exp = _ratio_to_float(exposure)
        if exp and exp < 1:
            exposure_text = f"1/{round(1/exp)} s"
        elif exp:
            exposure_text = f"{exp:.3g} s"
        else:
            exposure_text = _format_exif_value(exposure)

    gps_text = ""
    gps_raw = named.get("GPSInfo")
    if gps_raw:
        gps = {}
        try:
            for gk, gv in gps_raw.items():
                gps[GPSTAGS.get(gk, gk)] = gv
        except Exception:
            gps = gps_raw if isinstance(gps_raw, dict) else {}
        lat = _gps_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef")) if gps else None
        lon = _gps_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef")) if gps else None
        if lat is not None and lon is not None:
            gps_text = f"{lat:.6f}, {lon:.6f}"
        elif gps:
            gps_text = "存在 GPS 字段，但未能转换为经纬度"

    camera_text = " ".join(x for x in [_format_exif_value(camera_make) if camera_make else "", _format_exif_value(camera_model) if camera_model else ""] if x).strip()
    dimension_text = f"{width} × {height}" if width and height else ""

    file_cards = []
    for f in item.files:
        file_cards.append(
            f'''<div class="file-card">
                    <div class="file-name">{_h(f.name)}</div>
                    <div class="file-path">{_h(f)}</div>
                    <div class="file-size">{_h(format_bytes(safe_file_size(f)))}</div>
                </div>'''
        )

    exif_rows = []
    for key in sorted(named.keys(), key=lambda x: str(x)):
        if key == "GPSInfo":
            continue
        exif_rows.append(_kv_row(key, _format_exif_value(named[key])))
    if not exif_rows:
        exif_rows.append('<tr><td colspan="2" class="empty">未读取到 EXIF 元数据。</td></tr>')

    error_block = ""
    if err is not None:
        error_block = f'<div class="warn">读取元数据失败：{_h(err)}</div>'

    html_doc = f'''
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    margin: 0;
    padding: 0;
    background: #F5F5F7;
    color: #1D1D1F;
    font-family: "MiSans", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 14px;
    line-height: 1.52;
}}
.page {{ padding: 22px 24px 28px 24px; }}
.hero {{
    background: #FFFFFF;
    border: 1px solid #E6E8ED;
    border-radius: 18px;
    padding: 20px 22px;
    margin-bottom: 16px;
}}
.title {{ font-size: 22px; font-weight: 800; letter-spacing: -0.2px; color: #111113; margin-bottom: 8px; }}
.subtitle {{ color: #6B7280; font-size: 13px; }}
.badges {{ margin-top: 14px; }}
.badge {{
    display: inline-block;
    padding: 5px 10px;
    margin: 0 8px 8px 0;
    border-radius: 999px;
    background: #E9EEF7;
    color: #334155;
    font-weight: 650;
    font-size: 12px;
}}
.badge.live {{ background: #E7F8EE; color: #12834C; }}
.section {{
    background: #FFFFFF;
    border: 1px solid #E6E8ED;
    border-radius: 16px;
    margin: 14px 0;
    overflow: hidden;
}}
.section-title {{
    padding: 13px 16px;
    background: #FAFAFC;
    border-bottom: 1px solid #ECEEF2;
    font-size: 15px;
    font-weight: 800;
    color: #202124;
}}
table {{ width: 100%; border-collapse: collapse; }}
th {{
    width: 156px;
    text-align: left;
    vertical-align: top;
    padding: 10px 14px;
    color: #6B7280;
    font-weight: 650;
    border-bottom: 1px solid #F0F1F4;
}}
td {{
    padding: 10px 14px;
    color: #202124;
    border-bottom: 1px solid #F0F1F4;
}}
tr:last-child th, tr:last-child td {{ border-bottom: none; }}
.kv.accent td {{ font-weight: 750; color: #111827; }}
.muted {{ color: #A0A6B0; }}
.empty {{ color: #A0A6B0; text-align: center; padding: 22px; }}
.warn {{
    margin: 14px 0;
    padding: 12px 14px;
    border-radius: 12px;
    background: #FFF4E5;
    color: #8A4B00;
    border: 1px solid #FFE0AD;
}}
.file-card {{
    padding: 12px 14px;
    margin: 10px 14px;
    background: #F7F8FA;
    border: 1px solid #EAECF0;
    border-radius: 12px;
}}
.file-name {{ font-weight: 800; color: #111827; margin-bottom: 4px; }}
.file-path {{ color: #6B7280; font-size: 12px; word-break: break-all; }}
.file-size {{ color: #374151; font-size: 12px; margin-top: 5px; font-weight: 650; }}
</style>
</head>
<body>
<div class="page">
  <div class="hero">
    <div class="title">{_h(item.display_name)}</div>
    <div class="subtitle">{_h(item.source_folder)}</div>
    <div class="badges">
      <span class="badge {'live' if item.is_live else ''}">{'LIVE 实况照片' if item.is_live else '普通照片'}</span>
      <span class="badge">{_h(item.item_type)}</span>
      <span class="badge">{_h(format_bytes(item.size_bytes))}</span>
      <span class="badge">{_h(str(len(item.files)))} 个文件</span>
    </div>
  </div>

  {error_block}

  <div class="section">
    <div class="section-title">核心信息</div>
    <table>
      {_kv_row('文件名', item.display_name, True)}
      {_kv_row('类型', item.item_type)}
      {_kv_row('当前排序时间', format_time(item.shot_time), True)}
      {_kv_row('时间来源', item.time_source)}
      {_kv_row('图像尺寸', dimension_text, True)}
      {_kv_row('容量', format_bytes(item.size_bytes), True)}
      {_kv_row('来源文件夹', item.source_folder)}
    </table>
  </div>

  <div class="section">
    <div class="section-title">拍摄参数</div>
    <table>
      {_kv_row('设备', camera_text, True)}
      {_kv_row('镜头', _format_exif_value(lens) if lens else '')}
      {_kv_row('原始拍摄时间', _format_exif_value(dt_original) if dt_original else '', True)}
      {_kv_row('光圈', ap_text)}
      {_kv_row('焦距', focal_text)}
      {_kv_row('快门', exposure_text)}
      {_kv_row('ISO', _format_exif_value(iso) if iso else '')}
      {_kv_row('软件', _format_exif_value(software) if software else '')}
      {_kv_row('地点坐标', gps_text)}
    </table>
  </div>

  <div class="section">
    <div class="section-title">包含文件</div>
    {''.join(file_cards)}
  </div>

  <div class="section">
    <div class="section-title">完整 EXIF</div>
    <table>{''.join(exif_rows)}</table>
  </div>
</div>
</body>
</html>
'''
    return html_doc


# ==========================
# Signals
# ==========================


class WorkerSignals(QObject):
    scan_found = Signal(int)
    scan_items_ready = Signal(int, object)
    scan_error = Signal(int, str)
    scan_cancelled = Signal(int)
    thumb_done = Signal(int, str, bytes)
    thumb_failed = Signal(int, str, str)
    priority_thumb_done = Signal(int, str, bytes)
    meta_done = Signal(int, str, object)
    live_frames_ready = Signal(int, str, object)
    file_op_progress = Signal(int, int, str)
    file_op_done = Signal(object)


class DetailLoadSignals(QObject):
    detail_ready = Signal(int, str, object)
    detail_live_ready = Signal(int, str, object)


# ==========================
# Virtual models
# ==========================


class PhotoGridModel(QAbstractListModel):
    def __init__(self, window: "PhotoMoverQt"):
        super().__init__(window)
        self.window = window
        self.visible_ids: list[str] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.visible_ids)

    def set_visible_ids(self, ids: list[str]):
        self.beginResetModel()
        self.visible_ids = list(ids)
        self.endResetModel()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self.visible_ids):
            return None
        item_id = self.visible_ids[row]
        item = self.window.item_map.get(item_id)
        if item is None:
            return None
        if role == Qt.DecorationRole:
            return self.window.icon_for_item(item)
        if role == Qt.DisplayRole:
            return ""
        if role == Qt.ToolTipRole:
            return tooltip_for_item(item)
        if role == ITEM_ID_ROLE:
            return item_id
        if role == IS_LIVE_ROLE:
            return item.is_live
        if role == NEEDS_BINDING_ROLE:
            return bool(getattr(item, "needs_binding", False) or getattr(item, "item_kind", "photo") == "mov_only")
        if role == THUMB_READY_ROLE:
            return (item_id in self.window.icon_cache) or (item_id in self.window.live_frame_cache)
        return None

    def row_for_id(self, item_id: str) -> int:
        return self.window.visible_row_by_id.get(item_id, -1)

    def notify_rows(self, rows: list[int], roles=None):
        if not rows:
            return
        max_row = len(self.visible_ids) - 1
        roles = roles or [Qt.DecorationRole, Qt.ToolTipRole]
        for a, b in compact_ranges([r for r in rows if 0 <= r <= max_row]):
            self.dataChanged.emit(self.index(a, 0), self.index(b, 0), roles)


class PhotoTableModel(QAbstractTableModel):
    HEADERS = ["文件", "时间", "类型", "文件数", "时间来源", "来源文件夹"]

    def __init__(self, window: "PhotoMoverQt"):
        super().__init__(window)
        self.window = window
        self.visible_ids: list[str] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.visible_ids)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return 6

    def set_visible_ids(self, ids: list[str]):
        self.beginResetModel()
        self.visible_ids = list(ids)
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self.visible_ids):
            return None
        item_id = self.visible_ids[row]
        item = self.window.item_map.get(item_id)
        if item is None:
            return None
        if role == ITEM_ID_ROLE:
            return item_id
        if role == IS_LIVE_ROLE:
            return item.is_live
        if role == NEEDS_BINDING_ROLE:
            return bool(getattr(item, "needs_binding", False) or getattr(item, "item_kind", "photo") == "mov_only")
        if role == THUMB_READY_ROLE:
            return (item_id in self.window.icon_cache) or (item_id in self.window.live_frame_cache)
        if role == Qt.ToolTipRole:
            return tooltip_for_item(item)
        if role == Qt.DecorationRole and col == 0:
            return self.window.icon_for_item(item)
        if role == Qt.DisplayRole:
            if col == 0:
                return item.display_name
            if col == 1:
                return format_time(item.shot_time)
            if col == 2:
                return item.item_type
            if col == 3:
                return str(len(item.files))
            if col == 4:
                return item.time_source
            if col == 5:
                return str(item.source_folder)
        return None

    def notify_rows(self, rows: list[int], roles=None):
        if not rows:
            return
        max_row = len(self.visible_ids) - 1
        roles = roles or [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole]
        for a, b in compact_ranges([r for r in rows if 0 <= r <= max_row]):
            self.dataChanged.emit(self.index(a, 0), self.index(b, self.columnCount() - 1), roles)


def compact_ranges(rows: list[int]):
    if not rows:
        return []
    rows = sorted(set(rows))
    ranges = []
    start = prev = rows[0]
    for row in rows[1:]:
        if row == prev + 1:
            prev = row
        else:
            ranges.append((start, prev))
            start = prev = row
    ranges.append((start, prev))
    return ranges


# ==========================
# Font rendering helpers
# ==========================


def choose_modern_font(point_size: int = 10, *, bold: bool = False) -> QFont:
    """Return the active theme's locale-aware UI font.

    The historical themes intentionally use full hinting and period-appropriate
    CJK families; the native Apple-like theme keeps smooth no-hinting rendering.
    """
    return make_theme_font(
        globals().get("RUNTIME_THEME_STYLE", "light"),
        globals().get("RUNTIME_THEME_LOCALE", "zh_CN"),
        point_size,
        bold=bold,
    )


def apply_smooth_font(widget, point_size: int = 10, *, bold: bool = False):
    try:
        widget.setFont(choose_modern_font(point_size, bold=bold))
    except Exception:
        pass


def paint_empty_library_state(
    painter: QPainter,
    rect: QRectF,
    *,
    has_source: bool,
    table_mode: bool = False,
    translations=None,
):
    """Paint a calm Photos-style empty state directly in a virtual view."""
    if rect.width() < 260 or rect.height() < 190:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    center_x = rect.center().x()
    show_modern_icon = getattr(globals().get("RUNTIME_THEME_PROFILE"), "uses_modern_icons", True)
    center_y = rect.center().y() - (18 if show_modern_icon else 44)
    if show_modern_icon:
        halo_rect = QRectF(center_x - 42, center_y - 62, 84, 84)
        halo_path = l2_superellipse_path(halo_rect, radius=29, samples=64)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(SYSTEM_GRAY_1))
        painter.drawPath(halo_path)
        icon = render_ui_icon_pixmap("photo-stack" if not table_mode else "photo", SYSTEM_GRAY_6, 40)
        painter.drawPixmap(
            QRectF(center_x - 20, center_y - 40, 40, 40),
            icon,
            QRectF(0, 0, icon.width(), icon.height()),
        )

    title = "这里还没有照片" if has_source else "开始整理你的照片资料库"
    subtitle = "当前分类没有符合条件的项目" if has_source else "选择一个照片文件夹，照片会在这里安全地显示"
    if translations is not None:
        title = translations.text(title)
        subtitle = translations.text(subtitle)
    painter.setFont(choose_modern_font(13, bold=True))
    painter.setPen(QColor(APP_TEXT))
    painter.drawText(
        QRectF(rect.left() + 30, center_y + 34, rect.width() - 60, 28),
        Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextSingleLine,
        title,
    )
    painter.setFont(choose_modern_font(10))
    painter.setPen(QColor(SYSTEM_GRAY_6))
    painter.drawText(
        QRectF(rect.left() + 30, center_y + 64, rect.width() - 60, 24),
        Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextSingleLine,
        subtitle,
    )
    painter.restore()


class EmptyLibraryStateOverlay(QWidget):
    """Single paint owner for a view's empty state.

    Drawing the empty state from QAbstractItemView.paintEvent caused partial
    viewport updates (notably horizontal-scrollbar changes in table mode) to
    stamp a second copy of the text over pixels from the previous full repaint.
    This opaque child owns the whole empty surface and therefore clears before
    every draw.
    """

    def __init__(self, view: QAbstractItemView, *, table_mode: bool):
        super().__init__(view.viewport())
        self.view = view
        self.table_mode = bool(table_mode)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(CONTENT_BG))
        model = self.view.model()
        owner = getattr(model, "window", None) if model is not None else None
        paint_empty_library_state(
            painter,
            QRectF(self.rect()),
            has_source=bool(getattr(owner, "source_dir", None)),
            table_mode=self.table_mode,
            translations=getattr(owner, "translation_service", None),
        )
        painter.end()


# ==========================
# Grid view with drag selection
# ==========================


class PhotoGridDelegate(QStyledItemDelegate):
    def __init__(self, icon_size: int, grid_size: int, parent=None):
        super().__init__(parent)
        self.icon_size = icon_size
        self.grid_size = grid_size
        self.placeholder_font = choose_modern_font(9)
        self.badge_font = choose_modern_font(8, bold=True)

    def _image_rect(self, option):
        rect = option.rect
        side = self.icon_size
        x = rect.x() + max(0, (rect.width() - side) // 2)
        y = rect.y() + max(0, (rect.height() - side) // 2)
        return QRectF(x, y, side, side)

    def _apply_press_transform(self, painter: QPainter, option, row: int):
        """Scale the entire tile content around the mouse-down anchor.

        This is intentionally drawn in the delegate so thumbnail, LIVE badge,
        selection frame and check mark move together as one physical tile.
        """
        view = self.parent()
        scale = 1.0
        anchor = None
        try:
            scale = float(view._press_scale(row))
            anchor = view._press_anchor(row)
        except Exception:
            return False
        if anchor is None or abs(scale - 1.0) < 0.002:
            return False
        painter.setClipRect(option.rect)
        painter.translate(float(anchor.x()), float(anchor.y()))
        painter.scale(scale, scale)
        painter.translate(-float(anchor.x()), -float(anchor.y()))
        return True

    def _draw_placeholder(self, painter: QPainter, image_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Flat background only. No rounded rectangle and no outline, so placeholder
        # and loaded thumbnails have exactly the same visual tile bounds.
        profile = globals().get("RUNTIME_THEME_PROFILE")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(APP_PANEL_2))
        if not profile.uses_modern_icons:
            painter.drawRect(image_rect)
            painter.setPen(QPen(QColor(APP_BORDER), 1))
            painter.drawRect(image_rect.adjusted(0.5, 0.5, -0.5, -0.5))
            painter.restore()
            return
        painter.drawRoundedRect(image_rect, 4 if profile.corner_style == "continuous" else profile.control_radius, 4 if profile.corner_style == "continuous" else profile.control_radius)

        cx = image_rect.center().x()
        cy = image_rect.center().y() - 8
        glyph_w = 44
        glyph_h = 34
        g = QRectF(cx - glyph_w / 2, cy - glyph_h / 2, glyph_w, glyph_h)
        glyph_pen = QPen(QColor(SYSTEM_GRAY_6), 2.0)
        glyph_pen.setCapStyle(Qt.RoundCap)
        glyph_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(glyph_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(g, 6, 6)
        painter.setBrush(QColor(SYSTEM_GRAY_6))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(g.left() + 8, g.top() + 7, 7, 7))
        painter.setPen(glyph_pen)
        mountain = QPainterPath()
        mountain.moveTo(g.left() + 6, g.bottom() - 7)
        mountain.lineTo(g.left() + 18, g.bottom() - 19)
        mountain.lineTo(g.left() + 29, g.bottom() - 7)
        mountain.moveTo(g.left() + 23, g.bottom() - 7)
        mountain.lineTo(g.left() + 34, g.bottom() - 16)
        mountain.lineTo(g.right() - 6, g.bottom() - 7)
        painter.drawPath(mountain)

        painter.setFont(self.placeholder_font)
        painter.setPen(QColor("#7A7A82"))
        painter.drawText(QRectF(image_rect.left(), image_rect.center().y() + 20, image_rect.width(), 20), Qt.AlignCenter, "加载中")
        painter.restore()

    def _draw_live_badge(self, painter: QPainter, image_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        badge_w = 57
        badge_h = 22
        bx = image_rect.left() + 8
        by = image_rect.top() + 8
        badge = QRectF(bx, by, badge_w, badge_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(18, 18, 20, 188))
        painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)

        dot_d = 6
        dot_x = bx + 9
        dot_y = by + (badge_h - dot_d) / 2
        painter.setBrush(QColor("#34C759"))
        painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))

        painter.setFont(self.badge_font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(bx + 18, by, badge_w - 21, badge_h), Qt.AlignVCenter | Qt.AlignLeft, "LIVE")
        painter.restore()

    def _draw_unbound_mov_badge(self, painter: QPainter, image_rect: QRectF):
        """Badge for standalone/ambiguous Live Photo MOV items that need manual binding."""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        badge_w = 78
        badge_h = 22
        bx = image_rect.left() + 8
        by = image_rect.top() + 8
        badge = QRectF(bx, by, badge_w, badge_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(82, 70, 42, 206))
        painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)

        dot_d = 6
        dot_x = bx + 9
        dot_y = by + (badge_h - dot_d) / 2
        painter.setBrush(QColor("#FFCC00"))
        painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))

        painter.setFont(self.badge_font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(bx + 18, by, badge_w - 21, badge_h), Qt.AlignVCenter | Qt.AlignLeft, "待绑定")
        painter.restore()

    def _draw_selection(self, painter: QPainter, image_rect: QRectF, progress: float = 1.0):
        """Selected-state drawing for photo wall.

        Draw a Photos-like blue frame and check badge.
        """
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        progress = clamp01(progress)

        shade = ease_out_cubic(progress)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(20 + 18 * shade)))
        painter.drawRoundedRect(image_rect.adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)

        frame_pen = QPen(QColor(0, 122, 255), 3.0)
        frame_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(frame_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(image_rect.adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)

        check_raw = clamp01((progress - 0.06) / 0.94)
        check_p = ease_out_back(check_raw, 1.65)
        r = 13.5
        cx = image_rect.right() - 18
        cy = image_rect.bottom() - 18
        scale = max(0.05, min(1.24, check_p))
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.setOpacity(max(0.0, min(1.0, 0.08 + 0.92 * check_raw)))
        painter.setPen(QPen(QColor(255, 255, 255, 245), 3.0))
        painter.setBrush(QColor(0, 122, 255))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        check = QPainterPath()
        check.moveTo(-6.5, -1)
        check.lineTo(-1.2, 5.4)
        check.lineTo(8.8, -7.2)
        check_pen = QPen(QColor("white"), 3.4)
        check_pen.setCapStyle(Qt.RoundCap)
        check_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(check_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(check)
        painter.restore()

    def _draw_hover(self, painter: QPainter, image_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 22))
        painter.drawRoundedRect(image_rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.restore()

    def _draw_press_preview(self, painter: QPainter, image_rect: QRectF, progress: float = 1.0):
        """Immediate press hint without a blue selection frame.

        The actual physical feedback is the press-scale transform.  This only
        adds a very subtle darkening so the tile does not feel like it has been
        officially selected before the click/drag is resolved.
        """
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        progress = clamp01(progress)
        visual = ease_out_cubic(progress)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(18 + 22 * visual)))
        painter.fillRect(image_rect.adjusted(0.5, 0.5, -0.5, -0.5), painter.brush())
        painter.restore()

    def _draw_deselect(self, painter: QPainter, image_rect: QRectF, progress: float = 1.0):
        """Deselection transition for photo wall.

        The iOS-like darkening fades out and the check badge shrinks/fades.
        No blue outline is drawn during deselection.
        """
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        progress = clamp01(progress)
        fade = 1.0 - ease_out_quint(progress)
        if fade <= 0.001:
            painter.restore()
            return

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(70 * fade)))
        painter.fillRect(image_rect.adjusted(0.5, 0.5, -0.5, -0.5), painter.brush())

        # Check badge leaves with a small nonlinear shrink.
        r = 13.5
        cx = image_rect.right() - 18
        cy = image_rect.bottom() - 18
        scale = max(0.05, 1.0 - 0.72 * ease_in_out_cubic(progress))
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.setOpacity(max(0.0, fade))
        painter.setPen(QPen(QColor(255, 255, 255, 245), 3.0))
        painter.setBrush(QColor(0, 122, 255))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        check = QPainterPath()
        check.moveTo(-6.5, -1)
        check.lineTo(-1.2, 5.4)
        check.lineTo(8.8, -7.2)
        check_pen = QPen(QColor("white"), 3.4)
        check_pen.setCapStyle(Qt.RoundCap)
        check_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(check_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(check)
        painter.restore()

    def paint(self, painter: QPainter, option, index):
        rect = option.rect
        image_rect = self._image_rect(option)
        selected = bool(option.state & QStyle.State_Selected)
        is_live = bool(index.data(IS_LIVE_ROLE))
        needs_binding = bool(index.data(NEEDS_BINDING_ROLE))
        thumb_ready = bool(index.data(THUMB_READY_ROLE))
        item_id = str(index.data(ITEM_ID_ROLE) or "")
        view = self.parent()
        provisional = bool(getattr(view, "_provisional_row", None) == index.row())
        hovered = bool(item_id and getattr(view, "_hover_item_id", "") == item_id)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(rect, QColor(CONTENT_BG))
        self._apply_press_transform(painter, option, index.row())

        if thumb_ready:
            pix = index.data(Qt.DecorationRole)
            if isinstance(pix, QPixmap) and not pix.isNull():
                # Clip strictly to the image rectangle. This removes any accidental
                # native/QPixmap overpaint and prevents visible black fringes.
                painter.save()
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                clip = QPainterPath()
                clip.addRoundedRect(image_rect, 4, 4)
                painter.setClipPath(clip)
                painter.drawPixmap(image_rect.toRect(), pix)
                painter.restore()
            else:
                self._draw_placeholder(painter, image_rect)
        else:
            self._draw_placeholder(painter, image_rect)

        if needs_binding:
            self._draw_unbound_mov_badge(painter, image_rect)
        elif is_live:
            self._draw_live_badge(painter, image_rect)
        if provisional and not selected:
            prog = 1.0
            try:
                prog = float(view._provisional_progress(index.row()))
            except Exception:
                pass
            self._draw_press_preview(painter, image_rect, prog)
        elif hovered and not selected:
            self._draw_hover(painter, image_rect)
        if selected:
            prog = 1.0
            try:
                prog = float(view._check_progress(index.row()))
            except Exception:
                pass
            self._draw_selection(painter, image_rect, prog)
        elif hasattr(view, "_deselect_anim_start") and index.row() in getattr(view, "_deselect_anim_start", {}):
            prog = 1.0
            try:
                prog = float(view._deselect_progress(index.row()))
            except Exception:
                pass
            self._draw_deselect(painter, image_rect, prog)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self.grid_size, self.grid_size)



class FollowTooltip(QLabel):
    """Cursor-following tooltip with real transparent continuous corners.

    Stylesheet border-radius on Qt.ToolTip windows can leave a square native
    backing surface on Windows.  This tooltip paints its own L2 panel and applies
    a matching window mask, so no rectangular corner is visible outside the
    rounded shape.
    """

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.BypassWindowManagerHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # Do not apply a 1-bit QRegion mask here: it creates jagged/毛刺 edges on
        # high-DPI Windows.  Let DWM composite the antialiased alpha painted below.
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setMaximumWidth(680)
        self.setContentsMargins(14, 10, 14, 10)
        self._last_text = None
        self._tooltip_fill = QColor(222, 230, 239, 248)
        self._tooltip_border = QColor(168, 181, 198, 230)
        self.setStyleSheet("QLabel { background: transparent; border: none; padding: 0px; color: #111111; font-size: 10pt; }")

    def _panel_path(self):
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        return l2_superellipse_path(rect, radius=14, samples=28)

    def _update_tooltip_mask(self):
        # Intentionally no mask.  QRegion masks are not antialiased and can leave
        # visible teeth around the L2 corner.
        try:
            self.clearMask()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_tooltip_mask()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        except Exception:
            pass
        path = self._panel_path()
        painter.fillPath(path, self._tooltip_fill)
        painter.setPen(QPen(self._tooltip_border, 1.0))
        painter.drawPath(path)
        super().paintEvent(event)

    def show_text(self, text: str, global_pos: QPoint):
        if not text:
            self.hide()
            return
        if text != self._last_text:
            self._last_text = text
            self.setText(text)
            self.adjustSize()
            self._update_tooltip_mask()
        self.move_near(global_pos)
        if not self.isVisible():
            self.show()
        self.raise_()

    def move_near(self, global_pos: QPoint):
        offset = QPoint(18, 18)
        x = global_pos.x() + offset.x()
        y = global_pos.y() + offset.y()
        try:
            screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                w = max(self.width(), self.sizeHint().width())
                h = max(self.height(), self.sizeHint().height())
                if x + w > geo.right():
                    x = global_pos.x() - w - offset.x()
                if y + h > geo.bottom():
                    y = global_pos.y() - h - offset.y()
                x = max(geo.left(), min(x, geo.right() - w))
                y = max(geo.top(), min(y, geo.bottom() - h))
        except Exception:
            pass
        self.move(x, y)


def scroll_area_wheel_per_pixel(area, event, *, base_step: float = 46.0):
    """Wheel policy: keep ordinary notched wheels discrete, make continuous sources pixel-based.

    - pixelDelta (touchpad/precision devices): continuous and exact;
    - small/non-120 angleDelta: continuous;
    - isolated 120-step wheel notches: pass through to Qt's default discrete path;
    - rapid 120-step streams, such as free-spin wheels: treat as continuous.
    """
    try:
        orient_horizontal = bool(event.modifiers() & Qt.ShiftModifier)
        bar = area.horizontalScrollBar() if orient_horizontal else area.verticalScrollBar()
        now = time.monotonic()
        pd = event.pixelDelta()
        continuous = False
        delta_pixels = 0.0
        if not pd.isNull():
            raw = pd.x() if orient_horizontal and pd.x() else pd.y()
            delta_pixels = float(raw)
            continuous = True
        else:
            ad = event.angleDelta()
            raw = ad.x() if orient_horizontal and ad.x() else ad.y()
            if not raw:
                return False
            last_ts = float(getattr(bar, '_modern_last_wheel_ts', 0.0) or 0.0)
            # A lone 120 tick is the ordinary stepped mouse wheel.  A very dense
            # stream of ticks behaves like G502/free-spin continuous scrolling.
            is_exact_notch = (abs(int(raw)) % 120 == 0)
            rapid_stream = (now - last_ts) < 0.055
            if is_exact_notch and not rapid_stream:
                bar._modern_last_wheel_ts = now
                return False
            delta_pixels = float(raw) / 120.0 * float(base_step)
            continuous = True
        bar._modern_last_wheel_ts = now
        if continuous and delta_pixels:
            old_rem = float(getattr(bar, '_modern_wheel_remainder', 0.0) or 0.0)
            target = float(bar.value()) - delta_pixels + old_rem
            new_value = int(round(target))
            bar._modern_wheel_remainder = target - new_value
            bar.setValue(max(bar.minimum(), min(bar.maximum(), new_value)))
            event.accept()
            return True
    except Exception:
        pass
    return False


class SmoothWheelFilter(QObject):
    """Global wheel normalizer for scroll areas.

    It handles only Wheel events on QAbstractScrollArea viewports and maps them
    directly to scrollbar pixels.  There is no kinetic scrolling, overshoot, or
    delayed animation, so the scrollbar follows the wheel/touchpad delta without
    elastic end effects.
    """
    def eventFilter(self, obj, event):
        try:
            if event.type() != QEvent.Wheel:
                return False
            area = None
            if isinstance(obj, QAbstractScrollArea):
                area = obj
            elif isinstance(obj, QWidget):
                pw = obj.parentWidget()
                if isinstance(pw, QAbstractScrollArea):
                    area = pw
            if area is None:
                return False
            # DetailGraphicsView uses wheel for zoom, not scrolling.
            if isinstance(area, DetailGraphicsView) if 'DetailGraphicsView' in globals() else False:
                return False
            return scroll_area_wheel_per_pixel(area, event, base_step=42.0)
        except Exception:
            return False


class PhotoGridView(QListView):
    range_dragged = Signal(int, int, bool)
    item_open_requested = Signal(str)
    hover_item_changed = Signal(str)
    context_menu_requested = Signal(str, QPoint, bool)
    clear_selection_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._anchor_row: Optional[int] = None
        self._base_rows: set[int] = set()
        self._ctrl_mode = False
        self._drag_action = "select"
        self._drag_button = Qt.NoButton
        self._active_ranges: list[tuple[int, int]] = []
        self.selection_mode_enabled = True
        self._click_candidate_row: Optional[int] = None
        self._click_candidate_id: Optional[str] = None
        self._click_candidate_ctrl = False
        self._click_candidate_base_rows: set[int] = set()
        self._press_pos = None
        self._pre_press_selected_rows: set[int] = set()
        self._last_click_restore_rows: set[int] = set()
        self._last_click_item_id: Optional[str] = None
        self._last_click_time = 0.0
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self._commit_pending_single_click_selection)
        # Border-only visual feedback shown immediately after pressing a tile.
        # It is deliberately not part of the real selection state.
        self._provisional_row: Optional[int] = None
        self._provisional_anim_start: Optional[float] = None
        self._check_anim_start: dict[int, float] = {}
        self._deselect_anim_start: dict[int, float] = {}
        # Press animation is split into mouse-down hold and mouse-up release,
        # so the visual state follows the actual press/release gesture instead
        # of auto-rebounding before the user releases the button.
        self._press_down_start: dict[int, float] = {}
        self._press_effect_start: dict[int, float] = {}
        self._press_effect_anchor: dict[int, QPoint] = {}
        # Current row that owns the physical press feedback while dragging.
        # During an iOS-style left drag, the press effect follows the tile/row
        # under the cursor: enter = shrink, leave = rebound.
        self._active_press_row: Optional[int] = None
        self._right_click_candidate_row: Optional[int] = None
        self._right_click_candidate_id: Optional[str] = None
        self._right_click_candidate_global_pos = QPoint(0, 0)
        self._blank_left_press = False
        self._blank_press_pos = None
        self._visual_selected_rows: set[int] = set()
        self._tile_anim_timer = QTimer(self)
        self._tile_anim_timer.setTimerType(Qt.PreciseTimer)
        self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
        self._tile_anim_timer.timeout.connect(self._advance_tile_animations)

        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        try:
            # Keep Qt's native rubber-band rectangle available when dragging from
            # truly empty space.  When the drag starts on a photo tile we consume
            # the mouse events ourselves and use the iOS-style contiguous range
            # selection, so the native rectangle will not appear over photos.
            self.setSelectionRectVisible(True)
        except Exception:
            pass
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDropIndicatorShown(False)
        self.setAutoScroll(True)
        self.setSpacing(GRID_SPACING)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setGridSize(QSize(GRID_SIZE, GRID_SIZE))
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideNone)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(800)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        try:
            self.verticalScrollBar().setSingleStep(18)
            self.verticalScrollBar().setPageStep(max(120, self.height() - 80))
        except Exception:
            pass
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        prepare_scroll_area(self)
        self.setItemDelegate(PhotoGridDelegate(ICON_SIZE, GRID_SIZE, self))
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        try:
            # Passive Qt hover tracking repaints items even though our delegate does
            # not use State_MouseOver.  Do not mark the viewport as OpaquePaintEvent:
            # QListView may leave empty areas unpainted under that contract.  Use a
            # real viewport palette/background instead so the photo wall never shows
            # through to the desktop.
            self.setAttribute(Qt.WA_Hover, False)
            self.viewport().setAttribute(Qt.WA_Hover, False)
            self.setAttribute(Qt.WA_OpaquePaintEvent, False)
            self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, False)
            pal = self.viewport().palette()
            pal.setColor(QPalette.Window, QColor(CONTENT_BG))
            pal.setColor(QPalette.Base, QColor(CONTENT_BG))
            self.viewport().setPalette(pal)
            self.viewport().setAutoFillBackground(True)
            self.setAutoFillBackground(True)
        except Exception:
            pass
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            "QListView { background: #FFFFFF; border: none; outline: 0; }"
            "QListView::item { padding: 0px; margin: 0px; background: #FFFFFF; border: none; outline: 0; }"
            "QListView::item:selected { background: transparent; border: none; outline: 0; }"
            "QListView::item:focus { border: none; outline: 0; }"
        )

        self._hover_item_id = None
        self._hover_global_pos = QPoint(0, 0)
        self._tooltip_widget = FollowTooltip()
        self._tooltip_text_cache: dict[str, str] = {}
        self._pending_tooltip_id: Optional[str] = None
        self._full_tooltip_timer = QTimer(self)
        self._full_tooltip_timer.setSingleShot(True)
        self._full_tooltip_timer.setInterval(120)
        self._full_tooltip_timer.timeout.connect(self._finish_tooltip_text)

        # Hover side effects are deliberately debounced.  Moving a top-level
        # translucent tooltip and starting priority decodes on every item-enter
        # can make the photo wall look like every visible tile is trembling when
        # the mouse is shaken quickly.  The view still tracks the cursor
        # immediately for text lookup, but expensive/repainting hover effects are
        # committed only after the cursor is stable for a short moment.
        self._hover_emit_timer = QTimer(self)
        self._hover_emit_timer.setSingleShot(True)
        self._hover_emit_timer.setInterval(90)
        self._hover_emit_timer.timeout.connect(self._flush_hover_item_changed)
        self._pending_hover_emit_id = ""
        self._committed_hover_item_id = ""

        self._tooltip_defer_timer = QTimer(self)
        self._tooltip_defer_timer.setSingleShot(True)
        self._tooltip_defer_timer.setInterval(150)
        self._tooltip_defer_timer.timeout.connect(self._show_deferred_tooltip)
        self._tooltip_pending_id: Optional[str] = None
        self._tooltip_pending_text = ""
        self._tooltip_pending_pos = QPoint(0, 0)

        # When the cursor crosses the 1px gap between adjacent grid cells,
        # indexAt() can briefly return invalid.  Do not hide the tooltip
        # immediately in that seam; keep the last tooltip alive for a short
        # grace period, then hide only if the cursor is still not over any tile.
        self._tooltip_gap_hide_timer = QTimer(self)
        self._tooltip_gap_hide_timer.setSingleShot(True)
        self._tooltip_gap_hide_timer.setInterval(360)
        self._tooltip_gap_hide_timer.timeout.connect(self._hide_tooltip_after_gap)
        self._empty_state_overlay = EmptyLibraryStateOverlay(self, table_mode=False)

    def setModel(self, model):
        super().setModel(model)
        if model is not None:
            for signal_name in ("modelReset", "rowsInserted", "rowsRemoved", "layoutChanged"):
                try:
                    getattr(model, signal_name).connect(self._sync_empty_state_overlay)
                except Exception:
                    pass
        QTimer.singleShot(0, self._sync_empty_state_overlay)

    def _sync_empty_state_overlay(self, *_args):
        overlay = getattr(self, "_empty_state_overlay", None)
        if overlay is None:
            return
        overlay.setGeometry(self.viewport().rect())
        model = self.model()
        empty = model is not None and model.rowCount() == 0
        overlay.setVisible(empty)
        if empty:
            overlay.raise_()
            overlay.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_empty_state_overlay()

    def set_selection_mode_enabled(self, enabled: bool):
        self.selection_mode_enabled = bool(enabled)
        self._dragging = False
        self._anchor_row = None
        self._drag_button = Qt.NoButton
        self._click_candidate_row = None
        self._click_candidate_id = None
        self._tooltip_widget.hide()
        self.setCursor(Qt.ArrowCursor if enabled else Qt.PointingHandCursor)

    def _event_global_pos(self, event):
        try:
            return event.globalPosition().toPoint()
        except Exception:
            try:
                return event.globalPos()
            except Exception:
                return QPoint(0, 0)

    def _item_for_id(self, item_id: str):
        model = self.model()
        try:
            window = getattr(model, "window", None)
            if window is not None:
                return window.item_map.get(item_id)
        except Exception:
            pass
        return None

    def _press_row_at_pos(self, pos) -> Optional[int]:
        """Return the grid row whose *tile cell* is under the cursor.

        Shrink feedback belongs only to the row currently under the pointer; it
        is independent from the contiguous selection range.  Hit-testing the
        inner thumbnail square made the active row flip between row/None when the
        mouse jittered inside the same tile but near the image gutter.  The whole
        QListView cell is the stable tile boundary.
        """
        try:
            idx = self.indexAt(pos)
            if not idx.isValid():
                return None
            cell = self.visualRect(idx).adjusted(0, 0, -1, -1)
            p = QPoint(int(pos.x()), int(pos.y()))
            if cell.contains(p):
                return idx.row()
        except Exception:
            pass
        return None

    def _index_at_with_gap_tolerance(self, pos, radius: int = 4) -> QModelIndex:
        """Return a nearby index when the cursor is on the 1px tile seam.

        The photo wall uses very tight spacing.  When moving from one tile to the
        next, QAbstractItemView.indexAt() can briefly return an invalid index on
        the seam, causing the follow tooltip to disappear and reappear.  Tooltip
        hover is visual information, not selection hit-testing, so a tiny seam
        tolerance is appropriate here.
        """
        try:
            p = QPoint(int(pos.x()), int(pos.y()))
        except Exception:
            p = pos
        idx = self.indexAt(p)
        if idx.isValid():
            return idx
        offsets = (
            QPoint(1, 0), QPoint(-1, 0), QPoint(0, 1), QPoint(0, -1),
            QPoint(2, 0), QPoint(-2, 0), QPoint(0, 2), QPoint(0, -2),
            QPoint(radius, 0), QPoint(-radius, 0), QPoint(0, radius), QPoint(0, -radius),
            QPoint(2, 2), QPoint(-2, 2), QPoint(2, -2), QPoint(-2, -2),
        )
        for off in offsets:
            idx = self.indexAt(p + off)
            if idx.isValid():
                return idx
        return QModelIndex()

    def _queue_hover_emit(self, item_id: str):
        """Debounce expensive hover side effects.

        Plain mouse movement must not start thumbnail/LIVE work for dozens of
        tiles per second.  This method lets the tooltip know which item is under
        the cursor immediately, but delays the signal that triggers priority
        thumbnail decoding, LIVE preview, and model repaint.
        """
        item_id = item_id or ""
        if item_id == self._committed_hover_item_id and not self._hover_emit_timer.isActive():
            return
        self._pending_hover_emit_id = item_id
        if not item_id:
            self._hover_emit_timer.stop()
            self._flush_hover_item_changed()
        else:
            self._hover_emit_timer.start()

    def _flush_hover_item_changed(self):
        item_id = self._pending_hover_emit_id or ""
        if item_id == self._committed_hover_item_id:
            return
        self._committed_hover_item_id = item_id
        self.hover_item_changed.emit(item_id)

    def _set_hover_item_from_index(self, index):
        """Update cursor hover item immediately; debounce repaint-heavy effects."""
        try:
            item_id = str(index.data(ITEM_ID_ROLE)) if index is not None and index.isValid() and index.data(ITEM_ID_ROLE) else ""
        except Exception:
            item_id = ""
        new_hover = item_id or None
        if new_hover != self._hover_item_id:
            self._hover_item_id = new_hover
            self._queue_hover_emit(item_id)
        return item_id

    def _quick_tooltip_text(self, item_id: str) -> str:
        item = self._item_for_id(item_id)
        if item is None:
            return "正在读取信息……"
        try:
            model = self.model()
            window = getattr(model, "window", None)
            formatter = getattr(window, "quick_tooltip_for_item", None)
            if callable(formatter):
                return formatter(item)
        except Exception:
            pass
        return (
            f"文件名：{item.display_name}\n"
            f"类型：{item.item_type}\n"
            f"时间：{format_time(item.shot_time)}\n"
            f"容量：{format_bytes(item.size_bytes)}"
        )

    def _update_follow_tooltip(self, event):
        """Stable visual tooltip tracking for the photo wall.

        The important rule is: do not hide the bubble just because the cursor
        crosses a 1px view gap or because the item under the cursor changed.
        Hiding/re-showing a top-level tooltip is visually expensive and is the
        reason the bubble blinked while moving between adjacent tiles.  Keep the
        existing bubble alive, move it, and replace its text in-place.
        """
        if self._dragging:
            self._tooltip_gap_hide_timer.stop()
            self._tooltip_defer_timer.stop()
            self._full_tooltip_timer.stop()
            self._tooltip_widget.hide()
            return

        global_pos = self._event_global_pos(event)
        self._hover_global_pos = global_pos

        if self._tooltip_widget.isVisible():
            self._tooltip_widget.move_near(global_pos)

        index = self._index_at_with_gap_tolerance(event.position().toPoint(), radius=10)
        if not index.isValid():
            # Inside the photo wall but temporarily not over an item.  This can
            # be the 1px seam between cells, a scroll transition, or a small gap.
            # Keep the current bubble alive and only hide it after the cursor has
            # stayed away from every item for a while.  Do not clear hover here.
            if self.viewport().rect().contains(event.position().toPoint()):
                if self._tooltip_widget.isVisible():
                    self._tooltip_widget.move_near(global_pos)
                if self._hover_item_id or self._tooltip_widget.isVisible() or self._tooltip_defer_timer.isActive():
                    self._tooltip_gap_hide_timer.start(360)
                return
            self._tooltip_defer_timer.stop()
            self._tooltip_gap_hide_timer.stop()
            self._full_tooltip_timer.stop()
            self._pending_tooltip_id = None
            self._tooltip_pending_id = None
            self._set_hover_item_from_index(QModelIndex())
            self._tooltip_widget.hide()
            return

        self._tooltip_gap_hide_timer.stop()
        old_hover = self._hover_item_id
        item_id = self._set_hover_item_from_index(index)
        if not item_id:
            return

        cached = self._tooltip_text_cache.get(item_id)
        text = cached or self._quick_tooltip_text(item_id)

        if item_id != old_hover:
            # Replace text in-place.  Do NOT hide first; otherwise moving from
            # one adjacent tile to the next creates a visible off/on blink.
            self._tooltip_pending_id = item_id
            self._tooltip_pending_text = text
            self._tooltip_pending_pos = global_pos
            if self._tooltip_widget.isVisible():
                self._tooltip_widget.show_text(text, global_pos)
            else:
                self._tooltip_defer_timer.start()
            if not cached:
                self._pending_tooltip_id = item_id
                self._full_tooltip_timer.start(120)
            return

        self._tooltip_pending_pos = global_pos
        if self._tooltip_widget.isVisible():
            return
        if not self._tooltip_defer_timer.isActive():
            self._tooltip_pending_id = item_id
            self._tooltip_pending_text = text
            self._tooltip_defer_timer.start()

    def _hide_tooltip_after_gap(self):
        # Hide only if the cursor really remains away from every tile.  This is
        # deliberately more conservative than indexAt() so the tooltip does not
        # blink during tile-to-tile movement across tight seams.
        try:
            local_pos = self.viewport().mapFromGlobal(QCursor.pos())
            if self.viewport().rect().contains(local_pos):
                idx = self._index_at_with_gap_tolerance(local_pos, radius=14)
                if idx.isValid():
                    self._set_hover_item_from_index(idx)
                    return
                # Still inside the wall but not on an item after the grace
                # period: now treat it as real blank space and hide below.
        except Exception:
            pass
        self._tooltip_defer_timer.stop()
        self._full_tooltip_timer.stop()
        self._pending_tooltip_id = None
        self._tooltip_pending_id = None
        self._set_hover_item_from_index(QModelIndex())
        self._tooltip_widget.hide()

    def _show_deferred_tooltip(self):
        item_id = self._tooltip_pending_id
        if not item_id or item_id != self._hover_item_id or self._dragging:
            return
        self._tooltip_widget.show_text(self._tooltip_pending_text, self._tooltip_pending_pos)

    def _finish_tooltip_text(self):
        item_id = self._pending_tooltip_id
        self._pending_tooltip_id = None
        if not item_id or item_id != self._hover_item_id or self._dragging:
            return
        item = self._item_for_id(item_id)
        if item is None:
            return
        try:
            model = self.model()
            window = getattr(model, "window", None)
            formatter = getattr(window, "tooltip_for_item", None)
            text = formatter(item) if callable(formatter) else tooltip_for_item(item)
        except Exception:
            text = tooltip_for_item(item)
        self._tooltip_text_cache[item_id] = text
        if self._tooltip_widget.isVisible():
            self._tooltip_widget.show_text(text, self._hover_global_pos)
        else:
            self._tooltip_pending_id = item_id
            self._tooltip_pending_text = text

    def viewportEvent(self, event):
        # Disable native QToolTip and native item hover painting.  Do not update
        # or clear our logical hover from Qt HoverMove events here; mouseMoveEvent
        # is the single source of truth.  Having two paths was why seam misses
        # could still clear hover and blink the tooltip.
        if event.type() == QEvent.ToolTip:
            return True
        if event.type() in (QEvent.HoverMove, QEvent.HoverEnter, QEvent.HoverLeave):
            return True
        return super().viewportEvent(event)

    def leaveEvent(self, event):
        self._tooltip_gap_hide_timer.stop()
        self._tooltip_defer_timer.stop()
        self._full_tooltip_timer.stop()
        self._hover_emit_timer.stop()
        self._pending_tooltip_id = None
        self._tooltip_pending_id = None
        self._pending_hover_emit_id = ""
        if self._committed_hover_item_id:
            self._committed_hover_item_id = ""
            self.hover_item_changed.emit("")
        self._hover_item_id = None
        self._tooltip_widget.hide()
        super().leaveEvent(event)

    def _current_selected_rows(self) -> set[int]:
        sm = self.selectionModel()
        if sm is None:
            return set()
        return {i.row() for i in sm.selectedRows()}

    def _restore_selected_rows(self, rows: set[int]):
        model = self.model()
        sm = self.selectionModel()
        if model is None or sm is None:
            return
        max_row = model.rowCount() - 1
        selection = QItemSelection()
        for a, b in compact_ranges([r for r in rows if 0 <= r <= max_row]):
            selection.select(model.index(a, 0), model.index(b, 0))
        before = set(self._visual_selected_rows)
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self._visual_selected_rows = {r for r in rows if 0 <= r <= max_row}
        newly_selected = self._visual_selected_rows - before
        newly_deselected = before - self._visual_selected_rows
        changed = newly_selected | newly_deselected
        if len(changed) <= 80:
            self._start_check_animation_for_rows(newly_selected)
            self._start_deselect_animation_for_rows(newly_deselected)
        else:
            for r in changed:
                self._check_anim_start.pop(r, None)
                self._deselect_anim_start.pop(r, None)
        self.viewport().update()

    def _provisional_progress(self, row: int) -> float:
        if self._provisional_row != row or self._provisional_anim_start is None:
            return 1.0
        return clamp01((time.monotonic() - self._provisional_anim_start) * 1000.0 / PRESS_PREVIEW_ANIM_MS)

    def _check_progress(self, row: int) -> float:
        start = self._check_anim_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / CHECK_ANIM_MS)

    def _deselect_progress(self, row: int) -> float:
        start = self._deselect_anim_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / DESELECT_ANIM_MS)

    def _press_progress(self, row: int) -> float:
        start = self._press_effect_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / PRESS_RELEASE_ANIM_MS)

    def _press_scale(self, row: int) -> float:
        # Mouse-down: ease into a held-inward state and stay there until release.
        # Mouse-up: ease back to normal. This feels more physical than an
        # automatic pulse that rebounds before the user's finger leaves the mouse.
        now = time.monotonic()
        down_start = self._press_down_start.get(row)
        if down_start is not None:
            p = clamp01((now - down_start) * 1000.0 / PRESS_DOWN_ANIM_MS)
            return 1.0 - 0.046 * ease_out_cubic(p)
        release_start = self._press_effect_start.get(row)
        if release_start is not None:
            p = clamp01((now - release_start) * 1000.0 / PRESS_RELEASE_ANIM_MS)
            return 1.0 - 0.046 * (1.0 - ease_out_quint(p))
        return 1.0

    def _press_anchor(self, row: int):
        return self._press_effect_anchor.get(row)

    def _start_press_effect_for_row(self, row: int, pos):
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        if row < 0 or row > max_row:
            return
        self._press_down_start[row] = time.monotonic()
        self._press_effect_start.pop(row, None)
        try:
            # Center anchor: scaling around the mouse position makes the tile
            # appear to slide/wobble when the cursor moves inside the same tile.
            cell = self.visualRect(model.index(row, 0))
            c = cell.center()
            self._press_effect_anchor[row] = QPoint(int(c.x()), int(c.y()))
        except Exception:
            self._press_effect_anchor[row] = QPoint(0, 0)
        self._update_visual_row(row)
        if not self._tile_anim_timer.isActive():
            self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._tile_anim_timer.start()

    def _release_press_effect_for_row(self, row: Optional[int]):
        if row is None:
            return
        # Only the original pressed item owns the press animation.  Once the
        # pointer leaves it and the release animation has begun, do not restart
        # that animation on every subsequent mouse move or final mouse release.
        if row in self._press_down_start:
            self._press_down_start.pop(row, None)
            self._press_effect_start[row] = time.monotonic()
            self._update_visual_row(row)
            if not self._tile_anim_timer.isActive():
                self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
                self._tile_anim_timer.start()
        elif row in self._press_effect_start:
            return


    def _move_press_effect_to_row(self, row: Optional[int], pos):
        """Move the physical press feedback to the row currently under the cursor.

        This is used during left-button range selection: every tile/row the
        pointer enters gets its own press-in animation, and it rebounds when the
        pointer leaves.  Only the currently hovered row is held pressed.
        """
        if row == self._active_press_row:
            return
        old = self._active_press_row
        if old is not None:
            self._release_press_effect_for_row(old)
        self._active_press_row = row
        if row is not None:
            self._start_press_effect_for_row(row, pos)

    def _release_active_press_effect(self):
        row = self._active_press_row
        self._active_press_row = None
        if row is not None:
            self._release_press_effect_for_row(row)

    def _cleanup_stale_press_if_no_button(self, event=None):
        """Convert any stuck mouse-down shrink state into a release animation.

        If Windows/Qt drops a release event, a no-button hover move must not keep
        any tile in the held-down scale state.  Release animations may still run
        briefly, but no new tile can shrink unless a mouse button is actually held.
        """
        try:
            buttons = event.buttons() if event is not None else QApplication.mouseButtons()
        except Exception:
            buttons = QApplication.mouseButtons()
        if buttons & (Qt.LeftButton | Qt.RightButton):
            return
        rows = list(self._press_down_start.keys())
        self._active_press_row = None
        for row in rows:
            self._release_press_effect_for_row(row)

    def _clear_all_press_effects(self):
        """Emergency cleanup for all tile-scale animations.

        Normal left-drag behavior should not call this: press feedback is now
        enter/leave based, so the first tile presses on mouse down, intermediate
        actually-hovered tiles press on enter/rebound on leave, and the final
        tile rebounds on mouse release.
        """
        rows = set(self._press_down_start) | set(self._press_effect_start) | set(self._press_effect_anchor)
        active = self._active_press_row
        if active is not None:
            rows.add(active)
        self._active_press_row = None
        self._press_down_start.clear()
        self._press_effect_start.clear()
        self._press_effect_anchor.clear()
        for r in rows:
            self._update_visual_row(r)

    def _start_deselect_animation_for_rows(self, rows: set[int]):
        if not rows:
            return
        now = time.monotonic()
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        for row in rows:
            if 0 <= row <= max_row:
                self._deselect_anim_start[row] = now
                self._check_anim_start.pop(row, None)
                self._update_visual_row(row)
        if not self._tile_anim_timer.isActive():
            self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._tile_anim_timer.start()

    def _start_check_animation_for_rows(self, rows: set[int]):
        if not rows:
            return
        now = time.monotonic()
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        for row in rows:
            if 0 <= row <= max_row:
                self._check_anim_start[row] = now
                self._update_visual_row(row)
        if not self._tile_anim_timer.isActive():
            self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._tile_anim_timer.start()

    def _advance_tile_animations(self):
        active = False
        now = time.monotonic()
        if self._provisional_row is not None:
            self._update_visual_row(self._provisional_row)
            if self._provisional_anim_start is not None and (now - self._provisional_anim_start) * 1000.0 < PRESS_PREVIEW_ANIM_MS:
                active = True
        done = []
        for row, start in list(self._check_anim_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= CHECK_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._check_anim_start.pop(row, None)
            self._update_visual_row(row)
        done = []
        for row, start in list(self._deselect_anim_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= DESELECT_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._deselect_anim_start.pop(row, None)
            self._update_visual_row(row)
        for row, start in list(self._press_down_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 < PRESS_DOWN_ANIM_MS:
                active = True
        done = []
        for row, start in list(self._press_effect_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= PRESS_RELEASE_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._press_effect_start.pop(row, None)
            self._press_effect_anchor.pop(row, None)
            self._update_visual_row(row)
        if not active:
            self._tile_anim_timer.stop()

    def selectionChanged(self, selected, deselected):
        super().selectionChanged(selected, deselected)
        previous = set(self._visual_selected_rows)
        current = self._current_selected_rows()
        newly_selected = current - previous
        newly_deselected = previous - current
        self._visual_selected_rows = set(current)
        self._start_check_animation_for_rows(newly_selected)
        self._start_deselect_animation_for_rows(newly_deselected)

    def _update_visual_row(self, row: Optional[int]):
        if row is None:
            return
        model = self.model()
        if model is None or row < 0 or row >= model.rowCount():
            return
        try:
            idx = model.index(row, 0)
            self.viewport().update(self.visualRect(idx))
        except Exception:
            self.viewport().update()

    def _set_provisional_row(self, row: Optional[int]):
        old = self._provisional_row
        if old == row:
            return
        self._provisional_row = row
        self._provisional_anim_start = time.monotonic() if row is not None else None
        self._update_visual_row(old)
        self._update_visual_row(row)
        if row is not None and not self._tile_anim_timer.isActive():
            self._tile_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._tile_anim_timer.start()

    def _clear_provisional_row(self):
        self._set_provisional_row(None)

    def _selection_rows_for_single_left_click(self, row: int, ctrl: bool, base_rows: set[int]) -> set[int]:
        """Single-click semantics for the new left-button-only workflow.

        - Ctrl + left click toggles the clicked tile.
        - Plain left click on a tile clears the current selection.  This makes
          preview/double-click workflows feel less like ordinary file-manager
          selection and keeps all positive/negative selection actions on drag.
        """
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        if row < 0 or row > max_row:
            return set(base_rows)
        if ctrl:
            rows = set(base_rows)
            if row in rows:
                rows.remove(row)
            else:
                rows.add(row)
            return rows
        # Plain click on a tile/row starts a fresh single-item selection.
        # But if exactly this one item is already selected, clicking it once again
        # cancels the selection. Double-click preview restores the pre-click state,
        # so this does not pollute the open-preview gesture.
        if set(base_rows) == {row}:
            return set()
        return {row}

    def _schedule_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        # Do not wait for the full OS double-click interval; it makes selection feel
        # visibly delayed.  A short delay catches most fast double-clicks while keeping
        # single-click selection responsive.
        self._single_click_timer.start(GRID_SINGLE_CLICK_SELECTION_DELAY_MS)

    def _commit_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        rows = self._selection_rows_for_single_left_click(
            self._click_candidate_row,
            self._click_candidate_ctrl,
            self._click_candidate_base_rows,
        )
        self._restore_selected_rows(rows)
        self._clear_provisional_row()
        self._last_click_restore_rows = set(self._click_candidate_base_rows)
        self._last_click_item_id = str(self._click_candidate_id) if self._click_candidate_id else None
        self._last_click_time = time.monotonic()
        self._click_candidate_row = None
        self._click_candidate_id = None
        self._click_candidate_base_rows = set()
        self._press_pos = None

    def _cancel_pending_single_click_selection(self):
        self._single_click_timer.stop()
        self._clear_provisional_row()
        self._click_candidate_row = None
        self._click_candidate_id = None
        self._click_candidate_base_rows = set()
        self._release_active_press_effect()
        self._press_pos = None

    def wheelEvent(self, event):
        if scroll_area_wheel_per_pixel(self, event, base_step=44.0):
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                # Double-click opens preview only. The first click no longer performs an
                # immediate selection, so there is no temporary false highlight to undo.
                self._cancel_pending_single_click_selection()
                self._clear_provisional_row()
                self._dragging = False
                self._anchor_row = None
                self._drag_button = Qt.NoButton
                item_id = str(index.data(ITEM_ID_ROLE) or "")
                restore_rows = self._pre_press_selected_rows
                if (
                    self._last_click_item_id == item_id
                    and (time.monotonic() - self._last_click_time) <= (QApplication.doubleClickInterval() + 180) / 1000.0
                ):
                    restore_rows = self._last_click_restore_rows
                self._restore_selected_rows(restore_rows)
                self.item_open_requested.emit(item_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        self._tooltip_widget.hide()
        self._full_tooltip_timer.stop()
        self._single_click_timer.stop()
        self._pre_press_selected_rows = self._current_selected_rows()
        button = event.button()
        if not self.selection_mode_enabled:
            index = self.indexAt(event.position().toPoint())
            if button == Qt.LeftButton and index.isValid():
                self._click_candidate_row = index.row()
                self._click_candidate_id = index.data(ITEM_ID_ROLE)
                event.accept()
                return
            event.accept()
            return
        if button == Qt.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                press_row = self._press_row_at_pos(event.position().toPoint())
                if press_row is not None:
                    self._start_press_effect_for_row(press_row, event.position().toPoint())
                    self._active_press_row = press_row
                else:
                    self._active_press_row = None
                # Do not select on press. Wait until drag movement or until the
                # double-click interval expires after release. This removes the brief
                # wrong selection flash when the user is actually double-clicking to preview.
                self._dragging = False
                self._anchor_row = index.row()
                self._drag_button = Qt.LeftButton
                self._drag_action = "toggle"
                self._ctrl_mode = bool(event.modifiers() & Qt.ControlModifier)
                self._click_candidate_row = index.row()
                self._click_candidate_id = index.data(ITEM_ID_ROLE)
                self._click_candidate_ctrl = self._ctrl_mode
                self._click_candidate_base_rows = set(self._pre_press_selected_rows)
                self._press_pos = event.position().toPoint()
                # Tile drag is a range-toggle operation. Without Ctrl, the range
                # starts from an empty selection; with Ctrl, it continues from the
                # existing selection. This makes Ctrl meaningful and avoids keeping
                # old selections during a fresh drag.
                self._base_rows = set(self._pre_press_selected_rows) if self._ctrl_mode else set()
                # Instant press feedback: border only. The check mark appears only
                # after the click is confirmed as an actual selection.
                if index.row() not in self._pre_press_selected_rows:
                    self._set_provisional_row(index.row())
                else:
                    self._clear_provisional_row()
                event.accept()
                return
            # Empty-area left press: let Qt keep its native rectangle selection
            # for drags, but a plain click on blank space clears all selection.
            self._blank_left_press = True
            self._blank_press_pos = event.position().toPoint()
            super().mousePressEvent(event)
            return
        if button == Qt.RightButton:
            self._clear_provisional_row()
            index = self.indexAt(event.position().toPoint())
            self._right_click_candidate_row = index.row() if index.isValid() else None
            self._right_click_candidate_id = str(index.data(ITEM_ID_ROLE) or "") if index.isValid() else None
            self._right_click_candidate_global_pos = self._event_global_pos(event)
            if index.isValid():
                # Right and left now share the exact same physical press path:
                # press-in on mouse down, release on mouse up.  The context menu is
                # opened after release so the menu cannot swallow the release frame.
                self._start_press_effect_for_row(index.row(), event.position().toPoint())
                self._active_press_row = index.row()
            event.accept()
            return
        self._clear_provisional_row()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Only clean stale press states during passive hover. During an active
        # drag, some Windows/Qt paths can transiently report NoButton; treating
        # that as a real release was a same-tile wobble source.
        if self._drag_button == Qt.NoButton and not self._dragging:
            self._cleanup_stale_press_if_no_button(event)
        if not self.selection_mode_enabled:
            self._update_follow_tooltip(event)
            event.accept()
            return
        if self._drag_button == Qt.LeftButton and self._anchor_row is not None:
            index = self.indexAt(event.position().toPoint())
            moved_far = False
            try:
                if self._press_pos is not None:
                    moved_far = (event.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
            except Exception:
                moved_far = True
            if self._dragging or moved_far or (index.isValid() and index.row() != self._anchor_row):
                self._dragging = True
                self._clear_provisional_row()
                # Physical press feedback follows only the tile that the mouse
                # actually enters.  Selection may expand over many intermediate
                # rows, but those rows do not shrink unless a mouse-move event
                # places the cursor inside their real thumbnail rectangle.
                self._move_press_effect_to_row(self._press_row_at_pos(event.position().toPoint()), event.position().toPoint())
                if index.isValid():
                    self.apply_range_selection(index.row(), finished=False)
                    self.scrollTo(index, QAbstractItemView.EnsureVisible)
                else:
                    self._move_press_effect_to_row(None, event.position().toPoint())
                event.accept()
                return
            # The press started on a photo tile, so do not let Qt start a native
            # rubber-band.  Rubber-band is reserved for drags that begin from
            # empty space only.
            event.accept()
            return
        if self._dragging and self._anchor_row is not None:
            index = self.indexAt(event.position().toPoint())
            self._move_press_effect_to_row(self._press_row_at_pos(event.position().toPoint()), event.position().toPoint())
            if index.isValid():
                self.apply_range_selection(index.row(), finished=False)
                self.scrollTo(index, QAbstractItemView.EnsureVisible)
            else:
                self._move_press_effect_to_row(None, event.position().toPoint())
            event.accept()
            return
        self._update_follow_tooltip(event)
        # Do not hand passive hover moves to QAbstractItemView.  Its internal
        # hover-state repainting can invalidate many items rapidly even though
        # our delegate does not use the native hover state, which was the main
        # source of no-button tile trembling.
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self.selection_mode_enabled:
            if event.button() == Qt.LeftButton and self._click_candidate_id:
                index = self.indexAt(event.position().toPoint())
                if index.isValid() and index.row() == self._click_candidate_row:
                    self.item_open_requested.emit(str(self._click_candidate_id))
            self._click_candidate_row = None
            self._click_candidate_id = None
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._blank_left_press:
            moved_far = False
            try:
                if self._blank_press_pos is not None:
                    moved_far = (event.position().toPoint() - self._blank_press_pos).manhattanLength() >= QApplication.startDragDistance()
            except Exception:
                moved_far = True
            self._blank_left_press = False
            self._blank_press_pos = None
            if not moved_far and not self.indexAt(event.position().toPoint()).isValid():
                self.clear_selection_requested.emit()
                event.accept()
                return
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.LeftButton and self._drag_button == Qt.LeftButton and self._anchor_row is not None:
            self._release_active_press_effect()
            if self._dragging:
                self._clear_provisional_row()
                index = self.indexAt(event.position().toPoint())
                current = index.row() if index.isValid() else self._anchor_row
                self.apply_range_selection(current, finished=True)
                self._cancel_pending_single_click_selection()
            else:
                index = self.indexAt(event.position().toPoint())
                if index.isValid() and index.row() == self._click_candidate_row:
                    self._schedule_pending_single_click_selection()
                else:
                    self._cancel_pending_single_click_selection()
            self._dragging = False
            self._anchor_row = None
            self._drag_button = Qt.NoButton
            event.accept()
            return
        if event.button() == self._drag_button and self._dragging and self._anchor_row is not None:
            self._release_active_press_effect()
            index = self.indexAt(event.position().toPoint())
            current = index.row() if index.isValid() else self._anchor_row
            self.apply_range_selection(current, finished=True)
            self._dragging = False
            self._anchor_row = None
            self._drag_button = Qt.NoButton
            self._cancel_pending_single_click_selection()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            idx = self.indexAt(event.position().toPoint())
            release_row = self._right_click_candidate_row
            self._release_active_press_effect()
            selected_rows = self._current_selected_rows()
            candidate_row = release_row if release_row is not None else (idx.row() if idx.isValid() else None)
            candidate_id = self._right_click_candidate_id or (str(idx.data(ITEM_ID_ROLE) or "") if idx.isValid() else "")
            global_pos = self._event_global_pos(event)
            if candidate_row is not None and candidate_id:
                if selected_rows and candidate_row in selected_rows:
                    self.context_menu_requested.emit(candidate_id, global_pos, False)
                elif not selected_rows:
                    self._restore_selected_rows({candidate_row})
                    self.context_menu_requested.emit(candidate_id, global_pos, True)
                else:
                    self.clear_selection_requested.emit()
            else:
                self.clear_selection_requested.emit()
            self._right_click_candidate_row = None
            self._right_click_candidate_id = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def apply_range_selection(self, current_row: int, finished: bool):
        if self._anchor_row is None:
            return
        self._clear_provisional_row()
        model = self.model()
        if model is None:
            return
        max_row = model.rowCount() - 1
        if max_row < 0:
            return
        start = max(0, min(self._anchor_row, current_row))
        end = min(max_row, max(self._anchor_row, current_row))

        drag_rows = set(range(start, end + 1))
        if self._drag_action == "toggle":
            rows = set(self._base_rows)
            for r in drag_rows:
                if r in rows:
                    rows.remove(r)
                else:
                    rows.add(r)
        elif self._drag_action == "select":
            rows = drag_rows | self._base_rows
        else:
            rows = {r for r in self._base_rows if 0 <= r <= max_row and r not in drag_rows}

        ranges = compact_ranges([r for r in rows if 0 <= r <= max_row])
        self._active_ranges = ranges
        selection = QItemSelection()
        for a, b in ranges:
            selection.select(model.index(a, 0), model.index(b, 0))
        sm = self.selectionModel()
        if sm is None:
            return
        before = set(self._visual_selected_rows)
        blocker = QSignalBlocker(sm)
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        del blocker
        self._visual_selected_rows = set(rows)
        newly_selected = self._visual_selected_rows - before
        newly_deselected = before - self._visual_selected_rows
        changed = newly_selected | newly_deselected
        # Drag-range selection must not pop/check-animate every item in the
        # contiguous range: most of those tiles were never physically crossed by
        # the cursor, and the pop animation looks like random trembling.  During
        # a drag, selection state updates immediately; the only motion allowed is
        # the separate press-scale animation owned by the tile currently entered.
        for r in changed:
            self._check_anim_start.pop(r, None)
            self._deselect_anim_start.pop(r, None)
            self._update_visual_row(r)
        # Do not repaint the whole viewport on every drag step.  Full viewport
        # invalidation makes every visible pixmap redraw while the cursor moves,
        # which is perceived as global tile/label trembling on some Windows/DPI
        # combinations.
        self.range_dragged.emit(self._anchor_row, current_row, finished)



class DetailGraphicsView(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.TextAntialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        prepare_scroll_area(self)
        self.setStyleSheet(f"QGraphicsView {{ background: {DETAIL_VIEW_BG}; border: none; }}")
        self._zoom = 1.0

    def _sync_zoom_from_transform(self):
        try:
            self._zoom = max(0.01, float(self.transform().m11()))
        except Exception:
            self._zoom = 1.0
        self.zoom_changed.emit(self._zoom)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if not (0.02 <= new_zoom <= 40.0):
            return
        self.scale(factor, factor)
        self._sync_zoom_from_transform()
        event.accept()

    def reset_zoom_fit(self):
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect()
        if not rect.isEmpty():
            self.resetTransform()
            self.fitInView(rect, Qt.KeepAspectRatio)
            self._sync_zoom_from_transform()

    def set_zoom_100(self):
        self.resetTransform()
        self._sync_zoom_from_transform()

    def mouseDoubleClickEvent(self, event):
        self.reset_zoom_fit()
        event.accept()




def _resize_edge_name_from_wmsz(code: int) -> str:
    return {
        1: 'left',
        2: 'right',
        3: 'top',
        4: 'top-left',
        5: 'top-right',
        6: 'bottom',
        7: 'bottom-left',
        8: 'bottom-right',
    }.get(int(code), '')

def install_frameless_window_native_features(window: QWidget):
    """Give a frameless Qt window normal Windows taskbar/minimize behavior.

    Qt.FramelessWindowHint often removes minimize/maximize/system-menu styles.
    Without WS_MINIMIZEBOX/WS_SYSMENU, clicking the taskbar button may only
    activate the window instead of minimizing it.  We restore those styles while
    keeping the client area frameless.
    """
    if os.name != "nt":
        return
    try:
        hwnd = int(window.winId())
        GWL_STYLE = -16
        WS_SYSMENU = 0x00080000
        WS_THICKFRAME = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        get_long = ctypes.windll.user32.GetWindowLongW
        set_long = ctypes.windll.user32.SetWindowLongW
        style = int(get_long(hwnd, GWL_STYLE))
        style |= (WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_THICKFRAME)
        set_long(hwnd, GWL_STYLE, style)
        ctypes.windll.user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        )
    except Exception:
        pass


def handle_frameless_nccalcsize(event_type, message):
    """Remove Windows' invisible resize frame from a frameless client area."""

    if os.name != "nt":
        return None
    try:
        name = event_type.decode() if isinstance(event_type, (bytes, bytearray)) else str(event_type)
        if "windows" not in name:
            return None
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if int(msg.message) == 0x0083:  # WM_NCCALCSIZE
            return True, 0
    except Exception:
        pass
    return None


def set_native_corner_preference(window: QWidget, *, rounded: bool) -> bool:
    """Set DWM corner policy, including explicit square corners for classic themes."""
    if os.name != "nt":
        return False
    try:
        hwnd = int(window.winId())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        value = ctypes.c_int(2 if rounded else 1)  # ROUND / DONOTROUND
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return int(hr) == 0
    except Exception:
        return False


def enable_native_rounded_corners_if_available(window: QWidget) -> bool:
    """Ask DWM for native antialiased rounded corners and report success.

    On Windows 11 this gives real per-pixel antialiasing without a hard QRegion
    mask. On Windows 10 the call usually fails or is ignored, so callers can fall
    back to a Qt mask.  Returning a boolean is important: combining a DWM rounded
    corner with a 1-bit Qt mask reintroduces jagged edges and resize jitter.
    """
    return set_native_corner_preference(window, rounded=True)


def apply_opaque_rounded_window_mask(window: QWidget, radius: int = 28):
    """Keep the outer window corner intact without using layered transparency.

    Prefer DWM native rounded corners when available.  On systems that do not
    provide antialiased DWM corners, use a QWidget mask as a stability fallback;
    it is binary, but it prevents the opaque top-level background from filling
    the corner as a rectangle.  Do not combine the DWM rounded-corner path and
    the Qt mask path: doing both creates jagged corners and resize instability.
    """
    try:
        if window.isMaximized() or window.isFullScreen():
            window.clearMask()
            return
        if getattr(globals().get("RUNTIME_THEME_PROFILE"), "corner_style", "continuous") == "square":
            set_native_corner_preference(window, rounded=False)
            window._dwm_native_round_corners = False
            window.clearMask()
            return
        if getattr(globals().get("RUNTIME_THEME_PROFILE"), "control_style", "apple") == "win7":
            set_native_corner_preference(window, rounded=False)
            window._dwm_native_round_corners = False
        # Cache the DWM result. Calling DwmSetWindowAttribute on every resize
        # is unnecessary and can itself contribute to resize-time instability.
        native = getattr(window, '_dwm_native_round_corners', None)
        if native is None:
            native = bool(os.name == 'nt' and enable_native_rounded_corners_if_available(window))
            window._dwm_native_round_corners = native
        if native:
            window.clearMask()
            return
        rect = QRectF(window.rect())
        if rect.width() <= 2 or rect.height() <= 2:
            return
        path = l2_superellipse_path(rect.adjusted(0, 0, -1, -1), radius=radius, samples=96)
        region = QRegion(path.toFillPolygon().toPolygon())
        window.setMask(region)
    except Exception:
        pass


def maybe_update_live_resize_window_mask(window: QWidget, radius: int = 28, interval_ms: int = 28):
    """Keep the opaque top-level R corner current during native live resize.

    The old versions either kept a stale pre-resize mask, which made the opposite
    corner disappear when the user dragged the left/top edge, or updated the mask
    on every resize event, which could add jitter.  This throttles the fallback
    Qt mask path while letting DWM-native corners do nothing.  The freeze overlay
    hides child-layout work, so this mask update only affects the outer HWND
    region and should not pull the content or the opposite edge around.
    """
    try:
        if window.isMaximized() or window.isFullScreen():
            window.clearMask()
            return
        if bool(getattr(window, '_dwm_native_round_corners', False)):
            return
        now = time.monotonic()
        last = float(getattr(window, '_last_live_mask_update_ts', 0.0) or 0.0)
        if (now - last) * 1000.0 < interval_ms:
            return
        window._last_live_mask_update_ts = now
        apply_opaque_rounded_window_mask(window, radius)
    except Exception:
        pass


class ResizeFreezeOverlay(QWidget):
    """Full-window live-resize cover with a borderless content snapshot.

    This is intentionally a child of the top-level window, not of the shell.
    During native Windows resize, the shell/layout may move or relayout a little
    later than the HWND geometry.  If the freeze layer is parented to the shell,
    left/top resizing can inherit that layout jitter.  A full-window cover stays
    at (0, 0) in the top-level coordinate system and only repaints the current
    window-sized surface:
      - current rounded shell/background is drawn live;
      - the old inner content bitmap is always drawn at the same local top-left;
      - growth reveals APP_BG; shrinkage clips the bitmap.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._snapshot_pos = QPoint(2, 2)
        self._shell_rect = QRect()
        self._fixed_shell_margin = 0
        self._radius = 28
        self._snapshot_opacity = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.hide()

    def set_snapshot(self, pixmap: QPixmap, pos: QPoint | None = None, shell_rect: QRect | None = None, radius: int = 28):
        self._pixmap = pixmap
        self._snapshot_pos = QPoint(pos) if pos is not None else QPoint(2, 2)
        self._shell_rect = QRect(shell_rect) if shell_rect is not None else QRect(self.rect())
        self._fixed_shell_margin = max(0, int(self._shell_rect.left()))
        self._radius = int(radius)
        self._snapshot_opacity = 1.0
        self.update()

    def set_snapshot_opacity(self, value: float):
        try:
            self._snapshot_opacity = max(0.0, min(1.0, float(value)))
        except Exception:
            self._snapshot_opacity = 1.0
        self.update()

    def set_resize_edge(self, edge: str = ""):
        # The cover is deliberately top-left anchored.  Edges no longer affect
        # snapshot placement, which avoids the old left/top anchor fighting.
        pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), QColor(APP_BG))

        # Draw the live outer shell from the *current* window rectangle, not
        # from the disabled shell widget's geometry.  During left/top resizing
        # QWidget geometry can lag behind the HWND by a frame; using it here was
        # the source of opposite-edge corner loss and border jitter.
        m = max(0, int(getattr(self, '_fixed_shell_margin', 0)))
        current_shell = self.rect().adjusted(m, m, -m, -m)
        shell_rect = QRectF(current_shell if current_shell.isValid() else self.rect())
        if shell_rect.width() > 2 and shell_rect.height() > 2:
            path = l2_superellipse_path(shell_rect.adjusted(0.5, 0.5, -0.5, -0.5), radius=self._radius, samples=96)
            painter.setPen(Qt.NoPen)
            painter.fillPath(path, QColor(APP_BG))
            pen = QPen(QColor(APP_BORDER), 1.0)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawPath(path)

        if not self._pixmap.isNull():
            # Clip to the shell interior.  This keeps the current border/R-corner
            # visible and prevents the old bitmap from covering live rounded edges.
            # Only the frozen *content* is allowed to fade; the live border is
            # deliberately kept fully opaque so the outline never disappears
            # during the fade back to the real widgets.
            clip_rect = shell_rect.adjusted(2.0, 2.0, -2.0, -2.0)
            painter.save()
            painter.setClipRect(clip_rect)
            painter.setOpacity(max(0.0, min(1.0, float(getattr(self, '_snapshot_opacity', 1.0)))))
            painter.drawPixmap(self._snapshot_pos, self._pixmap)
            painter.restore()

class ModernScrollBar(QScrollBar):
    """Single, explicit scrollbar implementation used by all scroll areas.

    It does not rely on native arrows or stylesheet subcontrols, so QTableView,
    QListView, QTextBrowser and GraphicsView get the same rounded scrollbar.
    Dragging the thumb is continuous. Wheel behavior is handled by the global
    SmoothWheelFilter so notched wheels can remain notched while precision input
    remains pixel-based.
    """
    def __init__(self, orientation=Qt.Vertical, parent=None):
        super().__init__(orientation, parent)
        self._dragging_thumb = False
        self._drag_offset = 0.0
        self._hover_thumb = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        if orientation == Qt.Vertical:
            self.setFixedWidth(14)
        else:
            self.setFixedHeight(14)
        self.valueChanged.connect(lambda *_: self.update())
        self.rangeChanged.connect(lambda *_: self.update())

    def _extent(self) -> int:
        style = getattr(globals().get("RUNTIME_THEME_PROFILE"), "control_style", "apple")
        return 17 if style == "win7" else (16 if style in {"win2000", "macos8"} else (12 if style == "win11" else 14))

    def apply_theme(self, profile=None):
        extent = self._extent()
        if self.orientation() == Qt.Vertical:
            self.setFixedWidth(extent)
        else:
            self.setFixedHeight(extent)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        extent = self._extent()
        return QSize(extent, 80) if self.orientation() == Qt.Vertical else QSize(80, extent)

    def _track_rect(self) -> QRectF:
        r = QRectF(self.rect())
        style = getattr(globals().get("RUNTIME_THEME_PROFILE"), "control_style", "apple")
        if style in {"win7", "win2000", "macos8"}:
            extent = self._extent()
            if self.orientation() == Qt.Vertical:
                return QRectF(r.left() + 2, r.top() + extent, max(1.0, r.width() - 4), max(1.0, r.height() - extent * 2))
            return QRectF(r.left() + extent, r.top() + 2, max(1.0, r.width() - extent * 2), max(1.0, r.height() - 4))
        if self.orientation() == Qt.Vertical:
            return QRectF(r.center().x() - 3.0, r.top() + 3.0, 6.0, max(1.0, r.height() - 6.0))
        return QRectF(r.left() + 3.0, r.center().y() - 3.0, max(1.0, r.width() - 6.0), 6.0)

    def _thumb_rect(self) -> QRectF:
        tr = self._track_rect()
        minimum = self.minimum()
        maximum = self.maximum()
        page = max(1, self.pageStep())
        rng = max(0, maximum - minimum)
        if self.orientation() == Qt.Vertical:
            track_len = tr.height()
            if rng <= 0:
                return QRectF(tr.left(), tr.top(), tr.width(), track_len)
            length = max(46.0, track_len * page / (rng + page))
            length = min(track_len, length)
            travel = max(1.0, track_len - length)
            pos = (self.value() - minimum) / max(1, rng)
            return QRectF(tr.left(), tr.top() + travel * pos, tr.width(), length)
        track_len = tr.width()
        if rng <= 0:
            return QRectF(tr.left(), tr.top(), track_len, tr.height())
        length = max(46.0, track_len * page / (rng + page))
        length = min(track_len, length)
        travel = max(1.0, track_len - length)
        pos = (self.value() - minimum) / max(1, rng)
        return QRectF(tr.left() + travel * pos, tr.top(), length, tr.height())

    def _value_from_pos(self, p: QPoint, thumb_offset: float | None = None) -> int:
        tr = self._track_rect()
        th = self._thumb_rect()
        minimum = self.minimum()
        maximum = self.maximum()
        rng = max(0, maximum - minimum)
        if rng <= 0:
            return minimum
        if self.orientation() == Qt.Vertical:
            coord = float(p.y()) - (thumb_offset if thumb_offset is not None else th.height() / 2.0)
            travel = max(1.0, tr.height() - th.height())
            frac = (coord - tr.top()) / travel
        else:
            coord = float(p.x()) - (thumb_offset if thumb_offset is not None else th.width() / 2.0)
            travel = max(1.0, tr.width() - th.width())
            frac = (coord - tr.left()) / travel
        frac = max(0.0, min(1.0, frac))
        return int(round(minimum + frac * rng))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        profile = globals().get("RUNTIME_THEME_PROFILE")
        style = profile.control_style
        painter.fillRect(self.rect(), QColor(profile.panel_2 if style != "apple" else APP_BG))
        track = self._track_rect()
        thumb = self._thumb_rect()
        if style in {"win7", "win2000", "macos8"}:
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.fillRect(track, QColor("#FFFFFF" if style == "win7" else profile.content))
            painter.setPen(QPen(QColor(profile.border), 1))
            painter.drawRect(track)
            if self.maximum() > self.minimum():
                fill = QColor("#D8EAF5" if style == "win7" else ("#DDDDDD" if style == "macos8" else "#D4D0C8"))
                painter.fillRect(thumb, fill)
                painter.setPen(QPen(QColor("#FFFFFF"), 1))
                painter.drawLine(thumb.topLeft(), thumb.topRight())
                painter.drawLine(thumb.topLeft(), thumb.bottomLeft())
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.drawLine(thumb.bottomLeft(), thumb.bottomRight())
                painter.drawLine(thumb.topRight(), thumb.bottomRight())
            extent = self._extent()
            if self.orientation() == Qt.Vertical:
                buttons = [QRectF(0, 0, self.width(), extent), QRectF(0, self.height() - extent, self.width(), extent)]
            else:
                buttons = [QRectF(0, 0, extent, self.height()), QRectF(self.width() - extent, 0, extent, self.height())]
            for index, button in enumerate(buttons):
                painter.fillRect(button, QColor("#E7F2F9" if style == "win7" else profile.panel))
                painter.setPen(QPen(QColor(profile.border), 1))
                # drawRect also uses the current brush. The first arrow leaves a
                # black brush behind, so reset it before outlining the next button.
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(button.adjusted(0, 0, -1, -1))
                c = button.center()
                painter.setBrush(QColor(profile.text))
                painter.setPen(Qt.NoPen)
                if self.orientation() == Qt.Vertical:
                    points = [QPointF(c.x(), c.y() - 3), QPointF(c.x() - 3, c.y() + 2), QPointF(c.x() + 3, c.y() + 2)] if index == 0 else [QPointF(c.x(), c.y() + 3), QPointF(c.x() - 3, c.y() - 2), QPointF(c.x() + 3, c.y() - 2)]
                else:
                    points = [QPointF(c.x() - 3, c.y()), QPointF(c.x() + 2, c.y() - 3), QPointF(c.x() + 2, c.y() + 3)] if index == 0 else [QPointF(c.x() + 3, c.y()), QPointF(c.x() - 2, c.y() - 3), QPointF(c.x() - 2, c.y() + 3)]
                arrow = QPainterPath()
                arrow.moveTo(points[0])
                arrow.lineTo(points[1]); arrow.lineTo(points[2]); arrow.closeSubpath()
                painter.drawPath(arrow)
            return
        painter.setPen(Qt.NoPen)
        if track.width() > 0 and track.height() > 0:
            painter.setBrush(QColor(176, 188, 202, 80))
            r = min(track.width(), track.height()) / 2.0
            painter.drawRoundedRect(track, r, r)
        if self.maximum() > self.minimum() and thumb.width() > 0 and thumb.height() > 0:
            if self._dragging_thumb:
                color = QColor('#7F8E9F')
            elif self._hover_thumb:
                color = QColor('#909FB0')
            else:
                color = QColor('#9EACBB')
            painter.setBrush(color)
            r = min(thumb.width(), thumb.height()) / 2.0
            painter.drawRoundedRect(thumb, r, r)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            thumb = self._thumb_rect()
            pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
            style = getattr(globals().get("RUNTIME_THEME_PROFILE"), "control_style", "apple")
            if style in {"win7", "win2000", "macos8"}:
                extent = self._extent()
                coord = pos.y() if self.orientation() == Qt.Vertical else pos.x()
                length = self.height() if self.orientation() == Qt.Vertical else self.width()
                if coord < extent:
                    self.setValue(self.value() - self.singleStep())
                    event.accept()
                    return
                if coord >= length - extent:
                    self.setValue(self.value() + self.singleStep())
                    event.accept()
                    return
            if thumb.contains(QRectF(pos, QSize(1, 1)).center()):
                self._dragging_thumb = True
                self._drag_offset = (pos.y() - thumb.top()) if self.orientation() == Qt.Vertical else (pos.x() - thumb.left())
            else:
                self._dragging_thumb = True
                self._drag_offset = thumb.height() / 2.0 if self.orientation() == Qt.Vertical else thumb.width() / 2.0
                self.setValue(self._value_from_pos(pos, self._drag_offset))
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        if self._dragging_thumb:
            self.setValue(self._value_from_pos(pos, self._drag_offset))
            event.accept()
            return
        old = self._hover_thumb
        self._hover_thumb = self._thumb_rect().contains(QRectF(pos, QSize(1, 1)).center())
        if old != self._hover_thumb:
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_thumb:
            self._dragging_thumb = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._hover_thumb = False
        self.update()
        super().leaveEvent(event)

class ModernScrollBarStyle(QProxyStyle):
    """Stable iTunes-like scrollbars for every Qt scroll area.

    This avoids QSS scrollbar painting because Qt's stylesheet scrollbar engine
    is easy to override accidentally and may fall back to native square handles
    on some controls.  The style keeps normal scrollbar logic/hit-testing, but
    draws a simple rounded track and rounded thumb with no elastic effects.
    """
    def __init__(self, base=None):
        super().__init__(base)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_ScrollBarExtent:
            return 14
        if metric == QStyle.PM_ScrollBarSliderMin:
            return 42
        return super().pixelMetric(metric, option, widget)

    def subControlRect(self, control, option, subControl, widget=None):
        if control == QStyle.CC_ScrollBar:
            opt = option
            r = QRect(option.rect)
            if subControl in (QStyle.SC_ScrollBarSubLine, QStyle.SC_ScrollBarAddLine):
                return QRect()
            minimum = int(getattr(opt, 'minimum', 0))
            maximum = int(getattr(opt, 'maximum', 0))
            page_step = max(1, int(getattr(opt, 'pageStep', 1)))
            pos = int(getattr(opt, 'sliderPosition', getattr(opt, 'sliderValue', minimum)))
            rng = max(0, maximum - minimum)
            orientation = getattr(
                opt,
                "orientation",
                widget.orientation()
                if isinstance(widget, QScrollBar)
                else (Qt.Vertical if r.height() >= r.width() else Qt.Horizontal),
            )
            if orientation == Qt.Vertical:
                track_len = max(1, r.height())
                if rng <= 0:
                    slider = QRect(r.left(), r.top(), r.width(), r.height())
                else:
                    length = max(self.pixelMetric(QStyle.PM_ScrollBarSliderMin, option, widget), int(track_len * page_step / (rng + page_step)))
                    length = min(track_len, length)
                    travel = max(1, track_len - length)
                    y = r.top() + int(round((pos - minimum) / max(1, rng) * travel))
                    slider = QRect(r.left(), y, r.width(), length)
                if subControl == QStyle.SC_ScrollBarSlider:
                    return slider
                if subControl == QStyle.SC_ScrollBarGroove:
                    return r
                if subControl == QStyle.SC_ScrollBarSubPage:
                    return QRect(r.left(), r.top(), r.width(), max(0, slider.top() - r.top()))
                if subControl == QStyle.SC_ScrollBarAddPage:
                    return QRect(r.left(), slider.bottom() + 1, r.width(), max(0, r.bottom() - slider.bottom()))
            else:
                track_len = max(1, r.width())
                if rng <= 0:
                    slider = QRect(r.left(), r.top(), r.width(), r.height())
                else:
                    length = max(self.pixelMetric(QStyle.PM_ScrollBarSliderMin, option, widget), int(track_len * page_step / (rng + page_step)))
                    length = min(track_len, length)
                    travel = max(1, track_len - length)
                    x = r.left() + int(round((pos - minimum) / max(1, rng) * travel))
                    slider = QRect(x, r.top(), length, r.height())
                if subControl == QStyle.SC_ScrollBarSlider:
                    return slider
                if subControl == QStyle.SC_ScrollBarGroove:
                    return r
                if subControl == QStyle.SC_ScrollBarSubPage:
                    return QRect(r.left(), r.top(), max(0, slider.left() - r.left()), r.height())
                if subControl == QStyle.SC_ScrollBarAddPage:
                    return QRect(slider.right() + 1, r.top(), max(0, r.right() - slider.right()), r.height())
        return super().subControlRect(control, option, subControl, widget)

    def drawComplexControl(self, control, option, painter, widget=None):
        if control == QStyle.CC_ScrollBar:
            opt = option
            r = QRectF(option.rect)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            # Transparent outer area, very soft track only.
            orientation = getattr(
                opt,
                "orientation",
                widget.orientation()
                if isinstance(widget, QScrollBar)
                else (Qt.Vertical if r.height() >= r.width() else Qt.Horizontal),
            )
            if orientation == Qt.Vertical:
                track = r.adjusted(5, 2, -5, -2)
                slider = QRectF(self.subControlRect(control, option, QStyle.SC_ScrollBarSlider, widget)).adjusted(3.5, 3.0, -3.5, -3.0)
            else:
                track = r.adjusted(2, 5, -2, -5)
                slider = QRectF(self.subControlRect(control, option, QStyle.SC_ScrollBarSlider, widget)).adjusted(3.0, 3.5, -3.0, -3.5)
            if track.width() > 0 and track.height() > 0:
                painter.setBrush(QColor(170, 181, 194, 42))
                radius = min(track.width(), track.height()) / 2.0
                painter.drawRoundedRect(track, radius, radius)
            if slider.width() > 1 and slider.height() > 1:
                active = bool(option.state & QStyle.State_Sunken)
                hover = bool(option.state & QStyle.State_MouseOver)
                color = QColor('#8F9BAA') if active else (QColor('#9AA7B6') if hover else QColor('#A9B5C3'))
                painter.setBrush(color)
                radius = min(slider.width(), slider.height()) / 2.0
                painter.drawRoundedRect(slider, radius, radius)
            painter.restore()
            return
        return super().drawComplexControl(control, option, painter, widget)


def prepare_scroll_area(area: QAbstractScrollArea | None):
    """Install the same rounded, continuous-drag scrollbar on every scroll area."""
    if area is None:
        return
    try:
        area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    except Exception:
        pass
    try:
        area.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    except Exception:
        pass
    try:
        if not isinstance(area.verticalScrollBar(), ModernScrollBar):
            vbar = ModernScrollBar(Qt.Vertical, area)
            area.setVerticalScrollBar(vbar)
        if not isinstance(area.horizontalScrollBar(), ModernScrollBar):
            hbar = ModernScrollBar(Qt.Horizontal, area)
            area.setHorizontalScrollBar(hbar)
        area.verticalScrollBar().setSingleStep(18)
        area.horizontalScrollBar().setSingleStep(18)
        area.verticalScrollBar().setPageStep(max(120, area.viewport().height() - 40))
        area.horizontalScrollBar().setPageStep(max(120, area.viewport().width() - 40))
    except Exception:
        pass
    try:
        area.setStyleSheet(area.styleSheet() + "\nQScrollBar { background: transparent; border: none; }\n")
    except Exception:
        pass


def _ensure_resize_freeze_overlay(window: QWidget):
    try:
        overlay = getattr(window, '_resize_freeze_overlay', None)
        if overlay is None:
            overlay = ResizeFreezeOverlay(window)
            window._resize_freeze_overlay = overlay
        if overlay.parentWidget() is not window:
            overlay.setParent(window)
        return overlay
    except Exception:
        return None


def _resize_freeze_inner_inset(window: QWidget) -> int:
    # Keep the frozen bitmap inside the live-painted shell border.
    return 2


def _resize_freeze_shell(window: QWidget):
    return getattr(window, 'window_shell', None) or getattr(window, 'detail_shell', None)


def _resize_freeze_snapshot_geometry(window: QWidget):
    """Return (shell, shell_rect_in_window, inner_rect_in_shell, snapshot_pos_in_window).

    The snapshot must be anchored to the window's local top-left, not to a live
    shell geometry that may lag or bounce while the left/top edge is being dragged.
    Shell margins are currently zero, but keep the calculation explicit so the
    freeze layer remains correct if margins return later.
    """
    shell = _resize_freeze_shell(window)
    if shell is None:
        return None, QRect(window.rect()), window.rect(), QPoint(0, 0)
    try:
        m = max(0, int(getattr(window, '_window_normal_margin', getattr(window, '_detail_normal_margin', 0)) or 0))
    except Exception:
        m = 0
    shell_rect = QRect(window.rect()).adjusted(m, m, -m, -m)
    inset = _resize_freeze_inner_inset(window)
    inner_rect = shell.rect().adjusted(inset, inset, -inset, -inset)
    if inner_rect.width() < 1 or inner_rect.height() < 1:
        inner_rect = shell.rect()
        inset = 0
    snapshot_pos = shell_rect.topLeft() + QPoint(inset, inset)
    return shell, shell_rect, inner_rect, snapshot_pos


def _update_resize_freeze_overlay(window: QWidget):
    try:
        overlay = getattr(window, '_resize_freeze_overlay', None)
        if overlay is not None and overlay.isVisible():
            overlay.setGeometry(window.rect())
            # Keep only the current top-level size.  Do not sample shell.geometry()
            # during live resize: the shell is intentionally update-disabled and
            # can lag by one frame on left/top drags, which caused corner/border
            # jitter on the opposite side.
            try:
                m = max(0, int(getattr(overlay, '_fixed_shell_margin', 0)))
            except Exception:
                m = 0
            overlay._shell_rect = QRect(window.rect()).adjusted(m, m, -m, -m)
            overlay.raise_()
            overlay.update()
    except Exception:
        pass


def begin_window_live_resize(window: QWidget, edge: str = ""):
    """Show a stable full-window cover while Windows performs live resize.

    The snapshot is captured once, ideally from WM_ENTERSIZEMOVE before the first
    geometry change.  It is then anchored to the window's local top-left.  When
    dragging the left/top border, the HWND moves on screen, and this child cover
    moves with it, so there is no edge-dependent compensation and no opposite-side
    twitching.
    """
    try:
        timer = getattr(window, '_live_resize_fallback_timer', None)
        if timer is None:
            timer = QTimer(window)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda w=window: finish_window_live_resize(w, 28))
            window._live_resize_fallback_timer = timer
        timer.start(520)

        if getattr(window, '_live_resizing', False):
            _update_resize_freeze_overlay(window)
            return

        window._live_resizing = True
        window._active_resize_edge = edge or getattr(window, '_active_resize_edge', '') or ''
        shell, shell_rect, inner_rect, snapshot_pos = _resize_freeze_snapshot_geometry(window)
        if shell is None:
            return
        overlay = _ensure_resize_freeze_overlay(window)
        if overlay is None:
            return
        try:
            anim = getattr(window, '_resize_freeze_anim', None)
            if anim is not None:
                anim.stop()
        except Exception:
            pass
        try:
            fade_timer = getattr(window, '_resize_freeze_fade_timer', None)
            if fade_timer is not None:
                fade_timer.stop()
        except Exception:
            pass
        try:
            overlay.setGraphicsEffect(None)
        except Exception:
            pass
        overlay.hide()
        # Capture only the interior content, never the outer border.
        pix = shell.grab(inner_rect)
        overlay.setParent(window)
        overlay.setGeometry(window.rect())
        overlay.set_snapshot(pix, snapshot_pos, shell_rect, 28)
        maybe_update_live_resize_window_mask(window, 28, interval_ms=0)
        overlay.show()
        overlay.raise_()
        # Paint the live-resize cover immediately before disabling the real
        # shell.  This prevents a one-frame gap where the shell has stopped
        # updating but the overlay border has not been drawn yet.
        try:
            overlay.repaint()
        except Exception:
            pass
        # Stop live child repaint/layout artifacts leaking above the cover.
        try:
            shell.setUpdatesEnabled(False)
        except Exception:
            pass
    except Exception:
        pass


def finish_window_live_resize(window: QWidget, radius: int = 28):
    """Return from frozen bitmap to the newly laid-out live UI."""
    try:
        window._live_resizing = False
        window._active_resize_edge = ''
        try:
            timer = getattr(window, '_live_resize_fallback_timer', None)
            if timer is not None:
                timer.stop()
        except Exception:
            pass
        shell = _resize_freeze_shell(window)
        if shell is not None:
            try:
                shell.setUpdatesEnabled(True)
                shell.update()
            except Exception:
                pass
        try:
            if hasattr(window, 'update_table_column_layout'):
                window.update_table_column_layout()
        except Exception:
            pass
        apply_opaque_rounded_window_mask(window, radius)
        overlay = getattr(window, '_resize_freeze_overlay', None)
        if overlay is not None and overlay.isVisible():
            _update_resize_freeze_overlay(window)
            try:
                overlay.setGraphicsEffect(None)
                overlay.set_snapshot_opacity(1.0)
                overlay.raise_()
                overlay.repaint()
            except Exception:
                pass

            # Do NOT fade the whole overlay with QGraphicsOpacityEffect.  That
            # fades the border too, which is exactly what made the outline
            # briefly vanish when resizing from the opposite edge.  Fade only
            # the frozen inner bitmap; keep the live-painted border fully opaque
            # until the real shell has repainted underneath, then hide the cover.
            try:
                old_timer = getattr(window, '_resize_freeze_fade_timer', None)
                if old_timer is not None:
                    old_timer.stop()
            except Exception:
                pass
            fade_timer = QTimer(window)
            fade_timer.setInterval(12)
            fade_timer._step = 0
            def _fade_tick(w=window, ov=overlay, t=fade_timer):
                try:
                    t._step += 1
                    p = min(1.0, t._step / 14.0)
                    ov.set_snapshot_opacity(1.0 - ease_out_cubic(p))
                    ov.raise_()
                    if p >= 1.0:
                        t.stop()
                        try:
                            sh = _resize_freeze_shell(w)
                            if sh is not None:
                                sh.repaint()
                        except Exception:
                            pass
                        ov.hide()
                        ov.set_snapshot_opacity(1.0)
                except Exception:
                    try:
                        t.stop()
                        ov.hide()
                    except Exception:
                        pass
            fade_timer.timeout.connect(_fade_tick)
            window._resize_freeze_fade_timer = fade_timer
            fade_timer.start()
    except Exception:
        pass


def schedule_rounded_mask_update(window: QWidget, radius: int = 28, delay_ms: int = 90):
    """Debounce top-level mask changes to avoid live-resize content jitter."""
    try:
        if window.isMaximized() or window.isFullScreen():
            window.clearMask()
            return
        if getattr(window, '_live_resizing', False):
            return
        timer = getattr(window, '_rounded_mask_timer', None)
        if timer is None:
            timer = QTimer(window)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda w=window, r=radius: apply_opaque_rounded_window_mask(w, r))
            window._rounded_mask_timer = timer
        timer.start(delay_ms)
    except Exception:
        pass


class WindowControlButton(QPushButton):
    """macOS traffic-light window control with native Windows behavior."""
    def __init__(self, control: str, parent=None):
        super().__init__("", parent)
        self.control = control
        self._restore = False
        self.visual_style = "macos"
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setFlat(True)
        self.set_visual_style("macos")

    def set_visual_style(self, style: str):
        allowed = {"macos", "windows", "win11", "win7", "win2000", "macos8"}
        self.visual_style = style if style in allowed else "macos"
        if self.visual_style in {"windows", "win11"}:
            self.setFixedSize(42, 28)
            size_rule = "min-width:42px;max-width:42px;min-height:28px;max-height:28px;"
        elif self.visual_style == "win7":
            self.setFixedSize(46, 30)
            size_rule = "min-width:46px;max-width:46px;min-height:30px;max-height:30px;"
        elif self.visual_style == "win2000":
            self.setFixedSize(24, 20)
            size_rule = "min-width:24px;max-width:24px;min-height:20px;max-height:20px;"
        elif self.visual_style == "macos8":
            self.setFixedSize(18, 18)
            size_rule = "min-width:18px;max-width:18px;min-height:18px;max-height:18px;"
        else:
            self.setFixedSize(20, 20)
            size_rule = "min-width:20px;max-width:20px;min-height:20px;max-height:20px;"
        self.setStyleSheet(f"QPushButton {{ background:transparent;border:none;padding:0;margin:0;{size_rule} }}")
        self.update()

    def set_restore(self, restore: bool):
        self._restore = bool(restore)
        self.update()

    def setText(self, text: str):
        # Backwards-compatible with existing toggle code; draw state is kept in
        # _restore instead of relying on blurry font glyphs like □ / ❐.
        if text == "❐":
            self.set_restore(True)
        elif text == "□":
            self.set_restore(False)
        super().setText("")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        down = self.isDown()
        hover = self.underMouse()
        if self.visual_style in {"windows", "win11", "win7"}:
            if hover or down:
                if self.visual_style == "win7":
                    top = "#EE9C91" if self.control == "close" else "#EAF6FD"
                    bottom = "#C6473A" if self.control == "close" else "#B9DDF3"
                    gradient = QLinearGradient(0, 0, 0, self.height())
                    gradient.setColorAt(0, QColor(top))
                    gradient.setColorAt(1, QColor(bottom))
                    painter.fillRect(self.rect(), gradient)
                else:
                    color = "#C42B1C" if self.control == "close" else ("#D8D8DC" if down else "#E5E5EA")
                    painter.fillRect(self.rect(), QColor(color))
            pen_color = QColor("#FFFFFF" if self.control == "close" and (hover or down) else "#3A3A3C")
            pen = QPen(pen_color, 1.15)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            cx, cy = self.width() / 2.0, self.height() / 2.0
            if self.control == "min":
                painter.drawLine(QPointF(cx - 5, cy + 3), QPointF(cx + 5, cy + 3))
            elif self.control == "max":
                if self._restore:
                    painter.drawRect(QRectF(cx - 5, cy - 3, 8, 7))
                    painter.drawRect(QRectF(cx - 2, cy - 6, 8, 7))
                else:
                    painter.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
            else:
                painter.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx + 4, cy + 4))
                painter.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx - 4, cy + 4))
            return
        if self.visual_style in {"win2000", "macos8"}:
            fill = QColor("#D4D0C8" if self.visual_style == "win2000" else "#DDDDDD")
            painter.fillRect(self.rect(), fill)
            edge = self.rect().adjusted(0, 0, -1, -1)
            light = QColor("#FFFFFF" if not down else "#333333")
            dark = QColor("#333333" if not down else "#FFFFFF")
            painter.setPen(QPen(light, 1))
            painter.drawLine(edge.topLeft(), edge.topRight())
            painter.drawLine(edge.topLeft(), edge.bottomLeft())
            painter.setPen(QPen(dark, 1))
            painter.drawLine(edge.bottomLeft(), edge.bottomRight())
            painter.drawLine(edge.topRight(), edge.bottomRight())
            painter.setPen(QPen(QColor("#000000"), 1))
            cx, cy = self.width() / 2.0, self.height() / 2.0
            if self.control == "min":
                painter.drawLine(QPointF(cx - 4, cy + 3), QPointF(cx + 4, cy + 3))
            elif self.control == "max":
                painter.drawRect(QRectF(cx - 4, cy - 4, 8, 8))
            else:
                painter.drawLine(QPointF(cx - 3, cy - 3), QPointF(cx + 3, cy + 3))
                painter.drawLine(QPointF(cx + 3, cy - 3), QPointF(cx - 3, cy + 3))
            return
        colors = {
            "close": ("#FF5F57", "#E0443E"),
            "min": ("#FEBB2E", "#D89B16"),
            "max": ("#28C840", "#1FA834"),
        }
        base, pressed = colors.get(self.control, ("#C7C7CC", "#AEAEB2"))
        circle = QRectF(3.0, 3.0, 14.0, 14.0)
        painter.setPen(QPen(QColor(0, 0, 0, 34), 0.8))
        painter.setBrush(QColor(pressed if down else base))
        painter.drawEllipse(circle)
        if not hover and not down:
            return

        icon = QColor(70, 45, 38, 205)
        pen = QPen(icon, 1.15)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = circle.center().x()
        cy = circle.center().y()
        if self.control == "min":
            painter.drawLine(QPointF(cx - 3.2, cy), QPointF(cx + 3.2, cy))
        elif self.control == "max":
            if self._restore:
                painter.drawRect(QRectF(cx - 3.1, cy - 1.8, 4.8, 4.8))
                painter.drawRect(QRectF(cx - 1.5, cy - 3.4, 4.8, 4.8))
            else:
                painter.drawLine(QPointF(cx - 2.8, cy + 2.8), QPointF(cx + 2.8, cy - 2.8))
                painter.drawLine(QPointF(cx + 0.4, cy - 2.8), QPointF(cx + 2.8, cy - 2.8))
                painter.drawLine(QPointF(cx + 2.8, cy - 2.8), QPointF(cx + 2.8, cy - 0.4))
        else:
            painter.drawLine(QPointF(cx - 2.7, cy - 2.7), QPointF(cx + 2.7, cy + 2.7))
            painter.drawLine(QPointF(cx + 2.7, cy - 2.7), QPointF(cx - 2.7, cy + 2.7))


class FramelessTitleBar(QWidget):
    """Built-in frameless title bar.

    theme="dark" is used by the detail viewer; theme="light" is used by the main window.
    The rounded buttons use a large radius to approximate a continuous/squircle-like corner.
    """
    def __init__(self, dialog: QDialog, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self.dialog = dialog
        self.theme = theme
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self.setObjectName("MainTitleBar" if theme in ("light", "detail_light") else "DetailTitleBar")
        self.setFixedHeight(44)

        self._title_layout = QHBoxLayout(self)
        self._title_layout.setContentsMargins(14, 8, 14, 8)
        self._title_layout.setSpacing(6)

        self.title_label = QLabel("图片细节")
        apply_smooth_font(self.title_label, 10, bold=True)
        self.title_label.setAlignment(Qt.AlignCenter)
        if theme in ("light", "detail_light"):
            self.title_label.setStyleSheet("QLabel { color: #1D1D1F; background: transparent; letter-spacing: 0.2px; }")
        else:
            self.title_label.setStyleSheet("QLabel { color: #F2F2F7; background: transparent; }")
        self.btn_min = WindowControlButton("min", self)
        self.btn_max = WindowControlButton("max", self)
        self.btn_close = WindowControlButton("close", self)
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            # Native QToolTip can show a square backing surface on translucent
            # frameless windows.  These controls are self-explanatory, so avoid
            # that dirty popup entirely.
            btn.setToolTip("")
        self.set_control_style(TITLEBAR_STYLE)

        self.btn_min.clicked.connect(dialog.showMinimized)
        self.btn_max.clicked.connect(self.toggle_max_restore)
        self.btn_close.clicked.connect(dialog.close)
        self.setStyleSheet(
            "#MainTitleBar { background: #F7F7F8; border-bottom: 1px solid #D9D9DE; }"
            "#DetailTitleBar { background: #F7F7F8; border-bottom: 1px solid #D9D9DE; }"
        )

    def _button_style(self, bg: str, hover: str, color: str = "#F2F2F7", border: str = "transparent", pressed: str = "#DDE4EF", pressed_text: str | None = None) -> str:
        ptext = pressed_text or color
        return (
            f"QPushButton {{ color: {color}; background: {bg}; border: 1px solid {border}; border-radius: 14px; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QPushButton:pressed {{ background: {pressed}; color: {ptext}; }}"
        )

    def set_title(self, title: str):
        self.title_label.setText(title)

    def apply_theme(self, profile):
        self._theme_profile = profile
        style = profile.titlebar_skin
        height = 44
        if style == "win7":
            height = 36
            background = "qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #EAF5FC,stop:1 #B8D7EA)"
            title_color = "#1E1E1E"
        elif style == "win2000":
            height = 28
            background = "#0A246A"
            title_color = "#FFFFFF"
        elif style == "macos8":
            height = 28
            background = "transparent"
            title_color = "#000000"
        elif style == "win11":
            background = profile.app_bg
            title_color = profile.text
        else:
            background = profile.app_bg
            title_color = profile.text
        self.setFixedHeight(height)
        title_size = theme_display_point_size(
            RUNTIME_THEME_STYLE, RUNTIME_THEME_LOCALE, 9 if profile.is_flavor else 10
        )
        self.title_label.setFont(make_theme_font(RUNTIME_THEME_STYLE, RUNTIME_THEME_LOCALE, title_size, bold=True))
        label_background = profile.app_bg if style == "macos8" else "transparent"
        self.title_label.setStyleSheet(
            f"QLabel {{ color: {title_color}; background: {label_background}; padding: 0 8px; }}"
        )
        self.setStyleSheet(
            f"#{self.objectName()} {{ background: {background}; border-bottom: 1px solid {profile.border}; }}"
        )
        self.update()

    def set_control_style(self, style: str):
        allowed = {"macos", "windows", "win11", "win7", "win2000", "macos8"}
        style = style if style in allowed else "macos"
        layout = self._title_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
        for button in (self.btn_close, self.btn_min, self.btn_max):
            button.set_visual_style(style)
        if style in {"windows", "win11", "win7", "win2000"}:
            if style == "win2000":
                layout.setContentsMargins(6, 4, 4, 4)
            elif style == "win7":
                layout.setContentsMargins(10, 3, 0, 3)
            else:
                layout.setContentsMargins(14, 8, 0, 8)
            layout.addSpacing(92 if style in {"win2000", "win7"} else 126)
            layout.addWidget(self.title_label, 1)
            layout.addWidget(self.btn_min)
            layout.addWidget(self.btn_max)
            layout.addWidget(self.btn_close)
        elif style == "macos8":
            layout.setContentsMargins(6, 4, 6, 4)
            layout.addWidget(self.btn_close)
            layout.addSpacing(6)
            layout.addWidget(self.title_label, 1)
            layout.addSpacing(6)
            layout.addWidget(self.btn_min)
            layout.addWidget(self.btn_max)
        else:
            layout.setContentsMargins(14, 8, 14, 8)
            layout.addWidget(self.btn_close)
            layout.addWidget(self.btn_min)
            layout.addWidget(self.btn_max)
            layout.addSpacing(10)
            layout.addWidget(self.title_label, 1)
            layout.addSpacing(82)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event):
        profile = getattr(self, "_theme_profile", globals().get("RUNTIME_THEME_PROFILE"))
        style = getattr(profile, "titlebar_skin", "apple")
        painter = QPainter(self)
        if style == "win7":
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0.0, QColor("#EAF5FC"))
            gradient.setColorAt(1.0, QColor("#B8D7EA"))
            painter.fillRect(self.rect(), gradient)
            painter.setPen(QPen(QColor(profile.border), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        if style == "win2000":
            painter.fillRect(self.rect(), QColor("#0A246A"))
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        if style != "macos8":
            painter.fillRect(self.rect(), QColor(profile.app_bg))
            painter.setPen(QPen(QColor(profile.border), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        painter.fillRect(self.rect(), QColor("#DDDDDD"))
        painter.setPen(QPen(QColor("#777777"), 1))
        for y in range(5, max(6, self.height() - 4), 3):
            painter.drawLine(2, y, self.width() - 3, y)
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.drawLine(0, 0, self.width(), 0)
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def toggle_max_restore(self):
        if self.dialog.isMaximized():
            self.dialog.showNormal()
            try:
                self.btn_max.set_restore(False)
            except Exception:
                self.btn_max.setText("□")
        else:
            self.dialog.showMaximized()
            try:
                self.btn_max.set_restore(True)
            except Exception:
                self.btn_max.setText("❐")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            try:
                global_pos = event.globalPosition().toPoint()
            except Exception:
                global_pos = event.globalPos()
            self._drag_offset = global_pos - self.dialog.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            try:
                global_pos = event.globalPosition().toPoint()
            except Exception:
                global_pos = event.globalPos()
            if self.dialog.isMaximized():
                # Restore before moving; keep the cursor near the same horizontal ratio.
                old_w = max(1, self.dialog.width())
                ratio = max(0.05, min(0.95, event.position().x() / old_w)) if hasattr(event, 'position') else 0.5
                self.dialog.showNormal()
                self.btn_max.setText("□")
                self._drag_offset = QPoint(int(self.dialog.width() * ratio), 18)
            self.dialog.move(global_pos - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)


class ImageDetailDialog(QDialog):
    def __init__(
        self,
        start_item_id: str,
        ordered_ids: list[str],
        item_map: dict[str, PhotoItemData],
        fallback_pixmaps: dict[str, QPixmap] | None = None,
        parent=None,
        trash_context: bool = False,
    ):
        super().__init__(parent)
        self.ordered_ids = [iid for iid in ordered_ids if iid in item_map]
        self.item_map = item_map
        self.fallback_pixmaps = fallback_pixmaps or {}
        # Capture the view context at the moment this detail window is opened.
        # Multiple preview windows may remain open while the main window switches
        # between normal/trash filters; delete semantics must not follow the
        # owner's later filter change, otherwise a normal preview could suddenly
        # become a trash-preview delete operation.
        self.trash_context = bool(trash_context)
        self.current_pos = self.ordered_ids.index(start_item_id) if start_item_id in self.ordered_ids else 0
        self.pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._current_pixmap = QPixmap()
        self._current_source_pixmap = QPixmap()
        self._rotation_degrees_by_item: dict[str, int] = {}
        self._load_generation = 0
        self._closing = False
        owner = parent
        self._owns_detail_executor = False
        self._owns_detail_live_executor = False
        self._detail_executor = getattr(owner, "detail_executor", None)
        if self._detail_executor is None:
            self._detail_executor = AppThreadPoolExecutor(max_workers=1)
            self._owns_detail_executor = True
        self._detail_live_executor = getattr(owner, "detail_live_executor", None)
        if self._detail_live_executor is None:
            self._detail_live_executor = AppThreadPoolExecutor(max_workers=2)
            self._owns_detail_live_executor = True
        self._detail_signals = DetailLoadSignals(self)
        self._detail_signals.detail_ready.connect(self.on_detail_loaded)
        self._detail_signals.detail_live_ready.connect(self.on_detail_live_loaded)
        self._still_pixmap_cache: dict[str, QPixmap] = {}
        self._detail_live_cache: dict[str, list[QPixmap]] = {}
        self._detail_live_quality: dict[str, str] = {}
        self._detail_live_fast_requested: set[str] = set()
        self._detail_live_hq_requested: set[str] = set()
        self._detail_live_failed: set[str] = set()
        self._detail_live_timer = QTimer(self)
        self._detail_live_timer.setInterval(DETAIL_LIVE_PLAYBACK_INTERVAL_MS)
        try:
            self._detail_live_timer.setTimerType(Qt.PreciseTimer)
        except Exception:
            pass
        self._detail_live_timer.timeout.connect(self.advance_detail_live_frame)
        self._detail_live_frame_index = 0
        self._detail_live_scene_target: dict[str, tuple[int, int]] = {}
        self._detail_still_size_cache: dict[str, tuple[int, int]] = {}
        # When navigating with LIVE already enabled, the first LIVE frame must fit
        # the new photo once. Later LIVE frames must preserve zoom/pan.
        self._pending_live_fit_generation: Optional[int] = None
        self._manual_resizing = False
        self._manual_resize_edge = None
        self._manual_resize_start_global = QPoint(0, 0)
        self._manual_resize_start_geom = None
        self._live_resizing = False
        self._live_resize_settle_timer = None

        self.setWindowTitle("图片细节")
        # Use a borderless custom window so the viewer has an integrated title bar
        # and the three window controls are visually consistent with the dark UI.
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        # Opaque top-level for stable live-resize on Windows.  See
        # PhotoMoverQt.build_ui() for the layered-window explanation.
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        try:
            pal = self.palette()
            pal.setColor(QPalette.Window, QColor(APP_BG))
            self.setPalette(pal)
        except Exception:
            pass
        self.resize(1120, 800)
        self.setStyleSheet("QDialog { background: %s; }" % APP_BG)

        layout = QVBoxLayout(self)
        self._detail_outer_layout = layout
        self._detail_normal_margin = DETAIL_SHADOW_MARGIN
        layout.setContentsMargins(DETAIL_SHADOW_MARGIN, DETAIL_SHADOW_MARGIN, DETAIL_SHADOW_MARGIN, DETAIL_SHADOW_MARGIN)
        layout.setSpacing(0)

        self.detail_shell = L2Panel(self, fill=APP_BG, border=APP_BORDER, radius_hint=28)
        self.detail_shell.setObjectName("DetailShell")
        self.detail_shell.setAttribute(Qt.WA_StyledBackground, False)
        # See PhotoMoverQt.build_ui(): avoid QGraphicsDropShadowEffect on a
        # child that owns many descendants.  The dialog paints its own stable
        # shadow in paintEvent() instead.
        self._detail_shadow = None
        self.detail_shell.setGraphicsEffect(None)
        layout.addWidget(self.detail_shell, 1)
        shell_layout = QVBoxLayout(self.detail_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.title_bar = FramelessTitleBar(self, self, theme="detail_light")
        shell_layout.addWidget(self.title_bar)

        body = QWidget(self)
        body.setObjectName("DetailBody")
        body.setAttribute(Qt.WA_StyledBackground, False)
        body.setStyleSheet("#DetailBody { background: transparent; }")
        shell_layout.addWidget(body, 1)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(8)

        top = QHBoxLayout()
        body_layout.addLayout(top)

        self.btn_prev = L2Button("‹", self, variant="nav")
        self.btn_next = L2Button("›", self, variant="nav")
        self.btn_prev.setToolTip("上一张（← / A）")
        self.btn_next.setToolTip("下一张（→ / D / 空格）")
        self.btn_fit = L2Button("适应窗口", self, variant="detail")
        self.btn_100 = L2Button("原始大小", self, variant="detail")
        self.btn_100.setToolTip("按原始像素比例显示，即 100%。旁边的“缩放：xx%”才是当前实际缩放比例。")
        self.btn_rotate_left = L2Button("左转90°", self, variant="detail")
        self.btn_rotate_left.setToolTip("仅旋转当前预览显示，不修改原始照片。快捷键：Ctrl+L")
        self.btn_rotate_right = L2Button("右转90°", self, variant="detail")
        self.btn_rotate_right.setToolTip("仅旋转当前预览显示，不修改原始照片。快捷键：Ctrl+R")
        self.live_toggle = L2Button("播放实况", self, variant="live_off")
        self.live_toggle.setCheckable(True)
        self.btn_delete = L2Button("删除", self, variant="detail")
        self.btn_delete.setToolTip("当前普通视图下会先标记进垃圾箱；垃圾箱视图下会移动到程序目录的“已删除”文件夹。")
        for btn in (self.btn_fit, self.btn_100, self.btn_rotate_left, self.btn_rotate_right, self.live_toggle, self.btn_delete):
            apply_smooth_font(btn, 10, bold=True)
            btn.setMinimumHeight(42)
            btn.set_outer_fill(APP_BG)
            btn.set_radius_hint(18)
        self.btn_fit.setMinimumWidth(104)
        self.btn_100.setMinimumWidth(104)
        self.btn_rotate_left.setMinimumWidth(104)
        self.btn_rotate_right.setMinimumWidth(104)
        self.live_toggle.setMinimumWidth(116)
        self.btn_delete.setMinimumWidth(84)
        self.update_live_button_style(False, enabled=True)
        for btn in (self.btn_prev, self.btn_next):
            apply_smooth_font(btn, 26, bold=True)
            btn.setMinimumWidth(54)
            btn.setMaximumWidth(66)
            btn.set_outer_fill(APP_BG)
            btn.set_radius_hint(22)
            try:
                btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            except Exception:
                pass

        self.info_label = L2Label("", self, fill="#E7ECF2", border="#C0CAD8", radius_hint=18, padding=(10, 7, 10, 7))
        self.info_label.setStyleSheet("QLabel { color: #20242B; background: transparent; border: none; padding: 0px; }")
        apply_smooth_font(self.info_label, 10, bold=True)
        self.info_label.setMinimumWidth(320)
        self.info_label.setMinimumHeight(42)

        self.index_label = L2Label("", self, fill="#2C7BEA", border="#2C7BEA", radius_hint=18, padding=(10, 7, 10, 7))
        self.index_label.setStyleSheet("QLabel { color: #FFFFFF; background: transparent; border: none; padding: 0px; }")
        apply_smooth_font(self.index_label, 10, bold=True)
        self.index_label.setAlignment(Qt.AlignCenter)
        self.index_label.setMinimumHeight(42)

        self.zoom_label = L2Label("缩放：--", self, fill="#E7ECF2", border="#C0CAD8", radius_hint=18, padding=(10, 7, 10, 7))
        self.zoom_label.setStyleSheet("QLabel { color: #20242B; background: transparent; border: none; padding: 0px; }")
        apply_smooth_font(self.zoom_label, 10, bold=True)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(96)
        self.zoom_label.setMinimumHeight(42)

        top.addWidget(self.btn_fit)
        top.addWidget(self.btn_100)
        top.addWidget(self.btn_rotate_left)
        top.addWidget(self.btn_rotate_right)
        top.addWidget(self.live_toggle)
        top.addWidget(self.btn_delete)
        top.addWidget(self.index_label)
        top.addWidget(self.zoom_label)
        top.addWidget(self.info_label, 1)

        self.view = DetailGraphicsView(self)
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(8)
        view_row.addWidget(self.btn_prev)
        view_row.addWidget(self.view, 1)
        view_row.addWidget(self.btn_next)
        body_layout.addLayout(view_row, 1)
        self.scene = QGraphicsScene(self.view)
        self.scene.setBackgroundBrush(QColor(DETAIL_VIEW_BG))
        self.view.setScene(self.scene)
        self.view.setBackgroundBrush(QColor(DETAIL_VIEW_BG))
        self.view.viewport().setAutoFillBackground(True)
        self.view.zoom_changed.connect(self.on_zoom_changed)

        # Hidden child resize handles removed; native WM_NCHITTEST owns resizing.

        hint = L2Label("滚轮缩放｜按住左键拖拽平移｜双击适应窗口｜Ctrl+L/R 旋转｜←/→ 前后翻页｜Esc 关闭", self, fill="#E7ECF2", border="#C0CAD8", radius_hint=16, padding=(10, 6, 10, 6))
        hint.setStyleSheet("QLabel { color: #6B7280; background: transparent; border: none; padding: 0px; }")
        apply_smooth_font(hint, 9)
        body_layout.addWidget(hint)

        self.btn_prev.clicked.connect(self.show_previous)
        self.btn_next.clicked.connect(self.show_next)
        self.btn_fit.clicked.connect(self.view.reset_zoom_fit)
        self.btn_100.clicked.connect(self.view.set_zoom_100)
        self.btn_rotate_left.clicked.connect(lambda: self.rotate_current_item(-90))
        self.btn_rotate_right.clicked.connect(lambda: self.rotate_current_item(90))
        self.btn_delete.clicked.connect(self.delete_current_item)
        self.live_toggle.toggled.connect(self.on_detail_live_toggled)

        # Keep ←/→ reserved for photo navigation. Some focused child widgets
        # such as QGraphicsView / QPushButton may otherwise consume arrow keys
        # for scrolling or focus movement before the dialog sees them.
        for w in (self, self.view, self.view.viewport(), self.btn_prev, self.btn_next, self.btn_fit, self.btn_100, self.btn_rotate_left, self.btn_rotate_right, self.live_toggle, self.btn_delete):
            try:
                w.installEventFilter(self)
                w.setFocusPolicy(Qt.NoFocus)
            except Exception:
                pass
        try:
            self.view.setFocusPolicy(Qt.StrongFocus)
            self.view.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

        # Defer the first image load until after show().  Opening a non-modal
        # preview should create and display the window first, then populate the
        # scene from thumbnail/full image on the next event-loop turn.
        self.info_label.setText("正在准备图片预览…")
        self.index_label.setText("第 -- / --")
        QTimer.singleShot(0, lambda: (not getattr(self, "_closing", False)) and self.load_current_item(fit=True))

    def showEvent(self, event):
        super().showEvent(event)
        install_frameless_window_native_features(self)
        enable_native_rounded_corners_if_available(self)
        apply_opaque_rounded_window_mask(self, 28)

    def current_item(self) -> Optional[PhotoItemData]:
        if not self.ordered_ids:
            return None
        self.current_pos = max(0, min(self.current_pos, len(self.ordered_ids) - 1))
        return self.item_map.get(self.ordered_ids[self.current_pos])

    def owner_is_trash_view(self) -> bool:
        return bool(getattr(self, "trash_context", False))

    def _remove_ids_from_order(self, item_ids: set[str] | list[str], reason: str = ""):
        """Synchronize this independent preview window with owner-side mutations.

        Detail windows are intentionally non-modal and may be open in parallel.
        When another window moves an item to the program trash, restores it from
        trash, moves it to another folder, or sends it to the app-local deleted
        folder, this viewer must drop that item from its local navigation list.
        The method keeps the current photo stable when possible and closes the
        window only when no valid item remains.
        """
        if getattr(self, "_closing", False):
            return
        ids = {str(iid) for iid in (item_ids or []) if iid}
        if not ids or not self.ordered_ids:
            return
        old_current_id = self.ordered_ids[self.current_pos] if 0 <= self.current_pos < len(self.ordered_ids) else None
        old_pos = self.current_pos
        new_ids = [iid for iid in self.ordered_ids if iid not in ids]
        if new_ids == self.ordered_ids:
            return
        self.ordered_ids = new_ids
        self._load_generation += 1
        try:
            self._detail_live_timer.stop()
        except Exception:
            pass
        if not self.ordered_ids:
            # Avoid deleting the dialog synchronously while an owner-side
            # operation is still unwinding through this window's slot.
            QTimer.singleShot(0, self.close)
            return
        if old_current_id and old_current_id in self.ordered_ids:
            self.current_pos = self.ordered_ids.index(old_current_id)
            try:
                self.index_label.setText(f"第 {self.current_pos + 1} / {len(self.ordered_ids)}")
                self.btn_prev.setEnabled(self.current_pos > 0)
                self.btn_next.setEnabled(self.current_pos < len(self.ordered_ids) - 1)
            except Exception:
                pass
        else:
            self.current_pos = min(old_pos, len(self.ordered_ids) - 1)
            self.load_current_item(fit=True)

    def on_owner_items_removed(self, item_ids: set[str] | list[str]):
        # Real file moves / moves to the app-local “已删除” folder remove items
        # from every open preview, regardless of the filter context.
        self._remove_ids_from_order(item_ids, reason="removed")

    def on_owner_items_trashed(self, item_ids: set[str] | list[str]):
        # A normal-context preview should no longer navigate to items that were
        # just moved into the program trash. A trash-context preview is left alone.
        if not self.owner_is_trash_view():
            self._remove_ids_from_order(item_ids, reason="trashed")

    def on_owner_items_restored(self, item_ids: set[str] | list[str]):
        # Conversely, a trash-context preview should drop restored items because
        # they no longer belong to the trash view it was opened from.
        if self.owner_is_trash_view():
            self._remove_ids_from_order(item_ids, reason="restored")

    def on_owner_items_updated(self, item_ids: set[str] | list[str]):
        ids = {str(iid) for iid in (item_ids or []) if iid}
        item = self.current_item()
        if item is not None and item.item_id in ids:
            self._detail_live_timer.stop()
            self._detail_live_frame_index = 0
            self._detail_live_cache.pop(item.item_id, None)
            self._detail_live_quality.pop(item.item_id, None)
            self._detail_live_fast_requested.discard(item.item_id)
            self._detail_live_hq_requested.discard(item.item_id)
            self._detail_live_failed.discard(item.item_id)
            self.load_current_item(fit=None)

    def update_delete_button_state(self):
        try:
            if self.owner_is_trash_view():
                self.btn_delete.setText("删除")
                self.btn_delete.setToolTip("从垃圾箱删除：文件会移动到程序目录下的“已删除”文件夹，可手动找回。")
            else:
                self.btn_delete.setText("删除")
                self.btn_delete.setToolTip("普通视图删除：仅标记进程序垃圾箱，不移动原文件。")
        except Exception:
            pass

    def delete_current_item(self):
        item = self.current_item()
        if item is None:
            return
        owner = self.parent()
        if owner is None:
            return
        deleted_or_trashed = False
        try:
            if self.owner_is_trash_view():
                if hasattr(owner, "delete_items_to_deleted_folder_by_ids"):
                    deleted_or_trashed = bool(owner.delete_items_to_deleted_folder_by_ids(
                        [item.item_id], title="确认删除当前图片", show_message=True
                    ))
            else:
                if hasattr(owner, "move_items_to_trash_by_ids"):
                    deleted_or_trashed = bool(owner.move_items_to_trash_by_ids([item.item_id], show_message=True))
        except Exception:
            deleted_or_trashed = False
        if not deleted_or_trashed:
            return

        # The owner normally broadcasts the mutation to all open preview windows.
        # Keep a local fallback so this window still advances correctly if a future
        # owner-side code path forgets to notify.
        removed_id = item.item_id
        if removed_id in self.ordered_ids:
            self._remove_ids_from_order({removed_id}, reason="self_delete")

    def fallback_pixmap_for_item(self, item: PhotoItemData) -> QPixmap:
        pix = self.fallback_pixmaps.get(item.item_id, QPixmap())
        if pix.isNull():
            pix = QPixmap(900, 600)
            pix.fill(QColor("#222226"))
        return pix

    def current_rotation_degrees(self, item: PhotoItemData | None = None) -> int:
        item = item or self.current_item()
        if item is None:
            return 0
        return int(self._rotation_degrees_by_item.get(item.item_id, 0)) % 360

    def rotation_suffix_for_item(self, item: PhotoItemData | None = None) -> str:
        degrees = self.current_rotation_degrees(item)
        return f"  ｜  旋转 {degrees}°" if degrees else ""

    def detail_info_text(self, item: PhotoItemData, suffix: str = "") -> str:
        return (
            f"{item.display_name}  ｜  {item.item_type}  ｜  "
            f"{format_time(item.shot_time)}  ｜  {format_bytes(item.size_bytes)}"
            f"{self.rotation_suffix_for_item(item)}{suffix}"
        )

    def rotated_pixmap_for_current_item(self, pixmap: QPixmap) -> QPixmap:
        if pixmap.isNull():
            return pixmap
        degrees = self.current_rotation_degrees()
        if degrees == 0:
            return pixmap
        try:
            transform = QTransform()
            transform.rotate(degrees)
            return pixmap.transformed(transform, Qt.SmoothTransformation)
        except Exception:
            return pixmap

    def rotated_size_for_current_item(self, size: tuple[int, int]) -> tuple[int, int]:
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        if self.current_rotation_degrees() in (90, 270):
            return height, width
        return width, height

    def rotate_current_item(self, delta_degrees: int):
        item = self.current_item()
        if item is None:
            return
        current = self.current_rotation_degrees(item)
        self._rotation_degrees_by_item[item.item_id] = (current + int(delta_degrees)) % 360
        source = self._current_source_pixmap
        if source.isNull():
            source = self._still_pixmap_cache.get(item.item_id, QPixmap())
        if source.isNull():
            source = self.fallback_pixmap_for_item(item)
        if item.is_live and self.live_toggle.isChecked() and self._detail_live_timer.isActive():
            self.apply_live_pixmap_preserve_view(source, item.item_id)
            QTimer.singleShot(0, self.view.reset_zoom_fit)
        else:
            self.set_scene_pixmap(source, fit=True)
        self.info_label.setText(self.detail_info_text(item))

    def set_scene_pixmap(self, pixmap: QPixmap, fit: Optional[bool] = True):
        """Show a still pixmap.

        fit=True  -> fit to window;
        fit=False -> 100%;
        fit=None  -> keep the current zoom/pan transform.
        """
        self._current_source_pixmap = pixmap
        display_pixmap = self.rotated_pixmap_for_current_item(pixmap)
        self._current_pixmap = display_pixmap
        self.scene.clear()
        self.pixmap_item = QGraphicsPixmapItem(display_pixmap)
        self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self.pixmap_item.setScale(1.0)
        self.scene.addItem(self.pixmap_item)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        if fit is True:
            QTimer.singleShot(0, self.view.reset_zoom_fit)
        elif fit is False:
            self.view.set_zoom_100()

    def apply_live_pixmap_preserve_view(self, pixmap: QPixmap, item_id: str):
        """Replace the scene image with a LIVE frame without changing zoom/pan.

        Frames are decoded/cropped to the same aspect ratio as the still photo,
        then scaled as a graphics item to the original scene rectangle.  This
        avoids the jarring iOS-unlike behavior where LIVE playback resets zoom or
        changes the displayed geometry.
        """
        if pixmap.isNull():
            return
        self._current_source_pixmap = pixmap
        display_pixmap = self.rotated_pixmap_for_current_item(pixmap)
        target = self._detail_live_scene_target.get(item_id)
        if target is None:
            target = (max(1, pixmap.width()), max(1, pixmap.height()))
            self._detail_live_scene_target[item_id] = target
        target_w, target_h = self.rotated_size_for_current_item(target)
        if self.pixmap_item is None:
            self.scene.clear()
            self.pixmap_item = QGraphicsPixmapItem(display_pixmap)
            self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
            self.scene.addItem(self.pixmap_item)
        else:
            self.pixmap_item.setPixmap(display_pixmap)
        scale = target_w / max(1, display_pixmap.width())
        self.pixmap_item.setScale(scale)
        self.scene.setSceneRect(QRectF(0, 0, target_w, target_h))
        self._current_pixmap = display_pixmap

    def current_live_scene_target(self, item: PhotoItemData) -> tuple[int, int]:
        """Return the logical scene size used by still image and LIVE frames.

        Critical rule: never use the thumbnail/fallback pixmap size as the LIVE
        scene target if the full still size can be known.  The fallback is often
        only 118 px wide, and using it while navigating with LIVE playback enabled
        causes the next photo's scene geometry/zoom to become wrong.
        """
        still = self._still_pixmap_cache.get(item.item_id)
        if still is not None and not still.isNull():
            size = (max(1, still.width()), max(1, still.height()))
            self._detail_still_size_cache[item.item_id] = size
            return size

        cached = self._detail_still_size_cache.get(item.item_id)
        if cached:
            return cached

        size = get_image_display_size(item.representative_image)
        if size:
            self._detail_still_size_cache[item.item_id] = size
            return size

        # Last resort only: fallback thumbnail.  This path should be rare.
        pix = self.fallback_pixmap_for_item(item)
        return max(1, pix.width()), max(1, pix.height())

    def update_live_button_style(self, checked: bool, enabled: bool | None = None):
        if enabled is not None:
            self.live_toggle.setEnabled(enabled)
        if checked:
            self.live_toggle.setText("停止实况")
            try:
                self.live_toggle.set_variant("live_on")
            except Exception:
                pass
        else:
            self.live_toggle.setText("播放实况")
            try:
                self.live_toggle.set_variant("live_off")
            except Exception:
                pass

    def start_detail_load(self, item: PhotoItemData, generation: int):
        if getattr(self, "_closing", False):
            return
        path = item.representative_image
        item_id = item.item_id
        fut = self._detail_executor.submit(make_detail_qimage, path, None)
        fut.add_done_callback(lambda f, g=generation, iid=item_id: self._detail_callback(g, iid, f))

    def _detail_callback(self, generation: int, item_id: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            qimg = future.result()
        except Exception:
            qimg = QImage()
        self._detail_signals.detail_ready.emit(generation, item_id, qimg)

    def on_detail_loaded(self, generation: int, item_id: str, qimg_obj):
        if getattr(self, "_closing", False):
            return
        item = self.current_item()
        if generation != self._load_generation or item is None or item.item_id != item_id:
            return
        if not isinstance(qimg_obj, QImage) or qimg_obj.isNull():
            self.info_label.setText(self.info_label.text().replace("  ｜  正在加载原图…", "  ｜  原图加载失败"))
            return
        pixmap = QPixmap.fromImage(qimg_obj)
        if pixmap.isNull():
            return
        # Keep the viewer responsive: full-resolution pixmap arrives after the dialog is
        # already visible with the thumbnail preview. If LIVE preview is playing, keep it
        # on screen and only cache the still image for when LIVE is turned off.
        self._still_pixmap_cache[item.item_id] = pixmap
        self._detail_still_size_cache[item.item_id] = (max(1, pixmap.width()), max(1, pixmap.height()))
        # If a still image finishes loading while LIVE playback is pending/active,
        # keep the cached still size updated so future frames and navigation use
        # the full photo geometry instead of the initial thumbnail geometry.
        if item.is_live and self.live_toggle.isChecked():
            self._detail_live_scene_target[item.item_id] = self._detail_still_size_cache[item.item_id]
        if not (item.is_live and self.live_toggle.isChecked() and self._detail_live_timer.isActive()):
            self.set_scene_pixmap(pixmap, fit=True)
        self.info_label.setText(self.detail_info_text(item))

    def load_current_item(self, fit: bool = True):
        item = self.current_item()
        if item is None:
            return
        self._load_generation += 1
        generation = self._load_generation
        self.setWindowTitle(item.display_name)
        self.update_delete_button_state()
        try:
            self.title_bar.set_title(item.display_name)
        except Exception:
            pass
        self.info_label.setText(self.detail_info_text(item, "  ｜  正在加载原图…"))
        self.index_label.setText(f"第 {self.current_pos + 1} / {len(self.ordered_ids)}")
        self.btn_prev.setEnabled(self.current_pos > 0)
        self.btn_next.setEnabled(self.current_pos < len(self.ordered_ids) - 1)

        # LIVE preview is available only for Live Photo groups. Keep the user's toggle
        # preference while navigating between LIVE items, but disable it for normal photos.
        was_live_enabled = self.live_toggle.isChecked()
        self._detail_live_timer.stop()
        self._detail_live_frame_index = 0
        self._pending_live_fit_generation = generation if (fit and item.is_live and was_live_enabled) else None
        blocker = QSignalBlocker(self.live_toggle)
        if not item.is_live:
            self.live_toggle.setChecked(False)
            self.update_live_button_style(False, enabled=False)
        else:
            self.live_toggle.setChecked(was_live_enabled)
            self.update_live_button_style(was_live_enabled, enabled=True)
        del blocker

        # Show cached thumbnail immediately so opening the detail window feels instant.
        self.set_scene_pixmap(self.fallback_pixmap_for_item(item), fit=True)
        self.start_detail_load(item, generation)
        if item.is_live and self.live_toggle.isChecked():
            self.start_detail_live_preview(item, generation)

    def on_detail_live_toggled(self, enabled: bool):
        item = self.current_item()
        if item is None or not item.is_live:
            self._detail_live_timer.stop()
            self.update_live_button_style(False, enabled=False)
            return
        self.update_live_button_style(enabled, enabled=True)
        if enabled:
            # User explicitly toggled LIVE on for the current photo: preserve the
            # current zoom/pan instead of fitting.  Only navigation auto-fit uses
            # _pending_live_fit_generation.
            self._pending_live_fit_generation = None
            self.start_detail_live_preview(item, self._load_generation)
        else:
            self._pending_live_fit_generation = None
            self._detail_live_timer.stop()
            self._detail_live_frame_index = 0
            still = self._still_pixmap_cache.get(item.item_id)
            if still is None or still.isNull():
                still = self.fallback_pixmap_for_item(item)
            # Do not reset zoom/pan when leaving LIVE mode.  The still image and
            # LIVE frames share the same scene geometry whenever possible.
            self.set_scene_pixmap(still, fit=None)
            self.info_label.setText(self.detail_info_text(item))

    def start_detail_live_preview(self, item: PhotoItemData, generation: int):
        if getattr(self, "_closing", False):
            return
        """Start LIVE preview in two stages.

        Stage 1: low-resolution direct-QImage frames, visible as soon as possible.
        Stage 2: higher-quality frames decoded after Stage 1 is already playing.
        """
        frames = self._detail_live_cache.get(item.item_id)
        if frames:
            self._detail_live_frame_index = 0
            self._detail_live_scene_target[item.item_id] = self.current_live_scene_target(item)
            self.apply_live_pixmap_preserve_view(frames[0], item.item_id)
            if self._pending_live_fit_generation == generation:
                self._pending_live_fit_generation = None
                QTimer.singleShot(0, self.view.reset_zoom_fit)
            if not self._detail_live_timer.isActive():
                self._detail_live_timer.start()
            quality = self._detail_live_quality.get(item.item_id, "快速")
            self.info_label.setText(f"{item.display_name}  ｜  实况预览播放中（{quality}，{len(frames)} 帧）")
            if quality != "高清":
                self.request_detail_live_hq(item, generation)
            return

        if item.item_id in self._detail_live_failed:
            self.info_label.setText(f"{item.display_name}  ｜  实况预览解码失败")
            return

        mov = find_live_video_file(item)
        if mov is None:
            self._detail_live_failed.add(item.item_id)
            self.info_label.setText(f"{item.display_name}  ｜  找不到对应 MOV，无法播放实况预览")
            return

        # Fast stage first. Do not start the expensive HQ job before fast preview
        # is visible; otherwise they compete for HEVC decoding resources and the
        # first visible frame becomes slower.
        if item.item_id not in self._detail_live_fast_requested:
            self._detail_live_fast_requested.add(item.item_id)
            self.info_label.setText(f"{item.display_name}  ｜  正在快速载入实况预览…")
            target_size = self.current_live_scene_target(item)
            self._detail_live_scene_target[item.item_id] = target_size
            fut = self._detail_live_executor.submit(make_detail_live_preview_qimages_fast, mov, target_size)
            fut.add_done_callback(lambda f, g=generation, iid=item.item_id: self._detail_live_callback(g, iid, "快速", f))
        else:
            self.info_label.setText(f"{item.display_name}  ｜  正在快速载入实况预览…")

    def request_detail_live_hq(self, item: PhotoItemData, generation: int):
        if getattr(self, "_closing", False):
            return
        if item.item_id in self._detail_live_hq_requested:
            return
        if self._detail_live_quality.get(item.item_id) == "高清":
            return
        mov = find_live_video_file(item)
        if mov is None:
            return
        self._detail_live_hq_requested.add(item.item_id)
        target_size = self._detail_live_scene_target.get(item.item_id) or self.current_live_scene_target(item)
        self._detail_live_scene_target[item.item_id] = target_size
        fut = self._detail_live_executor.submit(make_detail_live_preview_qimages_hq, mov, target_size)
        fut.add_done_callback(lambda f, g=generation, iid=item.item_id: self._detail_live_callback(g, iid, "高清", f))

    def _detail_live_callback(self, generation: int, item_id: str, quality: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            frames = future.result()
        except Exception:
            frames = []
        self._detail_signals.detail_live_ready.emit(generation, item_id, (quality, frames))

    def on_detail_live_loaded(self, generation: int, item_id: str, frames_obj):
        if getattr(self, "_closing", False):
            return
        item = self.current_item()
        if isinstance(frames_obj, tuple) and len(frames_obj) == 2:
            quality, raw_frames = frames_obj
        else:
            quality, raw_frames = "高清", frames_obj

        if quality == "快速":
            self._detail_live_fast_requested.discard(item_id)
        else:
            self._detail_live_hq_requested.discard(item_id)

        if generation != self._load_generation or item is None or item.item_id != item_id:
            return

        pixmaps: list[QPixmap] = []
        for frame in list(raw_frames or []):
            if isinstance(frame, QImage):
                pix = QPixmap.fromImage(frame)
                if not pix.isNull():
                    pixmaps.append(pix)
            elif isinstance(frame, (bytes, bytearray)):
                pix = QPixmap()
                if pix.loadFromData(bytes(frame), "PNG") and not pix.isNull():
                    pixmaps.append(pix)

        if not pixmaps:
            # Fast stage failed: try HQ once before marking failed.
            if quality == "快速":
                self.info_label.setText(f"{item.display_name}  ｜  快速预览失败，正在尝试高清解码…")
                self.request_detail_live_hq(item, generation)
                return
            self._detail_live_failed.add(item_id)
            if self.live_toggle.isChecked():
                self.info_label.setText(f"{item.display_name}  ｜  实况预览解码失败")
            return

        self._detail_live_failed.discard(item_id)
        self._detail_live_cache[item_id] = pixmaps
        self._detail_live_quality[item_id] = quality

        if self.live_toggle.isChecked():
            self._detail_live_frame_index = 0
            self._detail_live_scene_target.setdefault(item.item_id, self.current_live_scene_target(item))
            # Never reset zoom/pan for LIVE playback.  The frame is drawn into the
            # same scene rectangle as the still image, so enabling LIVE feels like
            # the photo itself becomes animated rather than opening a new video.
            self.apply_live_pixmap_preserve_view(pixmaps[0], item.item_id)
            if self._pending_live_fit_generation == generation:
                self._pending_live_fit_generation = None
                QTimer.singleShot(0, self.view.reset_zoom_fit)
            if not self._detail_live_timer.isActive():
                self._detail_live_timer.start()
            if quality == "快速":
                self.info_label.setText(f"{item.display_name}  ｜  实况预览播放中（快速，{len(pixmaps)} 帧）｜后台提高清晰度…")
                QTimer.singleShot(120, lambda it=item, gen=generation: self.request_detail_live_hq(it, gen))
            else:
                self.info_label.setText(f"{item.display_name}  ｜  实况预览播放中（高清，{len(pixmaps)} 帧）")
        elif quality == "快速":
            # If the user turned the switch off while fast frames were decoding,
            # still prepare HQ lazily only when the switch is turned on again.
            pass

    def advance_detail_live_frame(self):
        item = self.current_item()
        if item is None or not self.live_toggle.isChecked():
            self._detail_live_timer.stop()
            return
        frames = self._detail_live_cache.get(item.item_id)
        if not frames:
            self._detail_live_timer.stop()
            return
        self._detail_live_frame_index = (self._detail_live_frame_index + 1) % len(frames)
        # Do not rebuild the scene or reset zoom every frame.  Draw each frame
        # into the fixed still-photo scene rectangle.
        pixmap = frames[self._detail_live_frame_index]
        self.apply_live_pixmap_preserve_view(pixmap, item.item_id)

    def on_zoom_changed(self, zoom: float):
        self.zoom_label.setText(f"缩放：{zoom * 100:.0f}%")

    def show_previous(self):
        if self.current_pos > 0:
            self.current_pos -= 1
            self.load_current_item(fit=True)

    def show_next(self):
        if self.current_pos < len(self.ordered_ids) - 1:
            self.current_pos += 1
            self.load_current_item(fit=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, '_live_resizing', False):
            _update_resize_freeze_overlay(self)
            maybe_update_live_resize_window_mask(self, 28, interval_ms=0)
            return
        _update_resize_freeze_overlay(self)
        apply_opaque_rounded_window_mask(self, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(APP_BG))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            sync_frameless_shell_state(
                self, self._detail_outer_layout, self.detail_shell, self._detail_shadow,
                self._detail_normal_margin, normal_radius=28
            )
            apply_opaque_rounded_window_mask(self, 28)

    def nativeEvent(self, eventType, message):
        """Native Windows edge/corner resize for the frameless window.

        Use Qt global cursor coordinates converted through mapFromGlobal(), not
        raw lParam arithmetic.  This avoids high-DPI physical/logical coordinate
        mismatches that make the right/bottom resize point appear inside the UI.
        The hit band is anchored to the visible L2 shell edge and extends outward
        through the transparent shadow margin, but only a few pixels inward.
        """
        nccalc_result = handle_frameless_nccalcsize(eventType, message)
        if nccalc_result is not None:
            return nccalc_result
        try:
            etype = eventType.decode() if isinstance(eventType, (bytes, bytearray)) else str(eventType)
        except Exception:
            etype = str(eventType)
        if "windows" in etype and os.name == "nt" and not self.isMaximized():
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                WM_ENTERSIZEMOVE = 0x0231
                WM_SIZING = 0x0214
                WM_EXITSIZEMOVE = 0x0232
                if msg.message == WM_ENTERSIZEMOVE:
                    begin_window_live_resize(self, getattr(self, '_active_resize_edge', ''))
                elif msg.message == WM_SIZING:
                    begin_window_live_resize(self, _resize_edge_name_from_wmsz(int(msg.wParam)))
                elif msg.message == WM_EXITSIZEMOVE:
                    finish_window_live_resize(self, 28)
                # Make taskbar-click minimize reliable on the frameless window.
                # Restored WS_MINIMIZEBOX normally handles this, but accepting
                # SC_MINIMIZE here protects against Qt/Win10 style quirks.
                if msg.message == 0x0112 and (int(msg.wParam) & 0xFFF0) == 0xF020:
                    self.showMinimized()
                    return True, 0
                # Do not change layouts, masks or overlays during live resize.
                # Native Windows resizing must be the single source of geometry.
                WM_NCHITTEST = 0x0084
                if msg.message == WM_NCHITTEST:
                    shell = getattr(self, "detail_shell", None)
                    if shell is not None and shell.isVisible():
                        shell_rect = shell.geometry()
                    else:
                        shell_rect = self.rect()
                    pos = self.mapFromGlobal(QCursor.pos())
                    x, y = int(pos.x()), int(pos.y())
                    outer = self.rect()
                    # Hit testing is anchored to the *visible shell edge*.
                    # The previous wide band extended across the full transparent
                    # shadow margin, which made the right/bottom resize point feel
                    # displaced.  Keep a small symmetric band around the visible
                    # edge only.
                    band_in = 3
                    band_out = 7
                    left = (shell_rect.left() - band_out) <= x <= (shell_rect.left() + band_in)
                    right = (shell_rect.right() - band_in) <= x <= (shell_rect.right() + band_out)
                    top = (shell_rect.top() - band_out) <= y <= (shell_rect.top() + band_in)
                    bottom = (shell_rect.bottom() - band_in) <= y <= (shell_rect.bottom() + band_out)
                    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
                    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
                    if top and left:
                        self._active_resize_edge = 'top-left'
                        return True, HTTOPLEFT
                    if top and right:
                        self._active_resize_edge = 'top-right'
                        return True, HTTOPRIGHT
                    if bottom and left:
                        self._active_resize_edge = 'bottom-left'
                        return True, HTBOTTOMLEFT
                    if bottom and right:
                        self._active_resize_edge = 'bottom-right'
                        return True, HTBOTTOMRIGHT
                    if left:
                        self._active_resize_edge = 'left'
                        return True, HTLEFT
                    if right:
                        self._active_resize_edge = 'right'
                        return True, HTRIGHT
                    if top:
                        self._active_resize_edge = 'top'
                        return True, HTTOP
                    if bottom:
                        self._active_resize_edge = 'bottom'
                        return True, HTBOTTOM
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        self._closing = True
        self._load_generation += 1
        try:
            self._detail_live_timer.stop()
        except Exception:
            pass
        for timer_name in ("_live_resize_settle_timer", "_resize_freeze_fade_timer", "_live_resize_fallback_timer", "_rounded_mask_timer"):
            try:
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        try:
            if getattr(self, "_owns_detail_executor", True) and self._detail_executor is not None:
                self._detail_executor.shutdown(wait=False, cancel_futures=True)
            if getattr(self, "_owns_detail_live_executor", True) and self._detail_live_executor is not None:
                self._detail_live_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except Exception:
            pass
        event.accept()
        super().closeEvent(event)

    def _handle_detail_key(self, event) -> bool:
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key_Escape:
            self.close()
            return True
        if modifiers & Qt.ControlModifier and key == Qt.Key_L:
            self.rotate_current_item(-90)
            return True
        if modifiers & Qt.ControlModifier and key == Qt.Key_R:
            self.rotate_current_item(90)
            return True
        if key in (Qt.Key_Left, Qt.Key_A):
            self.show_previous()
            return True
        if key in (Qt.Key_Right, Qt.Key_D, Qt.Key_Space):
            self.show_next()
            return True
        if key == Qt.Key_0:
            self.view.set_zoom_100()
            return True
        if key in (Qt.Key_F, Qt.Key_Return, Qt.Key_Enter):
            self.view.reset_zoom_fit()
            return True
        return False

    def _object_belongs_to_this_window(self, obj) -> bool:
        try:
            if obj is self:
                return True
            if isinstance(obj, QWidget):
                return obj.window() is self
        except Exception:
            pass
        return False

    def _handle_manual_resize_event(self, obj, event) -> bool:
        try:
            if not self._object_belongs_to_this_window(obj):
                return False
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and not self.isMaximized():
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = event.globalPos()
                edge = frameless_edge_at_global(self, getattr(self, "detail_shell", None), gp)
                if edge:
                    self._manual_resizing = True
                    self._manual_resize_edge = edge
                    self._manual_resize_start_global = gp
                    self._manual_resize_start_geom = self.geometry()
                    mark_frameless_resize_activity(self, delay_ms=260)
                    try:
                        self.grabMouse()
                    except Exception:
                        pass
                    event.accept()
                    return True
            if et == QEvent.MouseMove and self._manual_resizing and self._manual_resize_start_geom is not None:
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = event.globalPos()
                dx = gp.x() - self._manual_resize_start_global.x()
                dy = gp.y() - self._manual_resize_start_global.y()
                mark_frameless_resize_activity(self, delay_ms=260)
                apply_manual_frameless_resize(self, str(self._manual_resize_edge), self._manual_resize_start_geom, dx, dy)
                event.accept()
                return True
            if et == QEvent.MouseButtonRelease and self._manual_resizing:
                self._manual_resizing = False
                self._manual_resize_edge = None
                self._manual_resize_start_geom = None
                mark_frameless_resize_activity(self, delay_ms=60)
                try:
                    self.releaseMouse()
                except Exception:
                    pass
                event.accept()
                return True
        except Exception:
            return False
        return False

    def eventFilter(self, obj, event):
        # Keep this filter only for preview keyboard shortcuts.
        # Native WM_NCHITTEST is the only resize mechanism; manual fallback was
        # removed because it can race with native live resize and make content jitter.
        if event.type() == QEvent.KeyPress and self._handle_detail_key(event):
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if self._handle_detail_key(event):
            event.accept()
            return
        super().keyPressEvent(event)




def _draw_text_fade(painter: QPainter, rect: QRectF, text: str, font: QFont, color: QColor, flags=Qt.AlignVCenter | Qt.AlignLeft, fade_width: int = 28):
    """Draw text with a soft alpha fade when it does not fit.

    Qt's normal clipping cuts characters abruptly.  iTunes-like lists instead let
    overflowing text fade out near the right edge.
    """
    text = str(text or '')
    if not text or rect.width() <= 1 or rect.height() <= 1:
        return
    painter.save()
    painter.setFont(font)
    fm = painter.fontMetrics()
    if fm.horizontalAdvance(text) <= int(rect.width()) - 2:
        painter.setPen(color)
        painter.drawText(rect, flags, text)
        painter.restore()
        return

    w = max(1, int(rect.width()))
    h = max(1, int(rect.height()))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    ip = QPainter(img)
    ip.setRenderHint(QPainter.TextAntialiasing, True)
    ip.setFont(font)
    ip.setPen(color)
    ip.drawText(QRectF(0, 0, w, h), flags, text)
    ip.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    fw = max(8, min(int(fade_width), max(8, w // 3)))
    if flags & Qt.AlignHCenter:
        # Centered header text: fade both sides if the text is too long.
        lg_left = QLinearGradient(0, 0, fw, 0)
        lg_left.setColorAt(0.0, QColor(0, 0, 0, 0))
        lg_left.setColorAt(1.0, QColor(0, 0, 0, 255))
        ip.fillRect(QRectF(0, 0, fw, h), lg_left)
    lg = QLinearGradient(w - fw, 0, w, 0)
    lg.setColorAt(0.0, QColor(0, 0, 0, 255))
    lg.setColorAt(1.0, QColor(0, 0, 0, 0))
    ip.fillRect(QRectF(w - fw, 0, fw, h), lg)
    ip.end()
    painter.drawImage(rect.topLeft(), img)
    painter.restore()


class FadingHeaderView(QHeaderView):
    """Centered, no-grid table header with soft text fade."""
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._font = choose_modern_font(10, bold=True)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setHighlightSections(False)
        self.setSectionsClickable(False)

    def paintSection(self, painter, rect, logicalIndex):
        if not rect.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(rect.adjusted(-1, 0, 1, 1), QColor(SYSTEM_GRAY_1))
        text = self.model().headerData(logicalIndex, self.orientation(), Qt.DisplayRole) if self.model() is not None else ''
        _draw_text_fade(
            painter, QRectF(rect).adjusted(8, 0, -8, 0), str(text or ''),
            self._font, QColor(APP_MUTED), Qt.AlignCenter, fade_width=24
        )
        painter.restore()

class PhotoTableDelegate(QStyledItemDelegate):
    """Custom table delegate.

    Draws a larger, stable thumbnail in the first column and forces selected rows
    to look the same whether they were selected from the grid view or inside the
    table view. This avoids Qt's inactive-selection pale-blue state on Windows.
    """
    def __init__(self, thumb_size: int, row_height: int, parent=None):
        super().__init__(parent)
        self.thumb_size = thumb_size
        self.row_height = row_height
        self.text_font = choose_modern_font(10)
        self.name_font = choose_modern_font(10)
        self.badge_font = choose_modern_font(8, bold=True)

    def _selected(self, option) -> bool:
        return bool(option.state & QStyle.State_Selected)

    def _apply_press_transform(self, painter: QPainter, option, row: int):
        view = self.parent()
        scale = 1.0
        anchor = None
        try:
            scale = float(view._press_scale(row))
            anchor = view._press_anchor(row)
        except Exception:
            return False
        if anchor is None or abs(scale - 1.0) < 0.002:
            return False
        # Clip to the full row width so the pressed row feels like one piece.
        row_clip = QRectF(0, option.rect.top(), max(1, view.viewport().width()), option.rect.height())
        painter.setClipRect(row_clip)
        painter.translate(float(anchor.x()), float(anchor.y()))
        painter.scale(scale, scale)
        painter.translate(-float(anchor.x()), -float(anchor.y()))
        return True

    def _row_blue_rect(self, option, index) -> Optional[QRectF]:
        """Return the animated row highlight rectangle in viewport coordinates.

        Selected rows slide in from the left.  Deselected rows slide out to the
        right.  The calculation is row-wide rather than per-cell, so the motion
        looks like one continuous strip across the whole list row.
        """
        view = self.parent()
        row_width = 0
        try:
            row_width = int(view.viewport().width())
        except Exception:
            row_width = int(option.rect.width())
        row_width = max(1, row_width)
        row_top = float(option.rect.top())
        row_h = float(option.rect.height())

        if self._selected(option):
            prog = 1.0
            try:
                prog = float(view._check_progress(index.row()))
            except Exception:
                pass
            p = ease_out_quint(prog)
            return QRectF(0, row_top, row_width * p, row_h)

        if hasattr(view, "_deselect_anim_start") and index.row() in getattr(view, "_deselect_anim_start", {}):
            prog = 1.0
            try:
                prog = float(view._deselect_progress(index.row()))
            except Exception:
                pass
            p = ease_out_quint(prog)
            return QRectF(row_width * p, row_top, row_width * (1.0 - p), row_h)

        return None

    def _row_background(self, painter: QPainter, option, index):
        """Draw an iTunes-like table row background with no grid seams.

        The table no longer relies on QTableView grid lines.  Each cell slightly
        overfills by one pixel so Windows fractional DPI / scroll positions cannot
        expose transparent hairlines between columns or rows.  Row separation is
        provided only by subtle alternating row colors.
        """
        base = QColor(CONTENT_BG) if index.row() % 2 == 0 else QColor(APP_PANEL_2)
        fill = QColor(ACCENT_BLUE) if self._selected(option) else base
        painter.fillRect(QRect(option.rect).adjusted(-1, 0, 2, 1), fill)

    def _draw_text_with_sliding_highlight(self, painter: QPainter, option, index, text_rect: QRectF, flags, text: str, normal_color: QColor):
        """Draw table/list text with a right-edge fade instead of hard clipping."""
        color = QColor("white") if self._selected(option) else normal_color
        font = painter.font()
        _draw_text_fade(painter, text_rect, str(text), font, color, flags, fade_width=32)

    def _cell_alignment(self, column: int):
        # iTunes-like list: text columns are not scattered across the cell.
        # Only compact numeric status columns are centered.
        if column == 3:
            return Qt.AlignVCenter | Qt.AlignHCenter
        return Qt.AlignVCenter | Qt.AlignLeft

    def _cell_color(self, column: int) -> QColor:
        if column in (4, 5):
            return QColor(APP_MUTED)
        return QColor(APP_TEXT)

    def _draw_live_badge(self, painter: QPainter, thumb_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        badge_w = 52
        badge_h = 20
        bx = thumb_rect.left() + 7
        by = thumb_rect.bottom() - badge_h - 7
        badge = QRectF(bx, by, badge_w, badge_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(18, 18, 20, 202))
        painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)

        dot_d = 6
        dot_x = bx + 8
        dot_y = by + (badge_h - dot_d) / 2
        painter.setBrush(QColor("#34C759"))
        painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))

        painter.setFont(self.badge_font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(bx + 18, by, badge_w - 18, badge_h), Qt.AlignVCenter | Qt.AlignLeft, "LIVE")
        painter.restore()

    def _draw_unbound_mov_badge(self, painter: QPainter, thumb_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        badge_w = 72
        badge_h = 20
        bx = thumb_rect.left() + 7
        by = thumb_rect.bottom() - badge_h - 7
        badge = QRectF(bx, by, badge_w, badge_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(82, 70, 42, 210))
        painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)

        dot_d = 6
        dot_x = bx + 8
        dot_y = by + (badge_h - dot_d) / 2
        painter.setBrush(QColor("#FFCC00"))
        painter.drawEllipse(QRectF(dot_x, dot_y, dot_d, dot_d))

        painter.setFont(self.badge_font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(bx + 18, by, badge_w - 18, badge_h), Qt.AlignVCenter | Qt.AlignLeft, "待绑定")
        painter.restore()

    def _draw_selection_check(self, painter: QPainter, thumb_rect: QRectF, progress: float = 1.0):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        progress = clamp01(progress)
        check_raw = clamp01((progress - 0.10) / 0.90)
        p = ease_out_back(check_raw, 1.70)
        scale = max(0.05, min(1.24, p))
        r = 12.5
        cx = thumb_rect.right() - 18
        cy = thumb_rect.top() + 18
        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.setOpacity(max(0.0, min(1.0, 0.10 + 0.90 * check_raw)))
        painter.setPen(QPen(QColor(255, 255, 255, 245), 2.8))
        painter.setBrush(QColor(0, 122, 255))
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        check = QPainterPath()
        check.moveTo(-6.3, -1)
        check.lineTo(-1.0, 5.2)
        check.lineTo(8.5, -7.0)
        check_pen = QPen(QColor("white"), 3.2)
        check_pen.setCapStyle(Qt.RoundCap)
        check_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(check_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(check)
        painter.restore()

    def _draw_deselect(self, painter: QPainter, thumb_rect: QRectF, progress: float = 1.0):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        progress = clamp01(progress)
        fade = 1.0 - ease_out_cubic(progress)
        if fade <= 0.001:
            painter.restore()
            return
        pen = QPen(QColor(0, 122, 255, int(230 * fade)), 4.6 * fade + 0.6)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 122, 255, int(42 * fade)))
        inset = 2.0 + 7.0 * progress
        painter.drawRoundedRect(thumb_rect.adjusted(inset, inset, -inset, -inset), 8, 8)
        painter.restore()

    def _draw_placeholder(self, painter: QPainter, thumb_rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(thumb_rect, QColor(APP_PANEL_2))
        cx = thumb_rect.center().x()
        cy = thumb_rect.center().y() - 4
        glyph_w = 44
        glyph_h = 34
        g = QRectF(cx - glyph_w / 2, cy - glyph_h / 2, glyph_w, glyph_h)
        pen = QPen(QColor("#8E8E93"), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(g, 6, 6)
        painter.setBrush(QColor("#8E8E93"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(g.left() + 8, g.top() + 7, 7, 7))
        painter.setPen(pen)
        mountain = QPainterPath()
        mountain.moveTo(g.left() + 6, g.bottom() - 7)
        mountain.lineTo(g.left() + 18, g.bottom() - 19)
        mountain.lineTo(g.left() + 29, g.bottom() - 7)
        mountain.moveTo(g.left() + 23, g.bottom() - 7)
        mountain.lineTo(g.left() + 34, g.bottom() - 16)
        mountain.lineTo(g.right() - 6, g.bottom() - 7)
        painter.drawPath(mountain)
        painter.restore()

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # List/table view intentionally has no press-scale transform.  The row
        # highlight itself is enough feedback here; scaling list rows made dense
        # range selection feel heavy and visually noisy.
        self._row_background(painter, option, index)

        selected = self._selected(option)

        if index.column() == 0:
            cell = QRectF(option.rect).adjusted(10, 6, -8, -6)
            side = self.thumb_size
            tx = cell.left()
            ty = cell.top() + max(0, (cell.height() - side) / 2)
            thumb_rect = QRectF(tx, ty, side, side)

            pix = index.data(Qt.DecorationRole)
            if isinstance(pix, QPixmap) and not pix.isNull():
                painter.save()
                path = QPainterPath()
                path.addRoundedRect(thumb_rect, 8, 8)
                painter.setClipPath(path)
                painter.drawPixmap(thumb_rect.toRect(), pix)
                painter.restore()
            else:
                self._draw_placeholder(painter, thumb_rect)

            if selected:
                # Subtle iOS-like darkening on the thumbnail itself; row highlight
                # still handles the list-view selection background.
                painter.save()
                path = QPainterPath()
                path.addRoundedRect(thumb_rect, 8, 8)
                painter.setClipPath(path)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(0, 0, 0, 44))
                painter.drawRect(thumb_rect)
                painter.restore()

            if bool(index.data(NEEDS_BINDING_ROLE)):
                self._draw_unbound_mov_badge(painter, thumb_rect)
            elif bool(index.data(IS_LIVE_ROLE)):
                self._draw_live_badge(painter, thumb_rect)
            if selected:
                prog = 1.0
                try:
                    prog = float(self.parent()._check_progress(index.row()))
                except Exception:
                    pass
                self._draw_selection_check(painter, thumb_rect, prog)
            elif hasattr(self.parent(), "_deselect_anim_start") and index.row() in getattr(self.parent(), "_deselect_anim_start", {}):
                try:
                    prog = float(self.parent()._deselect_progress(index.row()))
                except Exception:
                    prog = 1.0
                # Animate only the check badge out; do not show a phantom blue
                # selection frame in list view.
                self._draw_selection_check(painter, thumb_rect, 1.0 - prog)

            text_rect = QRectF(
                thumb_rect.right() + 12,
                option.rect.top(),
                max(10, option.rect.right() - thumb_rect.right() - 18),
                option.rect.height(),
            )
            painter.setFont(self.name_font)
            name = index.data(Qt.DisplayRole) or ""
            self._draw_text_with_sliding_highlight(
                painter, option, index, text_rect,
                Qt.AlignVCenter | Qt.AlignLeft, str(name), QColor("#111111")
            )
        else:
            value = index.data(Qt.DisplayRole) or ""
            painter.setFont(self.text_font)
            col = index.column()
            normal_color = self._cell_color(col)
            # Wider left/right padding removes the "spreadsheet grid" feeling and
            # makes the table read as a clean iTunes-style list.
            left_pad = 14 if col != 3 else 6
            right_pad = 14 if col != 3 else 6
            text_rect = QRectF(option.rect).adjusted(left_pad, 0, -right_pad, 0)
            self._draw_text_with_sliding_highlight(
                painter, option, index, text_rect,
                self._cell_alignment(col), str(value), normal_color
            )

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(260, self.row_height)


class PhotoTableView(QTableView):
    """Table view with the same modern hover behavior as the photo wall.

    It uses a custom cursor-following tooltip, emits hover changes for LIVE preview,
    and opens the detail viewer by double-click.  Single-click selection is
    committed with a very short post-release delay so table rows feel responsive
    without reintroducing press-time false selection.
    """
    hover_item_changed = Signal(str)
    item_open_requested = Signal(str)
    context_menu_requested = Signal(str, QPoint, bool)
    clear_selection_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selection_mode_enabled = True
        self._hover_item_id: Optional[str] = None
        self._hover_global_pos = QPoint(0, 0)
        self._tooltip_widget = FollowTooltip()
        self._tooltip_text_cache: dict[str, str] = {}
        self._pending_tooltip_id: Optional[str] = None
        self._click_candidate_id: Optional[str] = None
        self._click_candidate_row: Optional[int] = None
        self._click_candidate_ctrl = False
        self._click_candidate_base_rows: set[int] = set()
        self._press_pos = None
        self._table_dragging_select = False
        self._table_drag_anchor_row: Optional[int] = None
        self._table_drag_action = "select"
        self._table_drag_button = Qt.NoButton
        self._pre_press_selected_rows: set[int] = set()
        self._last_click_restore_rows: set[int] = set()
        self._last_click_item_id: Optional[str] = None
        self._last_click_time = 0.0
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self._commit_pending_single_click_selection)
        self._check_anim_start: dict[int, float] = {}
        self._deselect_anim_start: dict[int, float] = {}
        self._press_down_start: dict[int, float] = {}
        self._press_effect_start: dict[int, float] = {}
        self._press_effect_anchor: dict[int, QPoint] = {}
        self._active_press_row: Optional[int] = None
        self._right_click_candidate_row: Optional[int] = None
        self._right_click_candidate_id: Optional[str] = None
        self._blank_left_press = False
        self._blank_press_pos = None
        self._visual_selected_rows: set[int] = set()
        self._check_anim_timer = QTimer(self)
        self._check_anim_timer.setTimerType(Qt.PreciseTimer)
        self._check_anim_timer.setInterval(adaptive_animation_interval_ms(self))
        self._check_anim_timer.timeout.connect(self._advance_check_animations)
        self._full_tooltip_timer = QTimer(self)
        self._full_tooltip_timer.setSingleShot(True)
        self._full_tooltip_timer.setInterval(120)
        self._full_tooltip_timer.timeout.connect(self._finish_tooltip_text)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        try:
            self.setAttribute(Qt.WA_Hover, False)
            self.viewport().setAttribute(Qt.WA_Hover, False)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, True)
        except Exception:
            pass
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.viewport().setContextMenuPolicy(Qt.NoContextMenu)
        self._empty_state_overlay = EmptyLibraryStateOverlay(self, table_mode=True)

    def setModel(self, model):
        super().setModel(model)
        if model is not None:
            for signal_name in ("modelReset", "rowsInserted", "rowsRemoved", "layoutChanged"):
                try:
                    getattr(model, signal_name).connect(self._sync_empty_state_overlay)
                except Exception:
                    pass
        QTimer.singleShot(0, self._sync_empty_state_overlay)

    def _sync_empty_state_overlay(self, *_args):
        overlay = getattr(self, "_empty_state_overlay", None)
        if overlay is None:
            return
        overlay.setGeometry(self.viewport().rect())
        model = self.model()
        empty = model is not None and model.rowCount() == 0
        overlay.setVisible(empty)
        if empty:
            overlay.raise_()
            overlay.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_empty_state_overlay()
        try:
            win = self.window()
            if hasattr(win, 'update_table_column_layout'):
                QTimer.singleShot(0, win.update_table_column_layout)
        except Exception:
            pass

    def _hide_follow_tooltip_now(self):
        try:
            self._full_tooltip_timer.stop()
            self._pending_tooltip_id = None
            self._tooltip_widget.hide()
        except Exception:
            pass

    def _update_visual_row(self, row: Optional[int]):
        if row is None:
            return
        model = self.model()
        if model is None or row < 0 or row >= model.rowCount():
            return
        try:
            rect = self.visualRect(model.index(row, 0))
            rect.setRight(self.viewport().width())
            self.viewport().update(rect)
        except Exception:
            self.viewport().update()

    def _check_progress(self, row: int) -> float:
        start = self._check_anim_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / CHECK_ANIM_MS)

    def _deselect_progress(self, row: int) -> float:
        start = self._deselect_anim_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / DESELECT_ANIM_MS)

    def _press_progress(self, row: int) -> float:
        start = self._press_effect_start.get(row)
        if start is None:
            return 1.0
        return clamp01((time.monotonic() - start) * 1000.0 / PRESS_RELEASE_ANIM_MS)

    def _press_scale(self, row: int) -> float:
        now = time.monotonic()
        down_start = self._press_down_start.get(row)
        if down_start is not None:
            p = clamp01((now - down_start) * 1000.0 / PRESS_DOWN_ANIM_MS)
            return 1.0 - 0.046 * ease_out_cubic(p)
        release_start = self._press_effect_start.get(row)
        if release_start is not None:
            p = clamp01((now - release_start) * 1000.0 / PRESS_RELEASE_ANIM_MS)
            return 1.0 - 0.046 * (1.0 - ease_out_quint(p))
        return 1.0

    def _press_anchor(self, row: int):
        return self._press_effect_anchor.get(row)

    def _start_press_effect_for_row(self, row: int, pos):
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        if row < 0 or row > max_row:
            return
        self._press_down_start[row] = time.monotonic()
        self._press_effect_start.pop(row, None)
        try:
            self._press_effect_anchor[row] = QPoint(int(pos.x()), int(pos.y()))
        except Exception:
            self._press_effect_anchor[row] = QPoint(0, 0)
        self._update_visual_row(row)
        if not self._check_anim_timer.isActive():
            self._check_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._check_anim_timer.start()

    def _release_press_effect_for_row(self, row: Optional[int]):
        if row is None:
            return
        if row in self._press_down_start:
            self._press_down_start.pop(row, None)
            self._press_effect_start[row] = time.monotonic()
            self._update_visual_row(row)
            if not self._check_anim_timer.isActive():
                self._check_anim_timer.setInterval(adaptive_animation_interval_ms(self))
                self._check_anim_timer.start()
        elif row in self._press_effect_start:
            return


    def _move_press_effect_to_row(self, row: Optional[int], pos):
        """Move the physical press feedback to the row currently under the cursor.

        This is used during left-button range selection: every tile/row the
        pointer enters gets its own press-in animation, and it rebounds when the
        pointer leaves.  Only the currently hovered row is held pressed.
        """
        if row == self._active_press_row:
            return
        old = self._active_press_row
        if old is not None:
            self._release_press_effect_for_row(old)
        self._active_press_row = row
        if row is not None:
            self._start_press_effect_for_row(row, pos)

    def _release_active_press_effect(self):
        row = self._active_press_row
        self._active_press_row = None
        if row is not None:
            self._release_press_effect_for_row(row)
    def _start_deselect_animation_for_rows(self, rows: set[int]):
        # In table/list view the thumbnail itself should not get a phantom blue frame,
        # but the full-row highlight can fade out smoothly. The delegate reads
        # _deselect_anim_start and animates only the row background.
        if not rows:
            return
        now = time.monotonic()
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        for row in rows:
            if 0 <= row <= max_row:
                self._deselect_anim_start[row] = now
                self._update_visual_row(row)
        if not self._check_anim_timer.isActive():
            self._check_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._check_anim_timer.start()

    def _start_check_animation_for_rows(self, rows: set[int]):
        if not rows:
            return
        now = time.monotonic()
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        for row in rows:
            if 0 <= row <= max_row:
                self._check_anim_start[row] = now
                self._update_visual_row(row)
        if not self._check_anim_timer.isActive():
            self._check_anim_timer.setInterval(adaptive_animation_interval_ms(self))
            self._check_anim_timer.start()

    def _advance_check_animations(self):
        active = False
        now = time.monotonic()
        done = []
        for row, start in list(self._check_anim_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= CHECK_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._check_anim_start.pop(row, None)
            self._update_visual_row(row)
        done = []
        for row, start in list(self._deselect_anim_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= DESELECT_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._deselect_anim_start.pop(row, None)
            self._update_visual_row(row)
        for row, start in list(self._press_down_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 < PRESS_DOWN_ANIM_MS:
                active = True
        done = []
        for row, start in list(self._press_effect_start.items()):
            self._update_visual_row(row)
            if (now - start) * 1000.0 >= PRESS_RELEASE_ANIM_MS:
                done.append(row)
            else:
                active = True
        for row in done:
            self._press_effect_start.pop(row, None)
            self._press_effect_anchor.pop(row, None)
            self._update_visual_row(row)
        if not active:
            self._check_anim_timer.stop()

    def selectionChanged(self, selected, deselected):
        super().selectionChanged(selected, deselected)
        previous = set(self._visual_selected_rows)
        current = self._current_selected_rows()
        newly_selected = current - previous
        newly_deselected = previous - current
        self._visual_selected_rows = set(current)
        self._start_check_animation_for_rows(newly_selected)
        self._start_deselect_animation_for_rows(newly_deselected)

    def _current_selected_rows(self) -> set[int]:
        sm = self.selectionModel()
        if sm is None:
            return set()
        return {i.row() for i in sm.selectedRows()}

    def _restore_selected_rows(self, rows: set[int]):
        model = self.model()
        sm = self.selectionModel()
        if model is None or sm is None:
            return
        max_row = model.rowCount() - 1
        max_col = max(0, model.columnCount() - 1)
        selection = QItemSelection()
        for a, b in compact_ranges([r for r in rows if 0 <= r <= max_row]):
            selection.select(model.index(a, 0), model.index(b, max_col))
        before = set(self._visual_selected_rows)
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self._visual_selected_rows = {r for r in rows if 0 <= r <= max_row}
        newly_selected = self._visual_selected_rows - before
        newly_deselected = before - self._visual_selected_rows
        changed = newly_selected | newly_deselected
        if len(changed) <= 80:
            self._start_check_animation_for_rows(newly_selected)
            self._start_deselect_animation_for_rows(newly_deselected)
        else:
            for r in changed:
                self._check_anim_start.pop(r, None)
                self._deselect_anim_start.pop(r, None)
        self.viewport().update()

    def _selection_rows_for_single_left_click(self, row: int, ctrl: bool, base_rows: set[int]) -> set[int]:
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        if row < 0 or row > max_row:
            return set(base_rows)
        if ctrl:
            rows = set(base_rows)
            if row in rows:
                rows.remove(row)
            else:
                rows.add(row)
            return rows
        # Plain click on a tile/row starts a fresh single-item selection.
        # But if exactly this one item is already selected, clicking it once again
        # cancels the selection. Double-click preview restores the pre-click state,
        # so this does not pollute the open-preview gesture.
        if set(base_rows) == {row}:
            return set()
        return {row}

    def _schedule_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        # Table/list view should feel immediate.  The photo wall keeps the full
        # double-click delay to avoid any visual flash, but in table view that delay
        # makes ordinary single-click selection feel laggy.  Commit shortly after
        # mouse release: this avoids selection on press, keeps drag/right-click logic
        # intact, and makes list-row clicking respond nearly instantly.
        self._single_click_timer.start(TABLE_SINGLE_CLICK_SELECTION_DELAY_MS)

    def _commit_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        rows = self._selection_rows_for_single_left_click(
            self._click_candidate_row,
            self._click_candidate_ctrl,
            self._click_candidate_base_rows,
        )
        self._restore_selected_rows(rows)
        self._click_candidate_row = None
        self._click_candidate_id = None
        self._click_candidate_base_rows = set()
        self._press_pos = None

    def _cancel_pending_single_click_selection(self):
        self._single_click_timer.stop()
        self._click_candidate_row = None
        self._click_candidate_id = None
        self._click_candidate_base_rows = set()
        self._release_active_press_effect()
        self._press_pos = None

    def _apply_row_selection(self, rows: set[int]):
        model = self.model()
        sm = self.selectionModel()
        if model is None or sm is None:
            return
        max_row = model.rowCount() - 1
        max_col = max(0, model.columnCount() - 1)
        selection = QItemSelection()
        for a, b in compact_ranges([r for r in rows if 0 <= r <= max_row]):
            selection.select(model.index(a, 0), model.index(b, max_col))
        before = set(self._visual_selected_rows)
        sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self._visual_selected_rows = {r for r in rows if 0 <= r <= max_row}
        newly_selected = self._visual_selected_rows - before
        newly_deselected = before - self._visual_selected_rows
        changed = newly_selected | newly_deselected
        if len(changed) <= 80:
            self._start_check_animation_for_rows(newly_selected)
            self._start_deselect_animation_for_rows(newly_deselected)
        else:
            for r in changed:
                self._check_anim_start.pop(r, None)
                self._deselect_anim_start.pop(r, None)
        self.viewport().update()

    def _rows_for_table_drag(self, current_row: int) -> set[int]:
        model = self.model()
        if model is None or self._table_drag_anchor_row is None:
            return set(self._pre_press_selected_rows)
        max_row = model.rowCount() - 1
        if max_row < 0:
            return set()
        start = max(0, min(self._table_drag_anchor_row, current_row))
        end = min(max_row, max(self._table_drag_anchor_row, current_row))
        drag_rows = set(range(start, end + 1))
        rows = set(self._pre_press_selected_rows) if getattr(self, "_click_candidate_ctrl", False) else set()
        for r in drag_rows:
            if r in rows:
                rows.remove(r)
            else:
                rows.add(r)
        return rows

    def _apply_table_drag_selection(self, current_row: int):
        self._apply_row_selection(self._rows_for_table_drag(current_row))

    def _selection_rows_for_single_left_click(self, row: int, ctrl: bool, base_rows: set[int]) -> set[int]:
        model = self.model()
        max_row = model.rowCount() - 1 if model is not None else -1
        if row < 0 or row > max_row:
            return set(base_rows)
        if ctrl:
            rows = set(base_rows)
            if row in rows:
                rows.remove(row)
            else:
                rows.add(row)
            return rows
        # Plain click on a tile/row starts a fresh single-item selection.
        # But if exactly this one item is already selected, clicking it once again
        # cancels the selection. Double-click preview restores the pre-click state,
        # so this does not pollute the open-preview gesture.
        if set(base_rows) == {row}:
            return set()
        return {row}

    def _schedule_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        # Fast table-row click commit.  Do not wait for the full OS double-click
        # interval; otherwise list view feels delayed while drag selection remains fast.
        self._single_click_timer.start(TABLE_SINGLE_CLICK_SELECTION_DELAY_MS)

    def _commit_pending_single_click_selection(self):
        if self._click_candidate_row is None:
            return
        rows = self._selection_rows_for_single_left_click(
            self._click_candidate_row,
            self._click_candidate_ctrl,
            self._click_candidate_base_rows,
        )
        self._apply_row_selection(rows)
        self._last_click_restore_rows = set(self._click_candidate_base_rows)
        self._last_click_item_id = str(self._click_candidate_id) if self._click_candidate_id else None
        self._last_click_time = time.monotonic()
        self._click_candidate_id = None
        self._click_candidate_row = None
        self._click_candidate_base_rows = set()
        self._press_pos = None

    def _cancel_pending_single_click_selection(self):
        self._single_click_timer.stop()
        self._click_candidate_id = None
        self._click_candidate_row = None
        self._click_candidate_base_rows = set()
        self._press_pos = None

    def wheelEvent(self, event):
        if scroll_area_wheel_per_pixel(self, event, base_step=42.0):
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                self._cancel_pending_single_click_selection()
                self._table_dragging_select = False
                self._table_drag_anchor_row = None
                item_id = str(idx.data(ITEM_ID_ROLE) or "")
                restore_rows = self._pre_press_selected_rows
                if (
                    self._last_click_item_id == item_id
                    and (time.monotonic() - self._last_click_time) <= (QApplication.doubleClickInterval() + 180) / 1000.0
                ):
                    restore_rows = self._last_click_restore_rows
                self._restore_selected_rows(restore_rows)
                self.item_open_requested.emit(item_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def set_selection_mode_enabled(self, enabled: bool):
        self.selection_mode_enabled = bool(enabled)
        self._click_candidate_id = None
        self._click_candidate_row = None
        self._tooltip_widget.hide()
        self.setCursor(Qt.ArrowCursor if enabled else Qt.PointingHandCursor)

    def _event_global_pos(self, event):
        try:
            return event.globalPosition().toPoint()
        except Exception:
            try:
                return event.globalPos()
            except Exception:
                return QPoint(0, 0)

    def _item_for_id(self, item_id: str):
        model = self.model()
        try:
            window = getattr(model, "window", None)
            if window is not None:
                return window.item_map.get(item_id)
        except Exception:
            pass
        return None

    def _set_hover_item_from_index(self, index):
        try:
            item_id = str(index.data(ITEM_ID_ROLE)) if index is not None and index.isValid() and index.data(ITEM_ID_ROLE) else ""
        except Exception:
            item_id = ""
        if item_id != self._hover_item_id:
            if item_id:
                self._hover_item_id = item_id
                self.hover_item_changed.emit(item_id)
            else:
                if self._hover_item_id is not None:
                    self.hover_item_changed.emit("")
                self._hover_item_id = None
        return item_id

    def _quick_tooltip_text(self, item_id: str) -> str:
        item = self._item_for_id(item_id)
        if item is None:
            return "正在读取信息……"
        try:
            model = self.model()
            window = getattr(model, "window", None)
            formatter = getattr(window, "quick_tooltip_for_item", None)
            if callable(formatter):
                return formatter(item)
        except Exception:
            pass
        return (
            f"文件名：{item.display_name}\n"
            f"类型：{item.item_type}\n"
            f"时间：{format_time(item.shot_time)}\n"
            f"容量：{format_bytes(item.size_bytes)}"
        )

    def _update_follow_tooltip(self, event):
        global_pos = self._event_global_pos(event)
        self._hover_global_pos = global_pos
        if self._tooltip_widget.isVisible():
            self._tooltip_widget.move_near(global_pos)
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            self._full_tooltip_timer.stop()
            self._pending_tooltip_id = None
            self._set_hover_item_from_index(QModelIndex())
            self._tooltip_widget.hide()
            return
        old_hover = self._hover_item_id
        item_id = self._set_hover_item_from_index(index)
        if not item_id:
            self._tooltip_widget.hide()
            return
        if item_id != old_hover:
            cached = self._tooltip_text_cache.get(item_id)
            if cached:
                self._tooltip_widget.show_text(cached, global_pos)
            else:
                self._tooltip_widget.show_text(self._quick_tooltip_text(item_id), global_pos)
                self._pending_tooltip_id = item_id
                self._full_tooltip_timer.start()
        else:
            if not self._tooltip_widget.isVisible():
                cached = self._tooltip_text_cache.get(item_id) or self._quick_tooltip_text(item_id)
                self._tooltip_widget.show_text(cached, global_pos)

    def _finish_tooltip_text(self):
        item_id = self._pending_tooltip_id
        self._pending_tooltip_id = None
        if not item_id or item_id != self._hover_item_id:
            return
        item = self._item_for_id(item_id)
        if item is None:
            return
        try:
            model = self.model()
            window = getattr(model, "window", None)
            formatter = getattr(window, "tooltip_for_item", None)
            text = formatter(item) if callable(formatter) else tooltip_for_item(item)
        except Exception:
            text = tooltip_for_item(item)
        self._tooltip_text_cache[item_id] = text
        self._tooltip_widget.show_text(text, self._hover_global_pos)

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            return True
        if event.type() in (QEvent.HoverMove, QEvent.HoverEnter):
            try:
                idx = self.indexAt(event.position().toPoint())
                self._set_hover_item_from_index(idx)
            except Exception:
                pass
            return True
        if event.type() == QEvent.HoverLeave:
            try:
                self._set_hover_item_from_index(QModelIndex())
            except Exception:
                pass
            return True
        return super().viewportEvent(event)

    def leaveEvent(self, event):
        self._full_tooltip_timer.stop()
        self._pending_tooltip_id = None
        if self._hover_item_id is not None:
            self.hover_item_changed.emit("")
        self._hover_item_id = None
        self._tooltip_widget.hide()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        self._update_follow_tooltip(event)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self._tooltip_widget.hide()
        self._full_tooltip_timer.stop()
        self._single_click_timer.stop()
        self._pre_press_selected_rows = self._current_selected_rows()
        button = event.button()
        if button == Qt.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                self._click_candidate_id = idx.data(ITEM_ID_ROLE)
                self._click_candidate_row = idx.row()
                self._click_candidate_ctrl = bool(event.modifiers() & Qt.ControlModifier)
                self._click_candidate_base_rows = set(self._pre_press_selected_rows)
                self._press_pos = event.position().toPoint()
                self._table_dragging_select = False
                self._table_drag_anchor_row = idx.row()
                self._table_drag_action = "toggle"
                self._table_drag_button = Qt.LeftButton
                event.accept()
                return
            self._blank_left_press = True
            self._blank_press_pos = event.position().toPoint()
            super().mousePressEvent(event)
            return
        if button == Qt.RightButton:
            self._cancel_pending_single_click_selection()
            idx = self.indexAt(event.position().toPoint())
            self._right_click_candidate_row = idx.row() if idx.isValid() else None
            self._right_click_candidate_id = str(idx.data(ITEM_ID_ROLE) or "") if idx.isValid() else None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._hide_follow_tooltip_now()
            if self._click_candidate_row is not None:
                idx = self.indexAt(event.position().toPoint())
                if idx.isValid():
                    moved_far = False
                    try:
                        if self._press_pos is not None:
                            moved_far = (event.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
                    except Exception:
                        moved_far = True
                    if self._table_dragging_select or moved_far or idx.row() != self._click_candidate_row:
                        self._table_dragging_select = True
                        self._table_drag_action = "toggle"
                        self._table_drag_button = Qt.LeftButton
                        self._apply_table_drag_selection(idx.row())
                        self.scrollTo(idx, QAbstractItemView.EnsureVisible)
                        event.accept()
                        return
            event.accept()
            return
        self._update_follow_tooltip(event)
        # Passive table hover is fully custom; do not let QTableView repaint
        # native hover/active cells on every mouse-move event.
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._blank_left_press:
            moved_far = False
            try:
                if self._blank_press_pos is not None:
                    moved_far = (event.position().toPoint() - self._blank_press_pos).manhattanLength() >= QApplication.startDragDistance()
            except Exception:
                moved_far = True
            self._blank_left_press = False
            self._blank_press_pos = None
            if not moved_far and not self.indexAt(event.position().toPoint()).isValid():
                self.clear_selection_requested.emit()
                event.accept()
                return
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.LeftButton and self._click_candidate_id:
            idx = self.indexAt(event.position().toPoint())
            if self._table_dragging_select:
                if idx.isValid() and self._click_candidate_row is not None:
                    self._apply_table_drag_selection(idx.row())
                self._cancel_pending_single_click_selection()
            else:
                if idx.isValid() and idx.row() == self._click_candidate_row:
                    self._schedule_pending_single_click_selection()
                else:
                    self._cancel_pending_single_click_selection()
            self._table_dragging_select = False
            self._table_drag_anchor_row = None
            self._table_drag_button = Qt.NoButton
            self._table_drag_action = "toggle"
            event.accept()
            return
        if event.button() == Qt.RightButton:
            idx = self.indexAt(event.position().toPoint())
            selected_rows = self._current_selected_rows()
            candidate_row = self._right_click_candidate_row if self._right_click_candidate_row is not None else (idx.row() if idx.isValid() else None)
            candidate_id = self._right_click_candidate_id or (str(idx.data(ITEM_ID_ROLE) or "") if idx.isValid() else "")
            if candidate_row is not None and candidate_id:
                if selected_rows and candidate_row in selected_rows:
                    self.context_menu_requested.emit(candidate_id, self._event_global_pos(event), False)
                elif not selected_rows:
                    self._restore_selected_rows({candidate_row})
                    self.context_menu_requested.emit(candidate_id, self._event_global_pos(event), True)
                else:
                    self.clear_selection_requested.emit()
            else:
                self.clear_selection_requested.emit()
            self._right_click_candidate_row = None
            self._right_click_candidate_id = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ==========================
# Main window
# ==========================



# ==========================
# Modern L2-continuous UI primitives
# ==========================


def l2_continuous_path(rect: QRectF, radius: float | None = None, exponent: float = 5.2, samples: int = 28, corners=(True, True, True, True)) -> QPainterPath:
    """Continuous-corner rectangle with optionally squared corners.

    corners are ordered as: top-left, top-right, bottom-right, bottom-left.
    This lets connected controls such as a combo face + popup share one visual
    sheet: the trigger keeps only its top continuous corners while the popup keeps
    only its lower continuous corners.
    """
    r = QRectF(rect)
    path = QPainterPath()
    if r.width() <= 0 or r.height() <= 0:
        return path
    profile = globals().get("RUNTIME_THEME_PROFILE")
    corner_style = getattr(profile, "corner_style", "continuous")
    if corner_style == "square":
        path.addRect(r)
        return path
    import math
    w, h = float(r.width()), float(r.height())
    if radius is None:
        radius = min(w, h) * 0.44
    radius = max(0.0, min(float(radius), min(w, h) / 2.0))
    if corner_style == "rounded":
        radius = min(radius, float(getattr(profile, "control_radius", 4)))
        path.addRoundedRect(r, radius, radius)
        return path
    radius = max(2.0, radius)
    n = max(2.2, float(exponent))
    power = 2.0 / n

    x0, y0 = r.left(), r.top()
    x1, y1 = r.right(), r.bottom()
    rad = radius
    tl, tr, br, bl = [bool(c) for c in corners]

    def corner_points(cx, cy, start, end):
        pts = []
        for i in range(1, samples + 1):
            t = start + (end - start) * (i / samples)
            ct = math.cos(t)
            st = math.sin(t)
            x = cx + rad * (1 if ct >= 0 else -1) * (abs(ct) ** power)
            y = cy + rad * (1 if st >= 0 else -1) * (abs(st) ** power)
            pts.append((x, y))
        return pts

    path.moveTo(x0 + rad if tl else x0, y0)
    path.lineTo(x1 - rad if tr else x1, y0)
    if tr:
        for x, y in corner_points(x1 - rad, y0 + rad, -math.pi / 2, 0):
            path.lineTo(x, y)
    path.lineTo(x1, y1 - rad if br else y1)
    if br:
        for x, y in corner_points(x1 - rad, y1 - rad, 0, math.pi / 2):
            path.lineTo(x, y)
    path.lineTo(x0 + rad if bl else x0, y1)
    if bl:
        for x, y in corner_points(x0 + rad, y1 - rad, math.pi / 2, math.pi):
            path.lineTo(x, y)
    path.lineTo(x0, y0 + rad if tl else y0)
    if tl:
        for x, y in corner_points(x0 + rad, y0 + rad, math.pi, 3 * math.pi / 2):
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def l2_superellipse_path(rect: QRectF, radius: float | None = None, exponent: float = 5.2, samples: int = 28) -> QPainterPath:
    """Apple-style continuous rounded rectangle using true superellipse corner patches.

    Important: this is *not* a full-rectangle superellipse. A full superellipse makes
    wide controls look like distorted capsules. Apple-like continuous corners keep
    straight edges and replace only each corner with a superellipse quarter whose
    curvature tends to 0 at the join, producing a real C2/L2-continuous transition.
    """
    if getattr(globals().get("RUNTIME_THEME_PROFILE"), "corner_style", "continuous") == "square":
        path = QPainterPath()
        path.addRect(QRectF(rect))
        return path
    return l2_continuous_path(rect, radius=radius, exponent=exponent, samples=samples, corners=(True, True, True, True))



class L2Panel(QWidget):
    """Card drawn with true continuous corners, not CSS border-radius.

    Window shells also receive a real mask so child widgets cannot overpaint the
    lower corners into a square rectangle.  This fixes the missing bottom-radius
    look on frameless translucent windows.
    """
    def __init__(self, parent=None, fill=APP_PANEL, border=APP_BORDER, radius_hint=24):
        super().__init__(parent)
        self.fill = QColor(fill)
        self.border = QColor(border)
        self.radius_hint = radius_hint
        self.setAttribute(Qt.WA_StyledBackground, False)

    def _shape_path(self) -> QPainterPath:
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.radius_hint is not None and float(self.radius_hint) <= 0:
            path = QPainterPath()
            path.addRect(rect)
            return path
        return l2_superellipse_path(rect, radius=self.radius_hint)

    def _update_shell_mask(self):
        # Avoid QWidget/QRegion shell masks. QRegion is a hard 1-bit mask and
        # makes the otherwise antialiased L2 shell edge look jagged on Windows.
        # The shell itself paints the antialiased shape; child widgets are kept
        # opaque only inside the content area, so a hard clipping mask is not
        # needed and can cause visible 毛刺.
        try:
            self.clearMask()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_shell_mask()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        path = self._shape_path()
        profile = globals().get("RUNTIME_THEME_PROFILE")
        painter.fillPath(path, self.fill)
        painter.setPen(QPen(self.border, 1.0))
        painter.drawPath(path)


class L2Button(QPushButton):
    """Compact button with true continuous-corner painting."""
    def __init__(self, text="", parent=None, variant="default", align=None):
        super().__init__(text, parent)
        self.variant = variant
        self.align = align or Qt.AlignCenter
        self.outer_fill = None
        self.radius_hint = None
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setFlat(True)
        # Hard reset native/stylesheet button metrics.  Global QPushButton QSS can
        # otherwise add a pressed inset or leave a clipped straight-edge artifact
        # around custom painted L2 buttons.
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; margin: 0px; }")

    def _outer_background(self) -> QColor:
        if self.outer_fill is not None:
            return QColor(self.outer_fill)
        try:
            parent = self.parentWidget()
            if isinstance(parent, L2Panel):
                return QColor(parent.fill)
            name = parent.objectName() if parent is not None else ""
            if name in ("ToolbarCard", "ProgressCard"):
                return QColor(APP_PANEL)
            if name in ("MainTitleBar", "DetailTitleBar", "MainBody"):
                return QColor(APP_BG)
        except Exception:
            pass
        return QColor(APP_BG)

    def set_variant(self, variant: str):
        self.variant = variant
        self.update()

    def set_outer_fill(self, color: str | QColor | None):
        self.outer_fill = QColor(color) if color is not None else None
        self.update()

    def set_radius_hint(self, radius: float | None):
        self.radius_hint = radius
        self.update()

    def _palette(self):
        profile = globals().get("RUNTIME_THEME_PROFILE")
        if not self.isEnabled():
            return QColor(profile.gray_1), QColor(profile.gray_3), QColor(profile.gray_6)
        down = self.isDown()
        hover = self.underMouse()
        control_style = profile.control_style
        if control_style in {"win2000", "macos8"}:
            if self.variant in ("accent", "detail_index"):
                return QColor(profile.accent), QColor(profile.accent), QColor("#FFFFFF")
            normal = "#EEEEEE" if control_style == "macos8" else "#D4D0C8"
            bg = profile.gray_2 if down else (profile.gray_1 if hover else normal)
            return QColor(bg), QColor(profile.border), QColor(profile.text)
        if control_style == "win7":
            if self.variant in ("accent", "detail_index"):
                bg = "#1E5F9B" if down else ("#3C7FB1" if hover else profile.accent)
                return QColor(bg), QColor("#174D7D"), QColor("#FFFFFF")
            bg = "#D7E8F4" if down else ("#EAF6FD" if hover else "#F6FAFD")
            fg = profile.accent if self.variant in ("source", "primary") else profile.text
            return QColor(bg), QColor("#7F9DB9"), QColor(fg)
        if control_style == "win11":
            if self.variant in ("accent", "detail_index"):
                bg = profile.accent_dark if down else profile.accent
                return QColor(bg), QColor(bg), QColor("#FFFFFF")
            bg = profile.gray_2 if down else (profile.gray_1 if hover else profile.panel)
            fg = profile.accent if self.variant in ("source", "primary") else profile.text
            return QColor(bg), QColor(profile.border), QColor(fg)
        if self.variant == "accent":
            bg = "#0062CC" if down else ("#087FF5" if hover else "#007AFF")
            return QColor(bg), QColor(bg), QColor("#FFFFFF")
        if self.variant == "toolbar":
            if down:
                return QColor("#DCDCE0"), QColor("#DCDCE0"), QColor(APP_TEXT)
            if hover:
                return QColor("#EEEEF0"), QColor("#EEEEF0"), QColor(APP_TEXT)
            return QColor("#FFFFFF"), QColor("#FFFFFF"), QColor("#3A3A3C")
        if self.variant in ("source", "primary"):
            bg = "#E5E5EA" if down else ("#F0F0F2" if hover else "#FFFFFF")
            border = "#B8B8BD" if hover or down else "#D1D1D6"
            return QColor(bg), QColor(border), QColor("#007AFF")
        if self.variant == "detail":
            bg = "#CBD5E1" if down else ("#D6DEE8" if hover else "#E7ECF2")
            border = "#AAB7C7" if hover or down else "#C0CAD8"
            return QColor(bg), QColor(border), QColor("#20242B")
        if self.variant == "detail_index":
            bg = "#2C7BEA" if not down else "#216BD1"
            return QColor(bg), QColor(bg), QColor("#FFFFFF")
        if self.variant == "live_on":
            bg = "#5F6F83" if down else ("#6B7A8C" if hover else "#758294")
            border = "#536477" if hover or down else "#637285"
            return QColor(bg), QColor(border), QColor("#F6F8FA")
        if self.variant == "live_off":
            bg = "#C6D1DD" if down else ("#D0DAE5" if hover else "#D9E1EA")
            border = "#A9B6C5" if hover or down else "#B8C4D2"
            return QColor(bg), QColor(border), QColor("#2D3742")
        if self.variant == "nav":
            bg = "#D1DAE5" if down else ("#DCE4EE" if hover else "#E7ECF3")
            border = "#AEBACA" if hover or down else "#C0CAD8"
            return QColor(bg), QColor(border), QColor("#20242B")
        bg = "#E1E1E5" if down else ("#EEEEF0" if hover else "#FFFFFF")
        border = "#B8B8BD" if hover or down else "#D1D1D6"
        return QColor(bg), QColor(border), QColor("#1D1D1F")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        bg, border, fg = self._palette()
        # Always paint an opaque outer rectangle first.  Transparent corners on
        # child widgets are the main cause of the apparent edge jitter / 毛刺 when
        # neighbouring controls or progress updates repaint asynchronously.
        painter.fillRect(self.rect(), self._outer_background())
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = self.radius_hint if self.radius_hint is not None else (22 if self.variant == "source" else (20 if self.variant == "nav" else 16))
        path = l2_superellipse_path(rect, radius=radius, samples=72)
        profile = globals().get("RUNTIME_THEME_PROFILE")
        if profile.control_style == "win7" and self.variant not in ("accent", "detail_index"):
            gradient = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            gradient.setColorAt(0.0, QColor("#FFFFFF"))
            gradient.setColorAt(0.48, bg.lighter(104))
            gradient.setColorAt(0.52, bg)
            gradient.setColorAt(1.0, bg.darker(105))
            painter.fillPath(path, gradient)
        else:
            painter.fillPath(path, bg)
        pen = QPen(border, 1.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        if profile.uses_bevels:
            dark_edge = "#333333" if profile.control_style == "macos8" else "#404040"
            bevel_light = QColor(dark_edge if self.isDown() else "#FFFFFF")
            bevel_dark = QColor("#FFFFFF" if self.isDown() else dark_edge)
            edge = self.rect().adjusted(1, 1, -2, -2)
            painter.setPen(QPen(bevel_light, 1))
            painter.drawLine(edge.topLeft(), edge.topRight())
            painter.drawLine(edge.topLeft(), edge.bottomLeft())
            painter.setPen(QPen(bevel_dark, 1))
            painter.drawLine(edge.bottomLeft(), edge.bottomRight())
            painter.drawLine(edge.topRight(), edge.bottomRight())
        painter.setPen(fg)
        text = self.text()
        icon = self.icon()
        has_icon = not icon.isNull()
        icon_size = self.iconSize()
        if icon_size.isEmpty():
            icon_size = QSize(20, 20)
        side_padding = 16 if self.variant == "source" else 12
        content_rect = QRectF(self.rect()).adjusted(side_padding, 0, -side_padding, 0)
        text_width = painter.fontMetrics().horizontalAdvance(text) if text else 0
        icon_width = icon_size.width() if has_icon else 0
        spacing = 8 if has_icon and text else 0
        content_width = icon_width + spacing + text_width

        if self.align & Qt.AlignLeft:
            x = content_rect.left()
        elif self.align & Qt.AlignRight:
            x = content_rect.right() - content_width
        else:
            x = content_rect.left() + max(0.0, (content_rect.width() - content_width) / 2.0)

        if has_icon:
            if not self.isEnabled():
                mode = QIcon.Disabled
            elif self.underMouse() or self.isDown():
                mode = QIcon.Active
            else:
                mode = QIcon.Normal
            state = QIcon.On if self.isCheckable() and self.isChecked() else QIcon.Off
            pixmap = icon.pixmap(icon_size, mode, state)
            icon_rect = QRectF(
                x,
                content_rect.center().y() - icon_size.height() / 2.0,
                icon_size.width(),
                icon_size.height(),
            )
            painter.drawPixmap(
                icon_rect,
                pixmap,
                QRectF(0, 0, pixmap.width(), pixmap.height()),
            )
            x += icon_width + spacing

        if text:
            text_rect = QRectF(
                x,
                self.rect().top(),
                max(0.0, content_rect.right() - x),
                self.rect().height(),
            )
            painter.drawText(text_rect, (self.align & Qt.AlignVertical_Mask) | Qt.AlignLeft | Qt.TextSingleLine, text)


class L2SidebarButton(QPushButton):
    """Sidebar row with the same C2/L2 continuous corner geometry as the cards."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0px; margin: 0px; }")

    def sizeHint(self) -> QSize:
        icon_w = self.iconSize().width() if not self.icon().isNull() else 0
        spacing = 9 if icon_w else 0
        return QSize(
            24 + icon_w + spacing + self.fontMetrics().horizontalAdvance(self.text()) + 18,
            30,
        )

    def minimumSizeHint(self) -> QSize:
        return QSize(min(140, self.sizeHint().width()), 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.fillRect(self.rect(), QColor(SIDEBAR_BG))

        checked = self.isChecked()
        hover = self.underMouse()
        if checked or hover:
            rect = QRectF(self.rect()).adjusted(0.5, 1.0, -0.5, -1.0)
            path = l2_superellipse_path(rect, radius=8, samples=48)
            painter.fillPath(path, QColor(ACCENT_BLUE if checked else SYSTEM_GRAY_1))

        font = self.font()
        font.setWeight(QFont.DemiBold if checked else QFont.Medium)
        painter.setFont(font)
        fg = QColor("#FFFFFF" if checked else APP_TEXT)
        painter.setPen(fg)

        icon = self.icon()
        icon_size = self.iconSize()
        if icon_size.isEmpty():
            icon_size = QSize(20, 20)
        x = 14.0
        if not icon.isNull():
            mode = QIcon.Selected if checked else (QIcon.Active if hover else QIcon.Normal)
            pixmap = icon.pixmap(icon_size, mode, QIcon.Off)
            icon_rect = QRectF(
                x,
                self.rect().center().y() - icon_size.height() / 2.0,
                icon_size.width(),
                icon_size.height(),
            )
            painter.drawPixmap(
                icon_rect,
                pixmap,
                QRectF(0, 0, pixmap.width(), pixmap.height()),
            )
            x += icon_size.width() + 9.0

        painter.drawText(
            QRectF(x, 0, max(0.0, self.width() - x - 10.0), self.height()),
            Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine,
            self.text(),
        )


class L2LineEdit(QLineEdit):
    """Line edit whose frame is painted with the shared L2 continuous corner path."""

    def __init__(self, parent=None, *, fill=CONTENT_BG, border=SYSTEM_GRAY_3, focus_border=ACCENT_BLUE, radius_hint=17, leading_icon: str | None = None):
        super().__init__(parent)
        self.fill = QColor(fill)
        self.border = QColor(border)
        self.focus_border = QColor(focus_border)
        self.radius_hint = radius_hint
        self.leading_icon = leading_icon
        self.setMinimumHeight(36)
        self.setFrame(False)
        self.setTextMargins(38 if leading_icon else 13, 0, 13, 0)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; padding: 0px; color: {APP_TEXT}; selection-background-color: {ACCENT_BLUE}; }}"
            f"QLineEdit::placeholder {{ color: {SYSTEM_GRAY_6}; }}"
        )

    def _outer_background(self) -> QColor:
        try:
            parent = self.parentWidget()
            if isinstance(parent, L2Panel):
                return QColor(parent.fill)
            if parent is not None and parent.objectName() in ("ToolbarCard", "MainSearchCard", "ProgressCard"):
                return QColor(APP_PANEL)
        except Exception:
            pass
        return QColor(CONTENT_BG)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self._outer_background())
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = l2_superellipse_path(rect, radius=self.radius_hint, samples=64)
        painter.fillPath(path, self.fill)
        painter.setPen(QPen(self.focus_border if self.hasFocus() else self.border, 1.0))
        painter.drawPath(path)
        if self.leading_icon:
            icon = render_ui_icon_pixmap(self.leading_icon, SYSTEM_GRAY_6, 17)
            painter.drawPixmap(
                QRectF(13, (self.height() - 17) / 2.0, 17, 17),
                icon,
                QRectF(0, 0, icon.width(), icon.height()),
            )
        super().paintEvent(event)


class L2CheckBox(QCheckBox):
    """Checkbox indicator painted as a small continuous-corner token."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("QCheckBox { background: transparent; border: none; padding: 0px; margin: 0px; }")
        self.setMinimumWidth(self.sizeHint().width())

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(27 + fm.horizontalAdvance(self.text()) + 14, 30)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _outer_background(self) -> QColor:
        try:
            parent = self.parentWidget()
            if isinstance(parent, L2Panel):
                return QColor(parent.fill)
        except Exception:
            pass
        return QColor(APP_PANEL)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.fillRect(self.rect(), self._outer_background())

        indicator = QRectF(0, 0, 20, 20)
        indicator.moveLeft(0)
        indicator.moveTop((self.height() - indicator.height()) / 2.0)
        path = l2_superellipse_path(indicator.adjusted(1, 1, -1, -1), radius=7, samples=40)
        checked = self.isChecked()
        hover = self.underMouse()
        profile = globals().get("RUNTIME_THEME_PROFILE")
        historical = profile.control_style in {"win7", "win2000", "macos8"}
        bg = QColor(CONTENT_BG if historical else (ACCENT_BLUE if checked else (SYSTEM_GRAY_1 if hover else CONTENT_BG)))
        border = QColor(("#555555" if historical else ACCENT_BLUE) if checked else (SYSTEM_GRAY_4 if hover else SYSTEM_GRAY_3))
        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.0))
        painter.drawPath(path)

        if checked:
            pen = QPen(QColor(ACCENT_BLUE if historical else "#FFFFFF"), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPoint(6, int(indicator.center().y())), QPoint(9, int(indicator.center().y() + 3)))
            painter.drawLine(QPoint(9, int(indicator.center().y() + 3)), QPoint(15, int(indicator.center().y() - 4)))

        painter.setPen(QColor(APP_TEXT))
        font = self.font()
        font.setWeight(QFont.Medium)
        painter.setFont(font)
        painter.drawText(
            QRectF(27, 0, max(0, self.width() - 37), self.height()),
            Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine,
            self.text(),
        )


class L2SegmentedControl(QWidget):
    """Compact macOS-style segmented control with an L2 selected surface."""

    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[str] = []
        self._current_index = -1
        self._hover_index = -1
        self.setFixedHeight(34)
        self.setMinimumWidth(164)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def addItems(self, items):
        self._items.extend(str(item) for item in items)
        if self._current_index < 0 and self._items:
            self._current_index = 0
        self.updateGeometry()
        self.update()

    def setItems(self, items):
        """Replace labels without changing the selected segment."""
        current = self._current_index
        self._items = [str(item) for item in items]
        self._current_index = max(0, min(current, len(self._items) - 1)) if self._items else -1
        self.updateGeometry()
        self.update()

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._current_index

    def currentText(self) -> str:
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index]
        return ""

    def setCurrentIndex(self, index: int):
        if not self._items:
            index = -1
        else:
            index = max(0, min(int(index), len(self._items) - 1))
        if index == self._current_index:
            return
        self._current_index = index
        self.update()
        self.currentIndexChanged.emit(index)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        width = 28 + sum(max(64, fm.horizontalAdvance(text) + 28) for text in self._items)
        return QSize(max(164, width), 34)

    def _index_at(self, x: float) -> int:
        if not self._items or self.width() <= 0:
            return -1
        return max(0, min(len(self._items) - 1, int(x / (self.width() / len(self._items)))))

    def mouseMoveEvent(self, event):
        try:
            x = event.position().x()
        except Exception:
            x = event.pos().x()
        index = self._index_at(x)
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                x = event.position().x()
            except Exception:
                x = event.pos().x()
            self.setCurrentIndex(self._index_at(x))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.setCurrentIndex(self._current_index - 1)
            event.accept()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.setCurrentIndex(self._current_index + 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        event.ignore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.fillRect(self.rect(), QColor(APP_PANEL))
        profile = globals().get("RUNTIME_THEME_PROFILE")
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        outer_path = l2_superellipse_path(rect, radius=12, samples=56)
        painter.setPen(QPen(QColor(APP_BORDER), 1.0))
        painter.setBrush(QColor(APP_PANEL_2))
        painter.drawPath(outer_path)

        count = len(self._items)
        if count <= 0:
            return
        segment_w = rect.width() / count
        for index, text in enumerate(self._items):
            segment = QRectF(rect.left() + segment_w * index, rect.top(), segment_w, rect.height())
            if index == self._current_index:
                selected = segment.adjusted(2.5, 2.5, -2.5, -2.5)
                selected_path = l2_superellipse_path(selected, radius=10, samples=48)
                painter.setPen(QPen(QColor(APP_BORDER), 0.8))
                selected_fill = profile.accent if profile.uses_bevels else ("#D8EAF5" if profile.control_style == "win7" else APP_PANEL)
                painter.setBrush(QColor(selected_fill))
                painter.drawPath(selected_path)
            elif index == self._hover_index:
                hover = segment.adjusted(3.0, 3.0, -3.0, -3.0)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 120))
                painter.drawPath(l2_superellipse_path(hover, radius=9, samples=40))

            font = self.font()
            font.setWeight(QFont.DemiBold if index == self._current_index else QFont.Medium)
            painter.setFont(font)
            selected_text = "#FFFFFF" if profile.uses_bevels and index == self._current_index else APP_TEXT
            painter.setPen(QColor(selected_text if index == self._current_index else APP_MUTED))
            painter.drawText(segment, Qt.AlignCenter | Qt.TextSingleLine, text)


class L2CategoryTreeDelegate(QStyledItemDelegate):
    """Paint smart-category rows with L2 continuous selection/hover backgrounds."""

    def paint(self, painter: QPainter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.State_Selected)
        hover = bool(opt.state & QStyle.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        if selected or hover:
            row_rect = QRectF(opt.rect).adjusted(0.0, 1.0, -0.5, -1.0)
            if opt.widget is not None and index.column() == 0:
                try:
                    row_rect.setRight(opt.widget.viewport().width() - 1.0)
                except Exception:
                    pass
            if index.column() == 0:
                bg_path = l2_superellipse_path(row_rect, radius=8, samples=48)
                painter.fillPath(bg_path, QColor(ACCENT_BLUE if selected else SYSTEM_GRAY_1))

        text_color = QColor("#FFFFFF" if selected else (APP_TEXT if index.column() == 0 else SYSTEM_GRAY_6))
        painter.setPen(text_color)
        font = opt.font
        if selected and index.column() == 0:
            font.setWeight(QFont.DemiBold)
        painter.setFont(font)

        if index.column() == 0:
            x = opt.rect.left() + 4
            icon = opt.icon
            icon_size = opt.decorationSize if opt.decorationSize.isValid() else QSize(20, 20)
            if not icon.isNull():
                pixmap = icon.pixmap(icon_size, QIcon.Selected if selected else QIcon.Normal, QIcon.Off)
                icon_rect = QRectF(
                    x,
                    opt.rect.center().y() - icon_size.height() / 2.0,
                    icon_size.width(),
                    icon_size.height(),
                )
                painter.drawPixmap(icon_rect, pixmap, QRectF(0, 0, pixmap.width(), pixmap.height()))
                x += icon_size.width() + 8
            painter.drawText(
                QRectF(x, opt.rect.top(), max(0, opt.rect.right() - x - 4), opt.rect.height()),
                Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine,
                opt.text,
            )
        else:
            painter.drawText(
                QRectF(opt.rect).adjusted(0, 0, -8, 0),
                Qt.AlignVCenter | Qt.AlignRight | Qt.TextSingleLine,
                opt.text,
            )
        painter.restore()


class L2Label(QLabel):
    """QLabel with true L2 continuous background instead of stylesheet radius."""
    def __init__(self, text: str = "", parent=None, *, fill="#D8E0E9", border="#B8C4D2", radius_hint=18, padding=(11, 7, 11, 7), outer_fill=None):
        super().__init__(text, parent)
        self.fill = QColor(fill)
        self.border = QColor(border)
        self.outer_fill = QColor(outer_fill) if outer_fill is not None else None
        self.radius_hint = radius_hint
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setContentsMargins(*padding)
        self.setMinimumHeight(34)

    def _outer_background(self) -> QColor:
        if self.outer_fill is not None:
            return QColor(self.outer_fill)
        try:
            parent = self.parentWidget()
            if isinstance(parent, L2Panel):
                return QColor(parent.fill)
        except Exception:
            pass
        return QColor(APP_BG)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.fillRect(self.rect(), self._outer_background())
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = l2_superellipse_path(rect, radius=self.radius_hint, samples=72)
        painter.fillPath(path, self.fill)
        pen = QPen(self.border, 1.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        super().paintEvent(event)


class StableProgressLabel(QWidget):
    """Progress text with a frozen prefix pixmap and fixed paint coordinates.

    The prefix such as “缩略图：” is rendered into a cached QPixmap only when the
    prefix/font/device-pixel-ratio changes. Counter updates repaint only the
    numeric value area. This avoids QLabel relayout and repeated CJK glyph
    rasterization, which showed up as tiny prefix wobble on Windows.
    """
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._display_text = ""
        self._prefix = ""
        self._value = ""
        self._prefix_pixmap = QPixmap()
        self._prefix_w = 0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setMinimumSize(140, 20)
        self.setMaximumHeight(20)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setText(text)

    def setFont(self, font: QFont):
        super().setFont(font)
        self._rebuild_prefix_pixmap()
        self.update()

    def _parse_text(self, text: str):
        text = str(text)
        if "：" in text:
            a, b = text.split("：", 1)
            return a + "：", b.strip()
        if ":" in text:
            a, b = text.split(":", 1)
            return a + ":", b.strip()
        return text, ""

    def _outer_background(self) -> QColor:
        try:
            parent = self.parentWidget()
            while parent is not None:
                if isinstance(parent, L2Panel):
                    return QColor(parent.fill)
                parent = parent.parentWidget()
        except Exception:
            pass
        return QColor(APP_PANEL)

    def _rebuild_prefix_pixmap(self):
        fm = self.fontMetrics()
        self._prefix_w = max(fm.horizontalAdvance("缩略图："), fm.horizontalAdvance("扫描：")) + 6
        dpr = 1.0
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            pass
        pm = QPixmap(max(1, int(round(self._prefix_w * dpr))), max(1, int(round(self.height() * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(self._outer_background())
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(QColor("#5E6673"))
        painter.drawText(QRectF(0, 0, self._prefix_w, self.height()), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, self._prefix)
        painter.end()
        self._prefix_pixmap = pm

    def setText(self, text: str):
        text = str(text)
        if text == self._display_text:
            return
        prefix, value = self._parse_text(text)
        old_value = self._value
        prefix_changed = prefix != self._prefix
        self._display_text = text
        self._prefix = prefix
        self._value = value
        if prefix_changed or self._prefix_pixmap.isNull():
            self._rebuild_prefix_pixmap()
            self.update()
        elif value != old_value:
            self.update(QRect(max(0, self._prefix_w), 0, max(1, self.width() - self._prefix_w), self.height()))

    def text(self) -> str:
        return self._display_text

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._outer_background())
        if not self._prefix_pixmap.isNull():
            painter.drawPixmap(0, 0, self._prefix_pixmap)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(QColor("#5E6673"))
        painter.drawText(QRectF(self._prefix_w, 0, self.width() - self._prefix_w, self.height()), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, self._value)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_prefix_pixmap()

class FramelessResizeHandle(QWidget):
    """Invisible edge/corner handle for reliable frameless resizing on Windows.

    Native WM_NCHITTEST is kept below as a fallback, but these actual child
    handles make resizing work even when Qt/Windows does not deliver the native
    hit-test message for translucent frameless windows.
    """
    EDGE_CURSORS = {
        "left": Qt.SizeHorCursor,
        "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor,
        "bottom": Qt.SizeVerCursor,
        "top_left": Qt.SizeFDiagCursor,
        "bottom_right": Qt.SizeFDiagCursor,
        "top_right": Qt.SizeBDiagCursor,
        "bottom_left": Qt.SizeBDiagCursor,
    }

    def __init__(self, target: QWidget, edge: str, thickness: int = 10):
        super().__init__(target)
        self.target = target
        self.edge = edge
        self.thickness = thickness
        self._resizing = False
        self._start_global = QPoint(0, 0)
        self._start_geom = None
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self.setCursor(self.EDGE_CURSORS.get(edge, Qt.ArrowCursor))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.target.isMaximized():
            self._resizing = True
            try:
                self._start_global = event.globalPosition().toPoint()
            except Exception:
                self._start_global = event.globalPos()
            self._start_geom = self.target.geometry()
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._start_geom is not None:
            try:
                gp = event.globalPosition().toPoint()
            except Exception:
                gp = event.globalPos()
            dx = gp.x() - self._start_global.x()
            dy = gp.y() - self._start_global.y()
            g = self._start_geom
            x, y, w, h = g.x(), g.y(), g.width(), g.height()
            min_w = max(760, self.target.minimumWidth())
            min_h = max(520, self.target.minimumHeight())
            if "left" in self.edge:
                nx = x + dx
                nw = w - dx
                if nw < min_w:
                    nx = x + w - min_w
                    nw = min_w
                x, w = nx, nw
            if "right" in self.edge:
                w = max(min_w, w + dx)
            if "top" in self.edge:
                ny = y + dy
                nh = h - dy
                if nh < min_h:
                    ny = y + h - min_h
                    nh = min_h
                y, h = ny, nh
            if "bottom" in self.edge:
                h = max(min_h, h + dy)
            self.target.setGeometry(int(x), int(y), int(w), int(h))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            event.accept()
            return
        super().mouseReleaseEvent(event)


def install_frameless_resize_handles(target: QWidget, thickness: int = 22):
    """Disabled: do not create invisible child resize handles.

    v2/v3 added transparent QWidget handles along the window edge.  They made
    resizing reliable, but on some Windows / DPI combinations they could behave
    like hidden internal control points and visually disturb the layout.  The app
    now relies only on native WM_NCHITTEST hit testing, so there are no invisible
    widgets inside the client area.
    """
    old = getattr(target, "_frameless_resize_handles", [])
    for h in list(old or []):
        try:
            h.hide()
            h.deleteLater()
        except Exception:
            pass
    target._frameless_resize_handles = []


def update_frameless_resize_handles(target: QWidget):
    """No-op: hidden resize widgets are intentionally disabled."""
    handles = getattr(target, "_frameless_resize_handles", [])
    if handles:
        for h in list(handles):
            try:
                h.hide()
            except Exception:
                pass
        target._frameless_resize_handles = []




def draw_frameless_window_shadow(window: QWidget, painter: QPainter, shell: QWidget | None, *, radius: int = 28):
    """Paint a stable soft shadow at the top-level window layer.

    This replaces QGraphicsDropShadowEffect on the UI shell.  Graphics effects on
    a large widget subtree are visually unstable on some Windows/DPI setups
    because Qt repeatedly rasterizes the entire subtree into an intermediate
    pixmap.  Painting only the shadow here leaves child widgets in the normal
    QWidget paint path, so clicking one tile cannot make unrelated labels/buttons
    jitter.
    """
    try:
        if shell is None or not shell.isVisible() or window.isMaximized() or getattr(window, "_live_resizing", False):
            return
        rect = QRectF(shell.geometry())
        if rect.isEmpty():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Low-alpha concentric strokes/filled paths produce an even shadow
        # without an off-screen effect cache.  The layers are symmetric.
        layers = [
            (14, 10), (11, 12), (8, 14), (5, 16), (3, 18), (1, 20)
        ]
        for spread, alpha in layers:
            r = rect.adjusted(-spread, -spread, spread, spread)
            path = l2_superellipse_path(r, radius=radius + spread, samples=60)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawPath(path)
        painter.restore()
    except Exception:
        pass



    except Exception:
        pass


def mark_frameless_resize_activity(window: QWidget, delay_ms: int = 180):
    """Temporarily switch translucent frameless windows to a stable resize paint.

    During live resizing, Windows repeatedly asks the translucent top-level
    window to repaint.  Drawing a soft alpha shadow on every intermediate size
    can flicker.  While the user is actively resizing, paint an opaque APP_BG
    safety mat and skip the soft shadow; restore the shadow shortly after the
    resize settles.
    """
    try:
        window._live_resizing = True
        timer = getattr(window, "_live_resize_settle_timer", None)
        if timer is None:
            timer = QTimer(window)
            timer.setSingleShot(True)
            def _finish(w=window):
                try:
                    w._live_resizing = False
                    w.update()
                except Exception:
                    pass
            timer.timeout.connect(_finish)
            window._live_resize_settle_timer = timer
        timer.start(delay_ms)
    except Exception:
        pass


def frameless_edge_at_global(window: QWidget, shell: QWidget | None, global_pos: QPoint):
    """Return edge names near the visible shell border, not the transparent margin."""
    try:
        if shell is None or not shell.isVisible() or window.isMaximized():
            return None
        p = shell.mapFromGlobal(global_pos)
        r = shell.rect()
        x, y = int(p.x()), int(p.y())
        band_in = 4
        band_out = 6
        left = -band_out <= x <= band_in
        right = r.width() - 1 - band_in <= x <= r.width() - 1 + band_out
        top = -band_out <= y <= band_in
        bottom = r.height() - 1 - band_in <= y <= r.height() - 1 + band_out
        if top and left:
            return "top_left"
        if top and right:
            return "top_right"
        if bottom and left:
            return "bottom_left"
        if bottom and right:
            return "bottom_right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
    except Exception:
        return None
    return None


def apply_manual_frameless_resize(window: QWidget, edge: str, start_geom, dx: int, dy: int):
    """Manual fallback resize without invisible child handles."""
    try:
        x, y, w, h = start_geom.x(), start_geom.y(), start_geom.width(), start_geom.height()
        min_w = max(760, int(window.minimumWidth() or 0))
        min_h = max(520, int(window.minimumHeight() or 0))
        if "left" in edge:
            nx = x + dx
            nw = w - dx
            if nw < min_w:
                nx = x + w - min_w
                nw = min_w
            x, w = nx, nw
        if "right" in edge:
            w = max(min_w, w + dx)
        if "top" in edge:
            ny = y + dy
            nh = h - dy
            if nh < min_h:
                ny = y + h - min_h
                nh = min_h
            y, h = ny, nh
        if "bottom" in edge:
            h = max(min_h, h + dy)
        window.setGeometry(int(x), int(y), int(w), int(h))
    except Exception:
        pass

def sync_frameless_shell_state(window: QWidget, outer_layout: QVBoxLayout, shell: L2Panel, shadow, normal_margin: int, normal_radius: int = 28):
    """Remove shadow padding when maximized and restore it when normal."""
    maximized = window.isMaximized()
    margin = 0 if maximized else int(normal_margin)
    outer_layout.setContentsMargins(margin, margin, margin, margin)
    shell.radius_hint = 0 if maximized else normal_radius
    if not hasattr(shell, "_normal_border_color"):
        shell._normal_border_color = QColor(shell.border)
    # A one-pixel inner outline reads as an outer gap at maximized size.
    shell.border = QColor(APP_BG) if maximized else QColor(shell._normal_border_color)
    if maximized:
        try:
            shell.clearMask()
        except Exception:
            pass
    else:
        try:
            shell._update_shell_mask()
        except Exception:
            pass
    shell.update()
    try:
        # Shadows are top-level painted, not QGraphicsEffect-based.
        if shadow is not None:
            shadow.setEnabled(False)
    except Exception:
        pass
    try:
        window.update()
    except Exception:
        pass
    update_frameless_resize_handles(window)


class ModernComboPopupItem(QWidget):
    clicked = Signal(int)

    def __init__(self, text: str, index: int, combo: "ModernComboBox", parent=None):
        super().__init__(parent)
        self.text = text
        self.index = index
        self.combo = combo
        self._hover = False
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        try:
            self.setAttribute(Qt.WA_OpaquePaintEvent, False)
            self.setAutoFillBackground(False)
        except Exception:
            pass

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        selected = self.index == self.combo.currentIndex()
        rect = QRectF(self.rect()).adjusted(6.0, 3.0, -6.0, -3.0)
        if selected or self._hover:
            path = l2_superellipse_path(rect, radius=13, samples=24)
            painter.fillPath(path, QColor(ACCENT_BLUE if selected else SYSTEM_GRAY_1))
        font = self.font()
        if selected:
            font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF" if selected else APP_TEXT))
        painter.drawText(self.rect().adjusted(18, 0, -14, 0), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, self.text)


class ModernComboPopupWindow(QWidget):
    closed = Signal()

    def __init__(self, combo: "ModernComboBox"):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.combo = combo
        # Use antialiased alpha instead of a binary QRegion mask.  The mask fixed
        # the old black frame, but it produced毛刺 on some Windows/DPI setups.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._sheet = QWidget(self)
        self._sheet.setAttribute(Qt.WA_StyledBackground, False)
        self._layout.addWidget(self._sheet)
        self._items_layout = QVBoxLayout(self._sheet)
        self._items_layout.setContentsMargins(6, 6, 6, 6)
        self._items_layout.setSpacing(0)
        self._items: list[ModernComboPopupItem] = []

    def rebuild_items(self):
        while self._items_layout.count():
            item = self._items_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._items = []
        for i in range(self.combo.count()):
            row = ModernComboPopupItem(self.combo.itemText(i), i, self.combo, self._sheet)
            row.clicked.connect(self._choose)
            self._items.append(row)
            self._items_layout.addWidget(row)

    def _choose(self, index: int):
        self.combo.setCurrentIndex(index)
        self.combo.hidePopup()

    def _popup_path(self):
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        return l2_continuous_path(rect, radius=18, samples=28, corners=(False, False, True, True))

    def _update_popup_mask(self):
        # No binary mask: it aliases the continuous corner.  The popup paints its
        # own antialiased alpha surface.
        try:
            self.clearMask()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_popup_mask()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        except Exception:
            pass
        path = self._popup_path()
        painter.fillPath(path, QColor(APP_PANEL))
        painter.setPen(QPen(QColor(APP_BORDER), 1.0))
        painter.drawPath(path)
        # A shared seam color makes the popup and trigger read as one fused unit.
        painter.drawLine(1, 0, max(1, self.width() - 2), 0)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


class ModernComboBox(QComboBox):
    """Modern dropdown with a real custom popup, aligned to the trigger width."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_open = False
        self._popup_window: Optional[ModernComboPopupWindow] = None
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setMaximumHeight(36)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setStyleSheet(
            "QComboBox { background: transparent; border: none; padding: 0px; color: #20242B; font-weight: 650; }"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
        )

    def _ensure_popup(self):
        if self._popup_window is None:
            self._popup_window = ModernComboPopupWindow(self)
            self._popup_window.closed.connect(self._on_popup_closed)
        self._popup_window.rebuild_items()
        return self._popup_window

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._popup_window is not None and self._popup_window.isVisible():
                self.hidePopup()
            else:
                self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # The popup is handled on press; accepting release prevents the native
        # QComboBox state machine from adding a small press-time layout jump.
        if event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showPopup(self):
        popup = self._ensure_popup()
        popup_w = int(self.width())
        item_h = 38
        popup_h = max(1, self.count()) * item_h + 12
        pos = self.mapToGlobal(QPoint(0, self.height() - 1))
        self._popup_open = True
        self.update()
        popup.setFixedWidth(popup_w)
        popup.setGeometry(pos.x(), pos.y(), popup_w, popup_h)
        try:
            popup._update_popup_mask()
        except Exception:
            pass
        popup.show()
        popup.raise_()

    def hidePopup(self):
        if self._popup_window is not None and self._popup_window.isVisible():
            self._popup_window.hide()
        self._on_popup_closed()

    def _on_popup_closed(self):
        if self._popup_open:
            self._popup_open = False
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        down = self._popup_open
        hover = self.underMouse()
        bg = QColor(SYSTEM_GRAY_2 if down else (SYSTEM_GRAY_1 if hover else APP_PANEL_2))
        border = QColor(SYSTEM_GRAY_4 if down or hover else APP_BORDER)
        painter.fillRect(self.rect(), QColor(APP_PANEL))
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = l2_continuous_path(rect, radius=17, samples=48, corners=(True, True, not down, not down))
        painter.fillPath(path, bg)
        border_pen = QPen(border, 1.0)
        border_pen.setJoinStyle(Qt.RoundJoin)
        border_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(border_pen)
        painter.drawPath(path)
        painter.setPen(QColor(APP_TEXT))
        painter.drawText(self.rect().adjusted(12, 0, -35, 0), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine, self.currentText())
        cx = self.width() - 18
        cy = self.height() / 2 + 1
        pen = QPen(QColor(APP_MUTED), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(QPoint(cx - 5, int(cy - 3)), QPoint(cx, int(cy + 2)))
        painter.drawLine(QPoint(cx, int(cy + 2)), QPoint(cx + 5, int(cy - 3)))


class L2ProgressBar(QProgressBar):
    """Thin progress bar with stable opaque repainting."""
    def __init__(self, parent=None, variant="scan"):
        super().__init__(parent)
        self.variant = variant
        self._phase = 0.0
        self.setTextVisible(False)
        self.setMinimumHeight(8)
        self.setMaximumHeight(8)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def apply_theme(self, profile=None):
        profile = profile or RUNTIME_THEME_PROFILE
        height = 16 if profile.control_style in {"win2000", "macos8"} else (12 if profile.control_style == "win7" else 8)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.updateGeometry()
        self.update()

    def _tick(self):
        if self.minimum() == self.maximum():
            self._phase = (self._phase + 0.035) % 1.0
            self.update()

    def _gradient(self, rect):
        grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        grad.setColorAt(0.0, QColor(ACCENT_BLUE))
        grad.setColorAt(0.72, QColor(ACCENT_BLUE_DARK))
        grad.setColorAt(1.0, QColor(ACCENT_BLUE).lighter(135))
        return grad

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(APP_PANEL))
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(0.0, 1.0, 0.0, -1.0)
        path = l2_superellipse_path(rect, radius=rect.height() / 2.0, samples=48)
        painter.fillPath(path, QColor(SYSTEM_GRAY_2))
        painter.save()
        painter.setClipPath(path)
        if self.minimum() == self.maximum():
            chunk_w = max(48.0, rect.width() * 0.28)
            x = rect.left() - chunk_w + (rect.width() + chunk_w * 2) * self._phase
            chunk_rect = QRectF(x, rect.top(), chunk_w, rect.height())
            painter.fillRect(chunk_rect, self._gradient(rect))
        else:
            den = max(1, self.maximum() - self.minimum())
            frac = max(0.0, min(1.0, (self.value() - self.minimum()) / den))
            if frac > 0:
                chunk_rect = QRectF(rect.left(), rect.top(), rect.width() * frac, rect.height())
                painter.fillRect(chunk_rect, self._gradient(rect))
        painter.restore()
        if RUNTIME_THEME_PROFILE.uses_bevels:
            painter.setPen(QPen(QColor("#555555"), 1))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.drawLine(1, 1, self.width() - 2, 1)



class LiveRelocationTargetDialog(QDialog):
    """Select the still photo that will receive/swap the current Live Photo MOV.

    The dialog deliberately uses the same virtual table model style as the main
    window, but it owns only its visible-id list. No selection state from the
    main window is reused, so choosing a target cannot accidentally operate on
    the caller's current multi-selection.
    """
    def __init__(self, owner: "PhotoMoverQt", source_item_id: str, candidate_ids: list[str], parent=None):
        super().__init__(parent or owner)
        self.owner = owner
        self.source_item_id = source_item_id
        self.candidate_ids = [iid for iid in candidate_ids if iid and iid != source_item_id and iid in owner.item_map]
        self.filtered_ids: list[str] = list(self.candidate_ids)
        self.selected_item_id: str = ""
        self.setWindowTitle("选择目标照片 - 实况重定位")
        self.resize(980, 680)
        self.setStyleSheet(
            f"QDialog {{ background: {APP_BG}; }}"
            "QLineEdit { background: #E7EDF4; border: 1px solid #B8C4D2; border-radius: 13px; padding: 8px 12px; color: #20242B; }"
            "QPushButton { padding: 8px 15px; border-radius: 10px; background: #E7EDF4; border: 1px solid #B8C4D2; color: #20242B; }"
            "QPushButton:hover { background: #EDF2F7; }"
            "QPushButton:disabled { color: #8B95A3; background: #D7DFE8; }"
            "QTableView { background: #DDE3EA; gridline-color: transparent; border: none; outline: 0; }"
            "QHeaderView::section { background: #D4DEE8; color: #4B5663; font-weight: 650; padding: 8px 10px; border: none; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        src_item = owner.item_map.get(source_item_id)
        hint = QLabel(self)
        if src_item is not None:
            hint.setText(
                f"将 “{src_item.display_name}” 的 MOV 重定位到目标照片。"
                "目标为普通照片时会转移 MOV；目标为实况照片时会交换双方 MOV。照片本体文件名不会改变。"
            )
        else:
            hint.setText("请选择目标照片。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #3D4652; font-size: 13px;")
        layout.addWidget(hint)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索文件名、类型、文件夹路径……")
        layout.addWidget(self.search_edit)

        self.table = QTableView(self)
        self.model = PhotoTableModel(owner)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(TABLE_ICON_SIZE, TABLE_ICON_SIZE))
        self.table.setItemDelegate(PhotoTableDelegate(TABLE_ICON_SIZE, TABLE_ROW_HEIGHT, self.table))
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setMinimumSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(True)
        for c in range(6):
            header.setSectionResizeMode(c, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 270)
        self.table.setColumnWidth(1, 155)
        self.table.setColumnWidth(2, 155)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 170)
        self.table.setColumnWidth(5, 260)
        prepare_scroll_area(self.table)
        layout.addWidget(self.table, 1)

        self.detail_label = QLabel("未选择目标照片。", self)
        self.detail_label.setStyleSheet("color: #5F6875; font-size: 12px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("取消", self)
        self.ok_btn = QPushButton("确定重定位", self)
        self.ok_btn.setEnabled(False)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.search_edit.textChanged.connect(self.apply_filter)
        self.table.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.table.doubleClicked.connect(lambda _idx: self.accept_if_valid())
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept_if_valid)
        self.apply_filter()

    def apply_filter(self):
        text = self.search_edit.text().strip().lower()
        if not text:
            ids = list(self.candidate_ids)
        else:
            ids = []
            for iid in self.candidate_ids:
                item = self.owner.item_map.get(iid)
                if item is None:
                    continue
                hay = " ".join([
                    item.display_name,
                    item.item_type,
                    item.time_source,
                    str(item.source_folder),
                ]).lower()
                if text in hay:
                    ids.append(iid)
        self.filtered_ids = ids
        self.model.set_visible_ids(ids)
        self.selected_item_id = ""
        self.ok_btn.setEnabled(False)
        self.detail_label.setText(f"当前候选目标：{len(ids)} 项。请选择一张目标照片。")

    def on_selection_changed(self, *_args):
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            self.selected_item_id = ""
            self.ok_btn.setEnabled(False)
            self.detail_label.setText("未选择目标照片。")
            return
        row = indexes[0].row()
        if 0 <= row < len(self.model.visible_ids):
            iid = self.model.visible_ids[row]
            item = self.owner.item_map.get(iid)
            if item is not None:
                self.selected_item_id = iid
                self.ok_btn.setEnabled(True)
                action = "交换双方 MOV" if item.is_live else "把当前 MOV 转移到此照片"
                self.detail_label.setText(f"目标：{item.display_name} ｜ {item.item_type} ｜ 将执行：{action}")
                return
        self.selected_item_id = ""
        self.ok_btn.setEnabled(False)
        self.detail_label.setText("未选择目标照片。")

    def accept_if_valid(self):
        if self.selected_item_id and self.selected_item_id in self.owner.item_map:
            self.accept()


class ReorderExportPreviewOwner(QObject):
    """Small, read-only owner object used by the reorder-export preview views.

    The main photo wall/table delegates were designed to read thumbnails and item
    metadata through ``model.window``.  The export preview should look like the
    main view, but it must not mutate the real main-window selection, filtering,
    trash state, LIVE hover state or item map.  This adapter exposes only the
    small read-only surface that PhotoGridModel / PhotoTableModel need.
    """
    def __init__(self, source_window: "PhotoMoverQt", preview_items: list[PhotoItemData], visible_ids: list[str], parent=None):
        super().__init__(parent)
        self.source_window = source_window
        self.item_map: dict[str, PhotoItemData] = {item.item_id: item for item in preview_items}
        self.visible_ids = list(visible_ids)
        self.visible_row_by_id = {iid: i for i, iid in enumerate(self.visible_ids)}
        self.icon_cache = source_window.icon_cache
        self.live_frame_cache = source_window.live_frame_cache
        self.live_preview_item_id = None
        self.live_preview_frame_index = 0
        self.placeholder_icon = source_window.placeholder_icon
        self.placeholder_live_icon = source_window.placeholder_live_icon

    def icon_for_item(self, item: PhotoItemData):
        # The preview items keep the original item_id, so they can reuse the real
        # thumbnail cache without copying QPixmap objects.  If a thumbnail has not
        # been decoded yet, show the same placeholder as the main window.
        try:
            if item.item_id in self.icon_cache:
                return self.icon_cache.get(item.item_id)
            if item.item_id in self.live_frame_cache:
                frames = self.live_frame_cache.get(item.item_id) or []
                if frames:
                    return frames[0]
        except Exception:
            pass
        return self.placeholder_live_icon if item.is_live else self.placeholder_icon

    def quick_tooltip_for_item(self, item: PhotoItemData) -> str:
        return (
            f"输出名：{item.display_name}\n"
            f"原文件：{getattr(item, 'preview_original_name', item.display_name)}\n"
            f"类型：{item.item_type}\n"
            f"时间：{format_time(item.shot_time)}"
        )

    def tooltip_for_item(self, item: PhotoItemData) -> str:
        output_files = list(getattr(item, "preview_output_files", []) or [])
        output_text = "\n".join(str(x) for x in output_files[:8])
        if len(output_files) > 8:
            output_text += "\n……"
        return (
            f"输出名：{item.display_name}\n"
            f"原文件：{getattr(item, 'preview_original_name', item.display_name)}\n"
            f"类型：{item.item_type}\n"
            f"时间：{format_time(item.shot_time)}\n"
            f"输出位置：{getattr(item, 'preview_output_dir', '')}\n"
            f"输出文件数：{len(output_files)}\n\n"
            f"输出文件：\n{output_text}"
        )


class ReorderExportGridModel(PhotoGridModel):
    """Grid model for the export preview.

    It inherits the same roles as the main grid model so the existing photo-wall
    delegate can draw LIVE / 待绑定 badges exactly the same way.
    """
    pass


class ReorderExportTableModel(PhotoTableModel):
    """Table model for the export preview.

    The visual delegate is the same as the main table, but the columns describe
    the *future export sequence* instead of the current source folder.
    """
    HEADERS = ["输出文件", "时间", "类型", "文件数", "输出目录", "原文件"]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self.visible_ids):
            return None
        item_id = self.visible_ids[row]
        item = self.window.item_map.get(item_id)
        if item is None:
            return None
        if role == ITEM_ID_ROLE:
            return item_id
        if role == IS_LIVE_ROLE:
            return item.is_live
        if role == NEEDS_BINDING_ROLE:
            return bool(getattr(item, "needs_binding", False) or getattr(item, "item_kind", "photo") == "mov_only")
        if role == THUMB_READY_ROLE:
            return (item_id in self.window.icon_cache) or (item_id in self.window.live_frame_cache)
        if role == Qt.ToolTipRole:
            original = getattr(item, "preview_original_name", item.display_name)
            output_files = getattr(item, "preview_output_files", []) or []
            output_text = "\n".join(str(x) for x in output_files[:6])
            if len(output_files) > 6:
                output_text += "\n……"
            return (
                f"输出名：{item.display_name}\n"
                f"原文件：{original}\n"
                f"类型：{item.item_type}\n"
                f"时间：{format_time(item.shot_time)}\n"
                f"输出位置：{getattr(item, 'preview_output_dir', '')}\n"
                f"输出文件：\n{output_text}"
            )
        if role == Qt.DecorationRole and col == 0:
            return self.window.icon_for_item(item)
        if role == Qt.DisplayRole:
            if col == 0:
                return item.display_name
            if col == 1:
                return format_time(item.shot_time)
            if col == 2:
                return item.item_type
            if col == 3:
                return str(len(getattr(item, "preview_output_files", item.files) or []))
            if col == 4:
                return str(getattr(item, "preview_output_dir", ""))
            if col == 5:
                return str(getattr(item, "preview_original_name", ""))
        return None


class ReorderDropIndicator(QWidget):
    """Bright insertion cursor used by the reorder-export preview drag/drop UI."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.orientation = "vertical"
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.hide()

    def set_indicator(self, orientation: str, rect: QRect):
        self.orientation = orientation
        self.setGeometry(rect)
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if r.width() <= 0 or r.height() <= 0:
            return
        glow = QColor(0, 122, 255, 76)
        core = QColor(0, 122, 255, 235)
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(r, min(10.0, r.width() / 2.0), min(10.0, r.height() / 2.0))
        if self.orientation == "horizontal":
            c = QRectF(r.left() + 4, r.center().y() - 2, max(1.0, r.width() - 8), 4)
            painter.setBrush(core)
            painter.drawRoundedRect(c, 2, 2)
            # small end caps make the line easier to see on table rows
            cap = 8.0
            painter.drawEllipse(QRectF(r.left() + 2, r.center().y() - cap / 2, cap, cap))
            painter.drawEllipse(QRectF(r.right() - cap - 2, r.center().y() - cap / 2, cap, cap))
        else:
            c = QRectF(r.center().x() - 2, r.top() + 4, 4, max(1.0, r.height() - 8))
            painter.setBrush(core)
            painter.drawRoundedRect(c, 2, 2)
            cap = 8.0
            painter.drawEllipse(QRectF(r.center().x() - cap / 2, r.top() + 2, cap, cap))
            painter.drawEllipse(QRectF(r.center().x() - cap / 2, r.bottom() - cap - 2, cap, cap))


class ReorderExportPreviewDialog(QDialog):
    """Confirm the iOS-like sequential export plan with real photo-wall/list views."""
    def __init__(self, plan: list[dict], target_dir: Path, parent: "PhotoMoverQt" = None):
        super().__init__(parent)
        self.plan = plan
        self.target_dir = target_dir
        self.owner_window = parent
        self.setWindowTitle("批量重排导出")
        # The reorder-export window is still used as a confirmation dialog, but
        # it must not prevent child preview windows from receiving input.  Use a
        # window-modal dialog instead of an application-modal one and open large
        # image previews as children of this dialog, not as children of the
        # disabled main window.
        try:
            self.setWindowModality(Qt.WindowModal)
        except Exception:
            pass
        self.resize(1220, 780)
        self.setStyleSheet(
            f"QDialog {{ background: {APP_BG}; color: {APP_TEXT}; }}"
            "QPushButton { padding: 8px 15px; border-radius: 10px; background: #E7EDF4; border: 1px solid #B8C4D2; color: #20242B; }"
            "QPushButton:hover { background: #EDF2F7; }"
            "QLabel#PreviewTitle { font-size: 18px; font-weight: 760; color: #111319; }"
            "QLabel#PreviewHint { color: #3D4652; font-size: 13px; }"
            "QLabel#PreviewStat { color: #4B5663; font-size: 12px; }"
        )

        self.preview_items, self.preview_ids = self._build_preview_items(plan)
        self.preview_owner = ReorderExportPreviewOwner(parent, self.preview_items, self.preview_ids, self)
        # Delegate resources and owner-side mutating operations to the real main
        # window while keeping preview-detail windows parented to this dialog.
        # This avoids the v51 bug where the modal reorder dialog disabled the
        # main window, so a detail dialog parented to the main window was shown
        # but could not be interacted with.
        self.detail_executor = getattr(parent, "detail_executor", None)
        self.detail_live_executor = getattr(parent, "detail_live_executor", None)
        self.detail_windows: set[ImageDetailDialog] = set()
        self._reorder_drag_view = None
        self._reorder_drag_press_pos = None
        self._reorder_drag_active = False
        self._reorder_drag_source_ids: list[str] = []
        self._reorder_drag_insert_index: int | None = None
        self._reorder_drag_last_pos = QPoint()
        self._reorder_drag_source_view_name = ""
        self._reorder_drop_indicator: ReorderDropIndicator | None = None
        self._preview_order_dirty = False
        self._plan_rebuild_in_progress = False
        self._reorder_autoscroll_timer = QTimer(self)
        self._reorder_autoscroll_timer.setInterval(32)
        self._reorder_autoscroll_timer.timeout.connect(self._auto_scroll_reorder_drag)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("确认批量重排导出", self)
        title.setObjectName("PreviewTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        view_label = QLabel("视图", self)
        view_label.setStyleSheet("color: #4B5663;")
        title_row.addWidget(view_label)
        self.view_combo = ModernComboBox(self)
        self.view_combo.addItems(["照片墙", "列表"])
        self.view_combo.setMinimumWidth(112)
        title_row.addWidget(self.view_combo)
        layout.addLayout(title_row)

        total_files = sum(len(entry.get("file_pairs", [])) for entry in plan)
        hint = QLabel(
            f"将把选区按当前展示顺序整理成新的序列，共 {len(plan)} 项、{total_files} 个文件。\n"
            f"目标：{target_dir}\n"
            "命名采用 iPhone/DCIM 风格：DCIM/100APPLE/IMG_0001…IMG_9999；超过 9999 后进入 101APPLE 并从 IMG_0001 继续。"
            "Live Photo 的静态图与 MOV 使用同一编号。请在下方像主界面一样确认新序列，再开始复制。",
            self,
        )
        hint.setObjectName("PreviewHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        stat = QLabel("提示：这是导出后的预览序列，原文件不会移动、不会改名；确认后才会复制到目标文件夹。", self)
        stat.setObjectName("PreviewStat")
        layout.addWidget(stat)
        self.reorder_status_label = stat

        reorder_hint = QLabel("顺序调整：选中一个或一批项目后，不按 Ctrl，直接按住选区内任意瓦片/行拖到目标缝隙；出现蓝色插入光标后松手完成重排。Ctrl 只用于继续选择。", self)
        reorder_hint.setStyleSheet("color: #4B5663; font-size: 12px;")
        reorder_hint.setWordWrap(True)
        layout.addWidget(reorder_hint)

        search_card = L2Panel(self, fill="#D6DEE7", border=APP_BORDER)
        search_card.setObjectName("ReorderSearchCard")
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(12, 9, 12, 9)
        search_layout.setSpacing(7)
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_label = QLabel("搜索", search_card)
        search_label.setObjectName("TinyToolbarLabel")
        self.preview_search_edit = QLineEdit(search_card)
        self.preview_search_edit.setPlaceholderText("按新序列搜索：支持 *.HEIC、IMG_12??、*.MOV;*.HEIC。点击结果会定位并选中。")
        self.preview_search_edit.setClearButtonEnabled(True)
        self.preview_search_edit.setStyleSheet("QLineEdit { background: #E7EDF4; border: 1px solid #B8C4D2; border-radius: 12px; padding: 7px 11px; color: #20242B; }")
        self.preview_search_status = QLabel("未搜索", search_card)
        self.preview_search_status.setObjectName("SearchStatus")
        self.preview_search_status.setStyleSheet("color: #5F6875; font-size: 12px;")
        search_row.addWidget(search_label)
        search_row.addWidget(self.preview_search_edit, 1)
        search_row.addWidget(self.preview_search_status)
        search_layout.addLayout(search_row)
        self.preview_search_model = SearchResultsModel(self)
        self.preview_search_results = QTableView(search_card)
        self.preview_search_results.setModel(self.preview_search_model)
        self.preview_search_results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_search_results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.preview_search_results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_search_results.verticalHeader().setVisible(False)
        self.preview_search_results.setShowGrid(False)
        self.preview_search_results.setMaximumHeight(150)
        self.preview_search_results.setVisible(False)
        self.preview_search_results.setStyleSheet("QTableView { background: #E7EDF4; border: 1px solid #B8C4D2; border-radius: 10px; outline: 0; } QHeaderView::section { background: #D4DEE8; color: #4B5663; padding: 6px 8px; border: none; }")
        prepare_scroll_area(self.preview_search_results)
        try:
            self.preview_search_results.horizontalHeader().setStretchLastSection(True)
            self.preview_search_results.setColumnWidth(0, 260)
            self.preview_search_results.setColumnWidth(1, 145)
        except Exception:
            pass
        search_layout.addWidget(self.preview_search_results)
        layout.addWidget(search_card)

        self.stack = QStackedWidget(self)
        self.stack.setObjectName("ReorderPreviewStack")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.stack.setStyleSheet(f"#ReorderPreviewStack {{ background: {APP_BG}; border: none; }}")
        layout.addWidget(self.stack, 1)

        self.grid = PhotoGridView(self)
        self.grid_model = ReorderExportGridModel(self.preview_owner)
        self.grid.setModel(self.grid_model)
        self.grid_model.set_visible_ids(self.preview_ids)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setFocusPolicy(Qt.StrongFocus)
        try:
            self.grid.set_selection_mode_enabled(True)
        except Exception:
            pass
        self.grid.range_dragged.connect(lambda _a, _b, finished: finished and self._sync_selection_from_view(self.grid))
        self.grid.clear_selection_requested.connect(lambda: self._select_preview_ids([]))
        try:
            self.grid.selectionModel().selectionChanged.connect(lambda *_: self._sync_selection_from_view(self.grid))
        except Exception:
            pass
        self.stack.addWidget(self.grid)

        self.table = PhotoTableView(self)
        self.table.setHorizontalHeader(FadingHeaderView(Qt.Horizontal, self.table))
        self.table_model = ReorderExportTableModel(self.preview_owner)
        self.table.setModel(self.table_model)
        self.table_model.set_visible_ids(self.preview_ids)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(TABLE_ICON_SIZE, TABLE_ICON_SIZE))
        self.table.setItemDelegate(PhotoTableDelegate(TABLE_ICON_SIZE, TABLE_ROW_HEIGHT, self.table))
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setMinimumSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setStyleSheet(
            "QTableView { background: #DDE3EA; gridline-color: transparent; border: none; outline: 0; }"
            "QTableView::item { border: none; padding: 0px; background: transparent; }"
            "QTableView::item:selected { background: transparent; color: white; }"
            "QTableView::item:selected:!active { background: transparent; color: white; }"
            "QHeaderView { background: #D4DEE8; border: none; }"
            "QHeaderView::section { background: #D4DEE8; color: #4B5663; font-weight: 650; padding: 8px 10px; border: none; }"
        )
        prepare_scroll_area(self.table)
        try:
            table_pal = self.table.viewport().palette()
            table_pal.setColor(QPalette.Window, QColor("#DDE3EA"))
            table_pal.setColor(QPalette.Base, QColor("#DDE3EA"))
            self.table.viewport().setPalette(table_pal)
            self.table.viewport().setAutoFillBackground(True)
        except Exception:
            pass
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setMinimumHeight(40)
        header.setDefaultAlignment(Qt.AlignCenter)
        for c in range(6):
            header.setSectionResizeMode(c, QHeaderView.Fixed)
        try:
            self.table.selectionModel().selectionChanged.connect(lambda *_: self._sync_selection_from_view(self.table))
        except Exception:
            pass
        self.stack.addWidget(self.table)

        # Drag-to-reorder preview items.
        # Important interaction contract:
        #   * after the user has finished selecting, a plain left-press on any
        #     already-selected tile/row is reserved for reorder dragging;
        #   * Ctrl/Shift/Alt modified clicks are left to the normal selection
        #     logic, so Ctrl can keep extending/toggling the selection;
        #   * while this reorder candidate is pending, the underlying main-view
        #     drag-selection state machine must not receive press/move/release
        #     events, otherwise it can get stuck and keep changing selection even
        #     after the mouse button is released.
        for reorder_view in (self.grid, self.table):
            try:
                reorder_view.viewport().installEventFilter(self)
                reorder_view.setMouseTracking(True)
                reorder_view.viewport().setMouseTracking(True)
            except Exception:
                pass

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消", self)
        ok_btn = QPushButton("开始复制导出", self)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

        self.preview_search_edit.textChanged.connect(self.update_preview_search_results)
        self.preview_search_edit.returnPressed.connect(lambda: self.focus_first_preview_search_result())
        self.preview_search_results.clicked.connect(self.focus_preview_search_result)
        self.preview_search_results.doubleClicked.connect(self.focus_preview_search_result)
        self.view_combo.currentIndexChanged.connect(self.on_view_mode_changed)
        QTimer.singleShot(0, self.update_table_column_layout)
        QTimer.singleShot(0, self._warm_preview_thumbnails)

    def update_preview_search_results(self):
        query = self.preview_search_edit.text().strip() if hasattr(self, "preview_search_edit") else ""
        if not query:
            try:
                self.preview_search_model.set_results([])
                self.preview_search_results.setVisible(False)
                self.preview_search_status.setText("未搜索")
            except Exception:
                pass
            return
        results: list[dict] = []
        try:
            for row, iid in enumerate(self.preview_ids):
                item = self.preview_owner.item_map.get(iid)
                if item is None:
                    continue
                extra = list(getattr(item, "preview_output_files", []) or []) + [
                    str(getattr(item, "preview_output_dir", "") or ""),
                    str(getattr(item, "preview_source_folder", "") or ""),
                    str(getattr(item, "preview_original_name", "") or ""),
                ]
                if wildcard_query_matches(query, searchable_fields_for_item(item, extra)):
                    results.append({
                        "item_id": iid,
                        "name": item.display_name,
                        "type": item.item_type,
                        "location": str(getattr(item, "preview_output_dir", "") or item.source_folder),
                        "tooltip": f"输出：{item.display_name}\n原文件：{getattr(item, 'preview_original_name', '')}\n{getattr(item, 'preview_output_dir', '')}",
                    })
            self.preview_search_model.set_results(results)
            self.preview_search_results.setVisible(True)
            self.preview_search_status.setText(f"匹配 {len(results)} 项")
            try:
                self.preview_search_results.resizeRowsToContents()
            except Exception:
                pass
        except Exception as e:
            self.preview_search_model.set_results([])
            self.preview_search_results.setVisible(True)
            self.preview_search_status.setText(f"搜索失败：{e}")

    def focus_first_preview_search_result(self):
        try:
            if self.preview_search_model.rowCount() > 0:
                self.focus_preview_search_result(self.preview_search_model.index(0, 0))
        except Exception:
            pass

    def focus_preview_search_result(self, index):
        try:
            if not index.isValid():
                return
            iid = str(index.sibling(index.row(), 0).data(ITEM_ID_ROLE) or "")
            if not iid or iid not in self.preview_ids:
                return
            self._select_preview_ids([iid])
            row = self.preview_ids.index(iid)
            self._scroll_preview_row_to_center(row)
            item = self.preview_owner.item_map.get(iid)
            self._update_reorder_status(f"已定位搜索结果：{item.display_name if item else iid}")
        except Exception:
            pass

    def _scroll_preview_row_to_center(self, row: int):
        try:
            row = max(0, min(len(self.preview_ids) - 1, int(row)))
            for view in (self.grid, self.table):
                model = view.model()
                if model is None:
                    continue
                idx = model.index(row, 0)
                if idx.isValid():
                    view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                    view.viewport().update()
            active = self._active_preview_view()
            active.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _plan_matches_preview_order(self) -> bool:
        try:
            plan_ids = [str(entry.get("item_id") or "") for entry in self.plan]
            return plan_ids == list(self.preview_ids)
        except Exception:
            return False

    def accept(self):
        # Before returning to the main window, make absolutely sure the copy plan
        # matches the current drag-reordered preview order.  This protects the
        # final filenames even if the user confirms immediately after dropping,
        # and also guards against any future code path that changes preview_ids
        # without rebuilding the output plan.
        if getattr(self, "_preview_order_dirty", False) or not self._plan_matches_preview_order():
            if not self._rebuild_plan_after_order_change(self._selected_preview_ids_from_view()):
                return
        super().accept()

    def _rel_to_target(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.target_dir))
        except Exception:
            return str(path)

    def _preview_output_name(self, entry: dict) -> str:
        for pair in entry.get("file_pairs", []) or []:
            dst = Path(pair.get("dst") or "")
            if dst.suffix.lower() in IMAGE_EXTENSIONS:
                return dst.name
        for pair in entry.get("file_pairs", []) or []:
            dst = Path(pair.get("dst") or "")
            if dst.name:
                return dst.name
        return str(entry.get("new_base") or entry.get("display_name") or "未命名项目")

    def _build_preview_items(self, plan: list[dict]) -> tuple[list[PhotoItemData], list[str]]:
        preview_items: list[PhotoItemData] = []
        visible_ids: list[str] = []
        owner = self.owner_window
        for entry in plan:
            item_id = str(entry.get("item_id") or "")
            orig = owner.item_map.get(item_id) if owner is not None else None
            if orig is None:
                continue
            output_files = [self._rel_to_target(Path(pair.get("dst") or "")) for pair in entry.get("file_pairs", []) or []]
            output_dirs = []
            for pair in entry.get("file_pairs", []) or []:
                dst = Path(pair.get("dst") or "")
                if dst.parent:
                    output_dirs.append(self._rel_to_target(dst.parent))
            output_dir = output_dirs[0] if output_dirs else str(entry.get("source_folder") or "")
            clone = PhotoItemData(
                item_id=orig.item_id,
                display_name=self._preview_output_name(entry),
                files=list(orig.files),
                size_bytes=orig.size_bytes,
                representative_image=orig.representative_image,
                is_live=orig.is_live,
                item_type=orig.item_type,
                shot_time=orig.shot_time,
                time_source=orig.time_source,
                source_folder=Path(output_dir),
                stable_key=orig.stable_key,
                file_signature=orig.file_signature,
                meta_cached=orig.meta_cached,
                item_kind=orig.item_kind,
                bound_image_paths=list(getattr(orig, "bound_image_paths", []) or []),
                needs_binding=bool(getattr(orig, "needs_binding", False)),
            )
            setattr(clone, "preview_original_name", orig.display_name)
            setattr(clone, "preview_output_files", output_files)
            setattr(clone, "preview_output_dir", output_dir)
            setattr(clone, "preview_source_folder", str(orig.source_folder))
            preview_items.append(clone)
            visible_ids.append(clone.item_id)
        return preview_items, visible_ids

    def _warm_preview_thumbnails(self):
        # Ask the main window's priority thumbnail lane to decode missing thumbnails
        # without blocking the dialog opening.  This keeps the preview wall useful
        # even if the selection came from list view and thumbnails were not all ready.
        owner = self.owner_window
        if owner is None:
            return
        try:
            for iid in self.preview_ids[:240]:
                if iid not in owner.icon_cache and iid in owner.item_map:
                    owner.request_priority_thumbnail(owner.generation, owner.item_map[iid])
        except Exception:
            pass


    def eventFilter(self, obj, event):
        try:
            if obj in (self.grid.viewport(), self.table.viewport()):
                view = self.grid if obj is self.grid.viewport() else self.table
                et = event.type()
                if et == QEvent.ContextMenu:
                    pos = event.pos() if hasattr(event, "pos") else QPoint()
                    global_pos = event.globalPos() if hasattr(event, "globalPos") else view.viewport().mapToGlobal(pos)
                    self._show_preview_context_menu(view, pos, global_pos)
                    return True
                if et == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    row = self._row_at_preview_pos(view, pos)
                    if row is not None and 0 <= row < len(self.preview_ids):
                        self._open_preview_detail(self.preview_ids[row])
                        self._clear_reorder_drag_state()
                        self._reset_preview_view_mouse_state(view)
                        return True
                    return False
                if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    if self._begin_reorder_drag_candidate(view, pos, event):
                        # Reserve this gesture exclusively for reorder.  Do not let
                        # PhotoGridView / PhotoTableView also start its own range
                        # selection, otherwise the release may be swallowed by us
                        # later and their internal drag flags can remain dirty.
                        self._reset_preview_view_mouse_state(view)
                        return True
                    return False
                if et == QEvent.MouseMove:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    if self._reorder_drag_active:
                        self._update_reorder_drag(view, pos)
                        return True
                    if self._reorder_drag_press_pos is not None:
                        # While a reorder candidate is pending, mouse movement with
                        # no left button should never mutate the selection.  If the
                        # button disappeared because of an OS/Qt edge case, cancel
                        # the candidate and consume this event to keep the views in
                        # a clean state.
                        if not (event.buttons() & Qt.LeftButton):
                            self._clear_reorder_drag_state()
                            self._reset_preview_view_mouse_state(view)
                            return True
                        if (pos - self._reorder_drag_press_pos).manhattanLength() >= QApplication.startDragDistance():
                            if self._start_reorder_drag_if_valid(view, pos):
                                return True
                            self._clear_reorder_drag_state()
                            return True
                if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                    if self._reorder_drag_active:
                        self._finish_reorder_drag()
                        self._reset_preview_view_mouse_state(view)
                        return True
                    if self._reorder_drag_press_pos is not None:
                        # Plain click on an already selected tile/row means "keep
                        # this selection ready for dragging", not "replace the
                        # selection by this one item".
                        self._clear_reorder_drag_state()
                        self._reset_preview_view_mouse_state(view)
                        return True
                    self._clear_reorder_drag_state()
                if et in (QEvent.Leave, QEvent.Hide):
                    if not self._reorder_drag_active:
                        self._hide_reorder_indicator()
        except Exception:
            self._clear_reorder_drag_state()
            try:
                self._reset_preview_view_mouse_state(view)  # type: ignore[name-defined]
            except Exception:
                pass
        return super().eventFilter(obj, event)

    def _preview_item_id_at_pos(self, view, pos: QPoint) -> str | None:
        row = self._row_at_preview_pos(view, pos)
        if row is None or row < 0 or row >= len(self.preview_ids):
            return None
        return self.preview_ids[row]

    def _show_preview_context_menu(self, view, pos: QPoint, global_pos: QPoint):
        item_id = self._preview_item_id_at_pos(view, pos)
        if not item_id:
            return
        selected_ids = self._selected_preview_ids_from_view(view)
        if item_id not in set(selected_ids):
            selected_ids = [item_id]
            self._select_preview_ids(selected_ids)
        menu = QMenu(self)
        remove_label = "从重排列表中移除选中项" if len(selected_ids) > 1 else "从重排列表中移除"
        remove_action = menu.addAction(remove_label)
        meta_action = menu.addAction("查看元数据信息…")
        open_action = menu.addAction("打开大图预览")
        action = menu.exec(global_pos)
        if action == remove_action:
            self._remove_preview_ids_from_reorder(selected_ids)
        elif action == meta_action:
            self._show_preview_item_metadata(item_id)
        elif action == open_action:
            self._open_preview_detail(item_id)

    def _show_preview_item_metadata(self, item_id: str):
        owner = self.owner_window
        if owner is not None and hasattr(owner, "show_item_metadata") and item_id in getattr(owner, "item_map", {}):
            owner.show_item_metadata(item_id)
            return
        item = self.preview_owner.item_map.get(item_id) if self.preview_owner is not None else None
        if item is None:
            return
        plain_text = metadata_text_for_item(item)
        html_text = metadata_html_for_item(item)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"元数据信息 - {item.display_name}")
        dialog.resize(900, 760)
        dialog.setStyleSheet(
            "QDialog { background: #F5F5F7; }"
            "QPushButton { padding: 7px 14px; border-radius: 8px; background: #FFFFFF; border: 1px solid #D0D5DD; }"
            "QPushButton:hover { background: #F2F4F7; }"
        )
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html_text)
        prepare_scroll_area(browser)
        layout.addWidget(browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = QPushButton("复制全部信息", dialog)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(plain_text))
        close_btn = QPushButton("关闭", dialog)
        close_btn.clicked.connect(dialog.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)
        dialog.exec()

    def _open_preview_detail(self, item_id: str):
        owner = self.owner_window
        if owner is None:
            return
        try:
            ordered_ids = [iid for iid in self.preview_ids if iid in getattr(owner, "item_map", {})]
            if item_id not in ordered_ids:
                return
            item_snapshot = {iid: owner.item_map[iid] for iid in ordered_ids if iid in owner.item_map}
            # Parent the detail window to the currently active reorder dialog, not
            # to the main window.  When this dialog is running via exec(), the main
            # window is disabled by modality; a child preview parented to it will
            # appear to be "blocked" and cannot receive mouse/keyboard input.
            dlg = ImageDetailDialog(
                item_id,
                ordered_ids,
                item_snapshot,
                fallback_pixmaps=getattr(owner, "icon_cache", {}),
                parent=self,
                trash_context=bool(owner.is_trash_view()) if hasattr(owner, "is_trash_view") else False,
            )
            try:
                dlg.setAttribute(Qt.WA_DeleteOnClose, True)
                dlg.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            # Keep references both locally and in the real owner.  The local set
            # prevents garbage collection while the reorder dialog is open; the
            # owner set keeps cross-window file/trash notifications working.
            self.detail_windows.add(dlg)
            if hasattr(owner, "detail_windows"):
                try:
                    owner.detail_windows.add(dlg)
                except Exception:
                    pass
            dlg_ref = weakref.ref(dlg)
            def _forget_detail_window(*_args, ref=dlg_ref):
                obj = ref()
                if obj is not None:
                    try:
                        self.detail_windows.discard(obj)
                    except Exception:
                        pass
                    try:
                        if hasattr(owner, "detail_windows"):
                            owner.detail_windows.discard(obj)
                    except Exception:
                        pass
            try:
                dlg.destroyed.connect(_forget_detail_window)
            except Exception:
                pass
            dlg.show()
            try:
                dlg.raise_(); dlg.activateWindow()
            except Exception:
                pass
        except Exception as e:
            QMessageBox.warning(self, "打开预览失败", str(e))

    # ImageDetailDialog uses parent() as its logical owner for delete operations
    # and shared executors.  Since preview-detail windows are parented to the
    # modal reorder dialog for input reasons, delegate those owner operations
    # back to the real PhotoMoverQt instance.
    def is_trash_view(self) -> bool:
        try:
            return bool(self.owner_window.is_trash_view()) if self.owner_window is not None else False
        except Exception:
            return False

    def move_items_to_trash_by_ids(self, *args, **kwargs):
        if self.owner_window is not None and hasattr(self.owner_window, "move_items_to_trash_by_ids"):
            return self.owner_window.move_items_to_trash_by_ids(*args, **kwargs)
        return False

    def delete_items_to_deleted_folder_by_ids(self, *args, **kwargs):
        if self.owner_window is not None and hasattr(self.owner_window, "delete_items_to_deleted_folder_by_ids"):
            return self.owner_window.delete_items_to_deleted_folder_by_ids(*args, **kwargs)
        return False

    def _remove_preview_ids_from_reorder(self, ids: list[str] | set[str]):
        remove_set = {iid for iid in ids if iid in self.preview_ids}
        if not remove_set:
            return
        if len(remove_set) >= len(self.preview_ids):
            if QMessageBox.question(
                self, "确认移除",
                "这会从本次重排导出列表中移除全部项目，确认继续吗？\n\n原始文件不会被删除、移动或改名。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
        scroll_state = self._capture_preview_scroll_state()
        old_ids = list(self.preview_ids)
        first_removed_row = min((i for i, iid in enumerate(old_ids) if iid in remove_set), default=0)
        self.preview_ids = [iid for iid in old_ids if iid not in remove_set]
        if not self.preview_ids:
            self.plan = []
            self.preview_items = []
            self.preview_owner.item_map = {}
            self.preview_owner.visible_ids = []
            self.preview_owner.visible_row_by_id = {}
            self.grid_model.set_visible_ids([])
            self.table_model.set_visible_ids([])
            self.update_preview_search_results()
            self._invalidate_preview_tooltips_and_repaint()
            self._update_reorder_status("已从本次重排导出列表中移除全部项目。")
            return
        next_select = []
        if 0 <= first_removed_row < len(self.preview_ids):
            next_select = [self.preview_ids[first_removed_row]]
        elif self.preview_ids:
            next_select = [self.preview_ids[-1]]
        self.preview_owner.visible_ids = list(self.preview_ids)
        self.preview_owner.visible_row_by_id = {iid: i for i, iid in enumerate(self.preview_ids)}
        self.grid_model.set_visible_ids(self.preview_ids)
        self.table_model.set_visible_ids(self.preview_ids)
        self._preview_order_dirty = True
        self._update_reorder_status("已从重排列表中移除项目，正在刷新后续输出编号……")
        self._rebuild_plan_after_order_change(next_select, scroll_state=scroll_state)

    def _capture_preview_scroll_state(self) -> dict:
        state = {}
        for name, view in (("grid", getattr(self, "grid", None)), ("table", getattr(self, "table", None))):
            if view is None:
                continue
            try:
                state[name] = (
                    int(view.verticalScrollBar().value()),
                    int(view.horizontalScrollBar().value()),
                )
            except Exception:
                pass
        return state

    def _restore_preview_scroll_state(self, state: dict | None):
        if not state:
            return
        def _restore_once():
            for name, view in (("grid", getattr(self, "grid", None)), ("table", getattr(self, "table", None))):
                if view is None or name not in state:
                    continue
                try:
                    v, h = state[name]
                    vb = view.verticalScrollBar(); hb = view.horizontalScrollBar()
                    vb.setValue(max(vb.minimum(), min(vb.maximum(), int(v))))
                    hb.setValue(max(hb.minimum(), min(hb.maximum(), int(h))))
                except Exception:
                    pass
        _restore_once()
        QTimer.singleShot(0, _restore_once)
        QTimer.singleShot(35, _restore_once)

    def _view_name_for_reorder(self, view) -> str:
        return "table" if view is getattr(self, "table", None) else "grid"

    def _reset_preview_view_mouse_state(self, view):
        """Clear the inherited selection-drag state after we consumed a reorder gesture.

        The preview uses the real main PhotoGridView / PhotoTableView classes to
        keep visuals and selection behavior consistent.  Once the preview dialog
        reserves a gesture for drag-to-reorder, those views must not keep a stale
        internal left-drag/rubber-band state.  This defensive cleanup prevents
        the specific bug where merely moving the mouse after releasing the button
        continues changing the selection.
        """
        try:
            # PhotoGridView state
            for name, value in (
                ("_dragging", False),
                ("_drag_button", Qt.NoButton),
                ("_anchor_row", None),
                ("_blank_press_pos", None),
                ("_click_candidate_row", None),
                ("_press_pos", None),
            ):
                if hasattr(view, name):
                    setattr(view, name, value)
            # PhotoTableView state
            for name, value in (
                ("_table_dragging_select", False),
                ("_table_drag_button", Qt.NoButton),
                ("_table_drag_anchor_row", None),
                ("_click_candidate_row", None),
                ("_blank_press_pos", None),
                ("_press_pos", None),
            ):
                if hasattr(view, name):
                    setattr(view, name, value)
            try:
                view.clearFocus()
                view.setFocus(Qt.MouseFocusReason)
            except Exception:
                pass
        except Exception:
            pass

    def _is_valid_reorder_drop_pos(self, view, pos: QPoint) -> bool:
        try:
            vp = view.viewport().rect()
            # Keep a small vertical margin so autoscroll near top/bottom is usable,
            # but do not accept arbitrary releases outside the visible preview.  A
            # reorder only commits when an insertion cursor is actually shown.
            return (-8 <= pos.x() <= vp.width() + 8) and (-48 <= pos.y() <= vp.height() + 48)
        except Exception:
            return False

    def _begin_reorder_drag_candidate(self, view, pos: QPoint, event=None) -> bool:
        self._clear_reorder_drag_state(keep_indicator=False)
        try:
            modifiers = event.modifiers() if event is not None else QApplication.keyboardModifiers()
        except Exception:
            modifiers = QApplication.keyboardModifiers()
        # Ctrl/Shift/Alt modified gestures remain normal selection gestures.  In
        # particular, Ctrl is the user's "continue selecting / toggle selecting"
        # path and must never be hijacked by reorder dragging.
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        row = self._row_at_preview_pos(view, pos)
        if row is None or row < 0 or row >= len(self.preview_ids):
            return False
        iid = self.preview_ids[row]
        selected_ids = self._selected_preview_ids_from_view(view)
        selected_set = set(selected_ids)
        if iid not in selected_set:
            return False
        # Dragging one of the already-selected items moves the whole selected
        # sequence in its current preview order.
        self._reorder_drag_view = view
        self._reorder_drag_press_pos = QPoint(pos)
        self._reorder_drag_last_pos = QPoint(pos)
        self._reorder_drag_source_ids = [x for x in self.preview_ids if x in selected_set]
        self._reorder_drag_source_view_name = self._view_name_for_reorder(view)
        try:
            view.setCursor(Qt.OpenHandCursor)
        except Exception:
            pass
        return True

    def _start_reorder_drag_if_valid(self, view, pos: QPoint) -> bool:
        if self._reorder_drag_view is not view:
            return False
        if not self._reorder_drag_source_ids:
            return False
        if not self._is_valid_reorder_drop_pos(view, pos):
            return False
        self._reorder_drag_active = True
        self._reorder_drag_last_pos = QPoint(pos)
        try:
            view.setCursor(Qt.ClosedHandCursor)
        except Exception:
            pass
        self._update_reorder_drag(view, pos)
        if self._reorder_drag_insert_index is None:
            return False
        self._reorder_autoscroll_timer.start()
        self._update_reorder_status(f"拖动 {len(self._reorder_drag_source_ids)} 项到目标缝隙，松手完成重排；离开预览区域松手则取消。")
        return True

    def _clear_reorder_drag_state(self, keep_indicator: bool = False):
        try:
            if self._reorder_autoscroll_timer.isActive():
                self._reorder_autoscroll_timer.stop()
        except Exception:
            pass
        try:
            if self._reorder_drag_view is not None:
                self._reorder_drag_view.unsetCursor()
        except Exception:
            pass
        self._reorder_drag_view = None
        self._reorder_drag_press_pos = None
        self._reorder_drag_active = False
        self._reorder_drag_source_ids = []
        self._reorder_drag_insert_index = None
        self._reorder_drag_last_pos = QPoint()
        self._reorder_drag_source_view_name = ""
        if not keep_indicator:
            self._hide_reorder_indicator()

    def _hide_reorder_indicator(self):
        try:
            if self._reorder_drop_indicator is not None:
                self._reorder_drop_indicator.hide()
        except Exception:
            pass

    def _ensure_reorder_indicator(self, view) -> ReorderDropIndicator:
        viewport = view.viewport()
        if self._reorder_drop_indicator is None or self._reorder_drop_indicator.parent() is not viewport:
            try:
                if self._reorder_drop_indicator is not None:
                    self._reorder_drop_indicator.hide()
                    self._reorder_drop_indicator.setParent(None)
            except Exception:
                pass
            self._reorder_drop_indicator = ReorderDropIndicator(viewport)
        return self._reorder_drop_indicator

    def _row_at_preview_pos(self, view, pos: QPoint) -> int | None:
        try:
            if view is self.table:
                row = view.rowAt(pos.y())
                if row >= 0:
                    return row
                idx = view.indexAt(pos)
                return idx.row() if idx.isValid() else None
            idx = view.indexAt(pos)
            if idx.isValid():
                return idx.row()
            # QListView gaps are common in icon mode.  Probe nearby points first;
            # this avoids scanning every preview item on every mouse move.
            vp = view.viewport().rect()
            xs = [pos.x(), pos.x() - 18, pos.x() + 18, 8, max(8, vp.width() // 2), max(8, vp.width() - 8)]
            ys = [pos.y(), pos.y() - 18, pos.y() + 18, pos.y() - 48, pos.y() + 48]
            for y in ys:
                if y < -80 or y > vp.height() + 80:
                    continue
                for x in xs:
                    if x < -80 or x > vp.width() + 80:
                        continue
                    idx = view.indexAt(QPoint(int(x), int(y)))
                    if idx.isValid():
                        return idx.row()
        except Exception:
            pass
        return None

    def _drop_index_for_view_pos(self, view, pos: QPoint) -> int:
        n = len(self.preview_ids)
        if n <= 0:
            return 0
        try:
            vp = view.viewport().rect()
            if pos.y() < 0:
                return 0
            if pos.y() > vp.height():
                return n
            if view is self.table:
                row = view.rowAt(pos.y())
                if row < 0:
                    idx = view.indexAt(pos)
                    row = idx.row() if idx.isValid() else (-1)
                if row < 0:
                    return 0 if pos.y() < vp.center().y() else n
                rect = view.visualRect(view.model().index(row, 0))
                return max(0, min(n, row if pos.y() < rect.center().y() else row + 1))
            row = self._row_at_preview_pos(view, pos)
            if row is None:
                return 0 if pos.y() < vp.center().y() else n
            row = max(0, min(n - 1, row))
            rect = view.visualRect(view.model().index(row, 0))
            if not rect.isValid():
                return row
            # Same-row gaps insert before/after the nearest tile.  Vertical gaps
            # still use the closest tile found by probing, which feels natural in
            # the photo wall and is much cheaper than a full hit-test scan.
            return max(0, min(n, row if pos.x() < rect.center().x() else row + 1))
        except Exception:
            return n

    def _show_reorder_indicator(self, view, insert_index: int):
        try:
            indicator = self._ensure_reorder_indicator(view)
            vp = view.viewport().rect()
            n = len(self.preview_ids)
            insert_index = max(0, min(n, insert_index))
            if view is self.table:
                if n == 0:
                    y = 0
                elif insert_index >= n:
                    rect = view.visualRect(view.model().index(n - 1, 0))
                    y = rect.bottom() + 1 if rect.isValid() else vp.height() - 3
                else:
                    rect = view.visualRect(view.model().index(insert_index, 0))
                    y = rect.top() if rect.isValid() else 0
                indicator.set_indicator("horizontal", QRect(10, max(0, y - 4), max(24, vp.width() - 20), 9))
                return
            # photo wall: draw a vertical blue insertion caret in the target gap
            if n == 0:
                x, y, h = 8, 8, max(40, GRID_SIZE)
            elif insert_index >= n:
                rect = view.visualRect(view.model().index(n - 1, 0))
                if not rect.isValid():
                    self._hide_reorder_indicator(); return
                x, y, h = rect.right() + 3, rect.top(), rect.height()
                if x > vp.width() - 8:
                    x = max(8, vp.width() - 8)
            else:
                rect = view.visualRect(view.model().index(insert_index, 0))
                if not rect.isValid():
                    self._hide_reorder_indicator(); return
                x, y, h = rect.left() - 5, rect.top(), rect.height()
                if x < 2:
                    x = 2
            indicator.set_indicator("vertical", QRect(max(0, x - 4), max(0, y + 4), 11, max(30, h - 8)))
        except Exception:
            self._hide_reorder_indicator()

    def _update_reorder_drag(self, view, pos: QPoint):
        self._reorder_drag_view = view
        self._reorder_drag_last_pos = QPoint(pos)
        if not self._is_valid_reorder_drop_pos(view, pos):
            self._reorder_drag_insert_index = None
            self._hide_reorder_indicator()
            return
        insert_index = self._drop_index_for_view_pos(view, pos)
        self._reorder_drag_insert_index = insert_index
        self._show_reorder_indicator(view, insert_index)

    def _auto_scroll_reorder_drag(self):
        view = self._reorder_drag_view
        if not self._reorder_drag_active or view is None:
            return
        try:
            vp = view.viewport().rect()
            pos = self._reorder_drag_last_pos
            edge = 42
            speed = 0
            if pos.y() < edge:
                speed = -max(6, int((edge - pos.y()) * 0.45))
            elif pos.y() > vp.height() - edge:
                speed = max(6, int((pos.y() - (vp.height() - edge)) * 0.45))
            if speed:
                bar = view.verticalScrollBar()
                new_value = max(bar.minimum(), min(bar.maximum(), bar.value() + speed))
                if new_value != bar.value():
                    bar.setValue(new_value)
                    self._update_reorder_drag(view, pos)
        except Exception:
            pass

    def _finish_reorder_drag(self):
        try:
            source_ids = [iid for iid in self._reorder_drag_source_ids if iid in self.preview_ids]
            drop_index = self._reorder_drag_insert_index
            indicator_visible = bool(self._reorder_drop_indicator is not None and self._reorder_drop_indicator.isVisible())
            if not source_ids or drop_index is None or not indicator_visible:
                self._update_reorder_status("未在有效插入光标处松手，顺序未变化。")
                return
            current_ids = list(self.preview_ids)
            selected_set = set(source_ids)
            selected_rows = [i for i, iid in enumerate(current_ids) if iid in selected_set]
            if not selected_rows:
                return
            moving = [iid for iid in current_ids if iid in selected_set]
            remaining = [iid for iid in current_ids if iid not in selected_set]
            insert_at = max(0, min(len(current_ids), int(drop_index)))
            insert_at -= sum(1 for r in selected_rows if r < insert_at)
            insert_at = max(0, min(len(remaining), insert_at))
            new_ids = remaining[:insert_at] + moving + remaining[insert_at:]
            if new_ids == current_ids:
                self._update_reorder_status("顺序未变化。")
                return
            scroll_state = self._capture_preview_scroll_state()
            self.preview_ids = new_ids
            self.preview_owner.visible_ids = list(self.preview_ids)
            self.preview_owner.visible_row_by_id = {iid: i for i, iid in enumerate(self.preview_ids)}
            self.grid_model.set_visible_ids(self.preview_ids)
            self.table_model.set_visible_ids(self.preview_ids)
            self._select_preview_ids(moving)
            self._restore_preview_scroll_state(scroll_state)
            self._preview_order_dirty = True
            self._update_reorder_status("已按拖放位置重排，正在刷新输出编号……")
            self._rebuild_plan_after_order_change(list(moving), scroll_state=scroll_state)
        finally:
            self._clear_reorder_drag_state()

    def _active_preview_view(self):
        return self.grid if self.stack.currentIndex() == 0 else self.table

    def _selected_preview_ids_from_view(self, view=None) -> list[str]:
        view = view or self._active_preview_view()
        try:
            sm = view.selectionModel()
            if sm is None:
                return []
            rows = sorted({idx.row() for idx in sm.selectedRows() if idx.isValid()})
            out = []
            for row in rows:
                if 0 <= row < len(self.preview_ids):
                    out.append(self.preview_ids[row])
            return out
        except Exception:
            return []

    def _select_preview_ids(self, ids: list[str] | set[str]):
        wanted = [iid for iid in self.preview_ids if iid in set(ids)]
        for view in (self.grid, self.table):
            try:
                model = view.model()
                sm = view.selectionModel()
                if model is None or sm is None:
                    continue
                selection = QItemSelection()
                row_by_id = {iid: i for i, iid in enumerate(self.preview_ids)}
                max_col = 0
                try:
                    max_col = max(0, model.columnCount() - 1) if isinstance(model, QAbstractTableModel) else 0
                except Exception:
                    max_col = 0
                rows = [row_by_id[iid] for iid in wanted if iid in row_by_id]
                for a, b in compact_ranges(rows):
                    selection.select(model.index(a, 0), model.index(b, max_col))
                blocker = QSignalBlocker(sm)
                sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                del blocker
                try:
                    view._restore_selected_rows(set(rows))
                except Exception:
                    pass
                view.viewport().update()
            except Exception:
                pass
        self._update_reorder_status()

    def _sync_selection_from_view(self, source_view):
        ids = self._selected_preview_ids_from_view(source_view)
        self._select_preview_ids(ids)

    def _update_reorder_status(self, extra: str = ""):
        try:
            selected = len(self._selected_preview_ids_from_view())
            text = f"提示：这是导出后的预览序列，原文件不会移动、不会改名；当前序列 {len(self.preview_ids)} 项，已选择 {selected} 项。"
            if extra:
                text += " " + extra
            self.reorder_status_label.setText(text)
            if hasattr(self, "order_insert_spin"):
                self.order_insert_spin.setMaximum(max(1, len(self.preview_ids) + 1))
        except Exception:
            pass

    def _invalidate_preview_tooltips_and_repaint(self):
        """Clear id-based tooltip caches after output names are regenerated.

        Preview item ids deliberately stay equal to the original photo ids so
        thumbnails and selections can be reused.  The downside is that the
        cursor-following tooltip cache is also keyed by item id.  After a manual
        reorder, every item after the insertion point can receive a new IMG_####
        name, not only the dragged items, so all preview tooltip caches must be
        discarded together.
        """
        for view in (getattr(self, "grid", None), getattr(self, "table", None)):
            try:
                cache = getattr(view, "_tooltip_text_cache", None)
                if isinstance(cache, dict):
                    cache.clear()
                timer = getattr(view, "_full_tooltip_timer", None)
                if timer is not None:
                    timer.stop()
                if hasattr(view, "_tooltip_defer_timer"):
                    view._tooltip_defer_timer.stop()
                if hasattr(view, "_tooltip_gap_hide_timer"):
                    view._tooltip_gap_hide_timer.stop()
                view._pending_tooltip_id = None
                if hasattr(view, "_tooltip_pending_id"):
                    view._tooltip_pending_id = None
                tooltip = getattr(view, "_tooltip_widget", None)
                if tooltip is not None:
                    tooltip._last_text = None
                    tooltip.hide()
                view._hover_item_id = None
                view.viewport().update()
            except Exception:
                pass
        try:
            n = len(self.preview_ids)
            if n > 0:
                self.grid_model.dataChanged.emit(
                    self.grid_model.index(0, 0),
                    self.grid_model.index(n - 1, 0),
                    [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, ITEM_ID_ROLE, IS_LIVE_ROLE, NEEDS_BINDING_ROLE, THUMB_READY_ROLE],
                )
                self.table_model.dataChanged.emit(
                    self.table_model.index(0, 0),
                    self.table_model.index(n - 1, self.table_model.columnCount() - 1),
                    [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, ITEM_ID_ROLE, IS_LIVE_ROLE, NEEDS_BINDING_ROLE, THUMB_READY_ROLE],
                )
        except Exception:
            pass

    def _rebuild_plan_after_order_change(self, keep_selected: list[str] | None = None, scroll_state: dict | None = None) -> bool:
        """Rebuild export names from the *current preview order* immediately.

        The drag/drop preview first changes ``preview_ids`` for instant visual
        feedback.  The final copy operation must then use the same order to
        regenerate DCIM/IMG_0001... names.  This method is synchronous on purpose:
        it avoids a race where the user releases the mouse and clicks "开始复制导出"
        before a delayed timer has updated ``self.plan``.
        """
        owner = self.owner_window
        if owner is None:
            return False
        if self._plan_rebuild_in_progress:
            return False
        if scroll_state is None:
            scroll_state = self._capture_preview_scroll_state()
        self._plan_rebuild_in_progress = True
        try:
            # Keep only items that still exist in the real owner map.  This makes
            # the preview robust if the source list changed while the dialog is open.
            order_ids = [iid for iid in self.preview_ids if iid in owner.item_map]
            items = [owner.item_map[iid] for iid in order_ids]
            new_plan, problems = owner.build_reorder_export_plan(items, self.target_dir)
            self.plan = new_plan
            self.preview_items, self.preview_ids = self._build_preview_items(self.plan)
            self.preview_owner.item_map = {item.item_id: item for item in self.preview_items}
            self.preview_owner.visible_ids = list(self.preview_ids)
            self.preview_owner.visible_row_by_id = {iid: i for i, iid in enumerate(self.preview_ids)}
            self.grid_model.set_visible_ids(self.preview_ids)
            self.table_model.set_visible_ids(self.preview_ids)
            self.update_preview_search_results()
            self._invalidate_preview_tooltips_and_repaint()
            self._select_preview_ids(keep_selected or [])
            self._restore_preview_scroll_state(scroll_state)
            QTimer.singleShot(0, self.update_table_column_layout)
            self._preview_order_dirty = False
            # Force repaint of both preview modes after the global renumbering.
            # Dragging a block out/in changes the IMG_#### name of every item
            # behind the gap, not just the moved block.
            try:
                self.grid.viewport().update()
                self.table.viewport().update()
            except Exception:
                pass
            if problems:
                self._update_reorder_status(f"顺序已更新，输出文件名已重新编号；其中 {len(problems)} 条导出计划警告，请确认目标文件夹是否已有同名文件。")
            else:
                self._update_reorder_status("顺序已更新，输出文件名已重新编号。")
            return True
        except Exception as e:
            QMessageBox.warning(self, "顺序调整失败", str(e))
            return False
        finally:
            self._plan_rebuild_in_progress = False

    def move_selected_preview_items(self, mode: str, target_index: int | None = None):
        selected_ids = self._selected_preview_ids_from_view()
        if not selected_ids:
            QMessageBox.information(self, "提示", "请先在预览照片墙或列表中选择要调整顺序的项目。")
            return
        current_ids = list(self.preview_ids)
        selected_set = set(selected_ids)
        selected_rows = [i for i, iid in enumerate(current_ids) if iid in selected_set]
        if not selected_rows:
            return
        first = min(selected_rows)
        last = max(selected_rows)
        moving = [iid for iid in current_ids if iid in selected_set]
        remaining = [iid for iid in current_ids if iid not in selected_set]
        if mode == "top":
            insert_at = 0
        elif mode == "bottom":
            insert_at = len(remaining)
        elif mode == "up":
            if first <= 0:
                return
            insert_at = max(0, first - 1)
            insert_at -= sum(1 for r in selected_rows if r < insert_at)
        elif mode == "down":
            if last >= len(current_ids) - 1:
                return
            # Insert after the item just below the selected range.  Non-contiguous
            # selections are compacted as a batch, which is predictable for export.
            target_pos = min(len(current_ids), last + 2)
            insert_at = target_pos - sum(1 for r in selected_rows if r < target_pos)
        else:
            target_pos = max(0, min(len(current_ids), int(target_index if target_index is not None else 0)))
            insert_at = target_pos - sum(1 for r in selected_rows if r < target_pos)
        insert_at = max(0, min(len(remaining), insert_at))
        new_ids = remaining[:insert_at] + moving + remaining[insert_at:]
        if new_ids == current_ids:
            return
        self.preview_ids = new_ids
        self._preview_order_dirty = True
        self._rebuild_plan_after_order_change(moving)

    def on_view_mode_changed(self, index: int):
        self.stack.setCurrentIndex(0 if index == 0 else 1)
        if index == 1:
            QTimer.singleShot(0, self.update_table_column_layout)

    def update_table_column_layout(self):
        try:
            viewport_w = max(1, int(self.table.viewport().width()))
            min_widths = [260, 150, 132, 72, 220, 240]
            weights = [1.20, 0.40, 0.28, 0.10, 1.05, 1.00]
            total_min = sum(min_widths)
            if viewport_w <= total_min:
                widths = list(min_widths)
            else:
                extra = viewport_w - total_min
                weight_total = sum(weights)
                widths = [mw + int(extra * w / weight_total) for mw, w in zip(min_widths, weights)]
                widths[-1] += viewport_w - sum(widths)
            for c, width in enumerate(widths):
                self.table.setColumnWidth(c, max(40, int(width)))
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            for view in (getattr(self, "grid", None), getattr(self, "table", None)):
                tip = getattr(view, "_tooltip_widget", None)
                if tip is not None:
                    tip.hide()
        except Exception:
            pass
        # Detail windows opened from the reorder dialog are non-modal children of
        # this confirmation page.  Close them with the page so no orphan preview
        # keeps pointing at a stale export sequence after cancel/accept.
        try:
            for dlg in list(getattr(self, "detail_windows", set())):
                try:
                    dlg.close()
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)



class PhotoMoverQt(QMainWindow):
    def __init__(self):
        super().__init__()
        register_optional_theme_fonts(
            (resource_path("assets/fonts"), app_state_dir() / "fonts")
        )
        self.setWindowTitle(PRODUCT_DISPLAY_NAME)
        # Main window is frameless too; resizing is preserved by nativeEvent hit testing below.
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint
            | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.resize(1320, 820)
        self.setMinimumSize(980, 620)

        self.source_dir: Optional[Path] = None
        self.generation = 0
        self.stop_event = threading.Event()
        self.signals = WorkerSignals()
        self._closing = False
        self.settings_service = SettingsService(app_state_dir(), self)
        self.translation_service = TranslationService(
            str(self.settings_service.get("language.locale", "system")), self
        )
        self.settings_dialog: SettingsDialog | None = None

        self.all_items: list[PhotoItemData] = []
        self.item_map: dict[str, PhotoItemData] = {}
        self.visible_ids: list[str] = []
        self.visible_row_by_id: dict[str, int] = {}
        self.selected_ids: set[str] = set()
        self.classification_service = ClassificationService(
            [
                TimeRule(),
                MediaRule(),
                FileRule(image_size_resolver=get_image_display_size),
                DeviceRule(),
                LocationRule(),
                SourceRule(),
                PlusAIRule(),
            ]
        )
        self.photo_database = PhotoManagerDatabase(app_state_dir())
        self.plus_feature_analyzer = self.build_plus_feature_analyzer()
        self.category_repository = CategoryRepository(
            app_state_dir(),
            database=self.photo_database,
        )
        self.classification_snapshot = (
            self.category_repository.load()
            or ClassificationSnapshot(
                categories=self.classification_service.base_categories(),
                rule_versions=self.classification_service.rule_versions(),
            )
        )
        self.active_auto_category_id: Optional[str] = None
        self._classification_previous_snapshot_for_next_scan: ClassificationSnapshot | None = None
        self._classification_running_generation: Optional[int] = None
        self._classification_progress_queue: queue.SimpleQueue = queue.SimpleQueue()
        # App-level recycle bin: items are hidden from normal views but files stay in place
        # until the user performs an explicit permanent delete from the trash view.
        self.trash_ids: set[str] = set()
        # Loaded lazily on first scan/trash operation.  Reading a large trash
        # journal during window construction makes startup feel slow, while the
        # records are not needed until a folder is actually scanned.
        self.trash_records: dict[str, dict] = {}
        self._trash_records_loaded = False
        self.item_info_cache: dict[str, dict] | None = None  # lazy-loaded on first scan to keep startup fast
        # Manual binding map for ambiguous same-basename MOV files.  Kept lazy so
        # startup remains fast, but persisted beside the program so a user-made
        # binding survives restart unless the underlying files/signatures change.
        self.mov_bindings: dict[str, dict] = {}
        self._mov_bindings_loaded = False
        self._mov_bindings_dirty = False
        # Non-modal detail preview windows.  Keep explicit references so multiple
        # previews can coexist without being garbage-collected, and so owner-side
        # file/trash operations can synchronize their navigation lists safely.
        self.detail_windows: set[ImageDetailDialog] = set()

        self.total_items_count = 0
        self.total_files_count = 0
        self.total_size_bytes = 0
        self.visible_file_prefix: list[int] = [0]
        self.visible_size_prefix: list[int] = [0]

        self.icon_cache: dict[str, QPixmap] = {}
        self.thumb_requested: set[str] = set()
        self.hover_thumb_requested: set[str] = set()
        self.pending_thumb_data: dict[str, bytes] = {}
        self.live_frame_cache: dict[str, list[QPixmap]] = {}
        self.live_frame_requested: set[str] = set()
        self.live_frame_failed: set[str] = set()
        self.live_preview_item_id: Optional[str] = None
        self.live_preview_frame_index = 0

        self.scan_thread: Optional[threading.Thread] = None
        configured_workers = int(self.settings_service.get("scan.workers", 0) or 0)
        thumbnail_workers = configured_workers if configured_workers > 0 else THUMB_WORKERS
        self.thumb_executor = AppThreadPoolExecutor(max_workers=max(1, min(16, thumbnail_workers)))
        self.hover_thumb_executor = AppThreadPoolExecutor(max_workers=1)
        self.meta_executor = AppThreadPoolExecutor(max_workers=META_WORKERS)
        # Dedicated high-priority executor: LIVE hover/detail previews must never wait
        # behind thousands of thumbnail jobs.
        self.live_executor = AppThreadPoolExecutor(max_workers=LIVE_WORKERS)
        # Shared non-modal detail preview executors.  v37 created fresh executors
        # for every preview window; with multiple previews this made opening a
        # window heavier and could spawn unnecessary worker threads.  These pools
        # are lazy at the ThreadPoolExecutor level and keep preview opening cheap.
        self.detail_executor = AppThreadPoolExecutor(max_workers=2)
        self.detail_live_executor = AppThreadPoolExecutor(max_workers=2)
        # App-local delete operations can involve thousands of files. Run them
        # on a single background worker so the UI stays responsive and file-name
        # conflict resolution remains deterministic.
        self.file_op_executor = AppThreadPoolExecutor(max_workers=1)
        self.classification_executor = AppThreadPoolExecutor(max_workers=1)
        self.deleting_to_deleted_ids: set[str] = set()
        self._pending_delete_message_flags: dict[str, bool] = {"show_message": True, "all_trash": False}
        self.thumb_total = 0
        self.thumb_done_count = 0
        self.thumb_failure_count = 0
        self.thumb_failed_ids: set[str] = set()
        self.thumb_failure_examples: list[str] = []
        self.meta_done_count = 0
        self.updating_selection = False

        # Session-only undo/redo history for selection changes and non-permanent
        # trash operations.  The in-memory stack is small and capped; a temporary
        # JSONL mirror is kept only for the running session and is deleted on exit.
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._history_replaying = False
        self._history_suspended = False
        self._session_history_file_initialized = False

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_models_preserve_selection)

        self.thumb_flush_timer = QTimer(self)
        self.thumb_flush_timer.setSingleShot(True)
        self.thumb_flush_timer.timeout.connect(self.flush_pending_thumbnail_updates)

        self.live_preview_timer = QTimer(self)
        self.live_preview_timer.setInterval(90)
        try:
            self.live_preview_timer.setTimerType(Qt.PreciseTimer)
        except Exception:
            pass
        self.live_preview_timer.timeout.connect(self.advance_live_preview_frame)

        self._system_theme_timer = QTimer(self)
        self._system_theme_timer.setInterval(1800)
        self._system_theme_timer.timeout.connect(self._poll_system_theme)
        self._last_system_light_theme: bool | None = None

        self._item_info_cache_dirty = False
        self._persistent_state_dirty = False
        self._state_save_timer = QTimer(self)
        self._state_save_timer.setSingleShot(True)
        self._state_save_timer.timeout.connect(self.flush_persistent_state)

        self.classification_refresh_timer = QTimer(self)
        self.classification_refresh_timer.setSingleShot(True)
        self.classification_refresh_timer.timeout.connect(
            lambda: self.schedule_auto_classification(force=True)
        )
        self.classification_poll_timer = QTimer(self)
        self.classification_poll_timer.setInterval(50)
        self.classification_poll_timer.timeout.connect(self._poll_classification_future)

        self.placeholder_icon = make_placeholder_icon(False)
        # Do not bake LIVE into placeholder pixmaps. Both grid and table delegates draw
        # crisp vector LIVE badges themselves, so there is no clipped/double badge.
        self.placeholder_live_icon = self.placeholder_icon

        self._manual_resizing = False
        self._manual_resize_edge = None
        self._manual_resize_start_global = QPoint(0, 0)
        self._manual_resize_start_geom = None

        self.build_ui()
        self.bind_signals()
        self.settings_service.setting_changed.connect(self.apply_setting_change)
        self.settings_service.settings_reloaded.connect(lambda _snapshot: self.apply_all_settings())
        self.apply_all_settings()
        self._reset_session_history_file()
        self._install_undo_redo_shortcuts()
        QTimer.singleShot(180, self.restore_startup_folder)
        # Resizing is handled only by native WM_NCHITTEST.
        # Do not install a global/manual fallback: native and manual resizing
        # fighting each other was the root cause of live-resize content jitter.

    def showEvent(self, event):
        super().showEvent(event)
        install_frameless_window_native_features(self)
        set_native_corner_preference(
            self,
            rounded=(
                RUNTIME_THEME_PROFILE.corner_style == "continuous"
                or RUNTIME_THEME_PROFILE.control_style == "win11"
            ),
        )
        apply_opaque_rounded_window_mask(self, 10)

    # ---------- UI ----------

    def build_ui(self):
        # All themes deliberately use an opaque Qt surface.  It avoids the
        # contrast glitches and input issues caused by simulated glass.
        self._aero_translucent_surface = False
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        try:
            pal = self.palette()
            pal.setColor(QPalette.Window, QColor(APP_BG))
            self.setPalette(pal)
        except Exception:
            pass
        central = QWidget(self)
        central.setObjectName("RootTransparent")
        central.setAutoFillBackground(True)
        try:
            cpal = central.palette()
            cpal.setColor(QPalette.Window, QColor(APP_BG))
            central.setPalette(cpal)
        except Exception:
            pass
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        self._window_outer_layout = outer
        self._window_normal_margin = WINDOW_SHADOW_MARGIN
        outer.setContentsMargins(WINDOW_SHADOW_MARGIN, WINDOW_SHADOW_MARGIN, WINDOW_SHADOW_MARGIN, WINDOW_SHADOW_MARGIN)
        outer.setSpacing(0)

        self.window_shell = L2Panel(self, fill=APP_BG, border=APP_BORDER, radius_hint=10)
        self.window_shell.setObjectName("WindowShell")
        self.window_shell.setAttribute(Qt.WA_StyledBackground, False)
        # Do NOT put QGraphicsDropShadowEffect on a widget that contains the
        # whole UI.  Qt renders the affected widget subtree through an off-screen
        # pixmap; every child repaint can recache that pixmap and create the
        # apparent global trembling of text, buttons and thumbnails.  The window
        # shadow is now painted by the top-level window itself in paintEvent().
        self._window_shadow = None
        self.window_shell.setGraphicsEffect(None)
        outer.addWidget(self.window_shell, 1)

        shell_layout = QVBoxLayout(self.window_shell)
        self._window_shell_layout = shell_layout
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.main_title_bar = FramelessTitleBar(self, self, theme="light")
        self.main_title_bar.set_title(PRODUCT_DISPLAY_NAME)
        shell_layout.addWidget(self.main_title_bar)

        body = QWidget(self)
        body.setObjectName("MainBody")
        body.setAttribute(Qt.WA_StyledBackground, False)
        body.setStyleSheet("#MainBody { background: transparent; }")
        shell_layout.addWidget(body, 1)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        navigation = QWidget(body)
        navigation.setObjectName("LibraryNavigation")
        navigation.setFixedHeight(58)
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(16, 8, 18, 8)
        navigation_layout.setSpacing(8)
        self.btn_source = L2Button("选择照片文件夹…", self, variant="source", align=Qt.AlignLeft | Qt.AlignVCenter)
        self.btn_source.setObjectName("SourcePickerButton")
        self.btn_source.setIcon(ui_icon("folder", ACCENT_BLUE, ACCENT_BLUE, ACCENT_BLUE, 20))
        self.btn_source.setIconSize(QSize(20, 20))
        self.btn_source.setMinimumHeight(38)
        self.btn_source.setMinimumWidth(272)
        self.source_label = self.btn_source
        navigation_layout.addWidget(self.btn_source)
        navigation_layout.addStretch(1)
        self.library_title = QLabel("照片资料库", navigation)
        self.library_title.setObjectName("LibraryTitle")
        self.library_title.setAlignment(Qt.AlignCenter)
        navigation_layout.addWidget(self.library_title)
        navigation_layout.addStretch(1)
        self.library_count_label = QLabel("0 项", navigation)
        self.library_count_label.setObjectName("LibraryCount")
        navigation_layout.addWidget(self.library_count_label)
        body_layout.addWidget(navigation)

        workspace = QWidget(body)
        workspace.setObjectName("LibraryWorkspace")
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        body_layout.addWidget(workspace, 1)

        sidebar = QWidget(workspace)
        sidebar.setObjectName("LibrarySidebar")
        sidebar.setFixedWidth(248)
        self.sidebar_layout = QVBoxLayout(sidebar)
        self.sidebar_layout.setContentsMargins(14, 16, 14, 14)
        self.sidebar_layout.setSpacing(4)
        self.sidebar_heading = QLabel("资料库", sidebar)
        self.sidebar_heading.setObjectName("SidebarHeading")
        self.sidebar_layout.addWidget(self.sidebar_heading)
        workspace_layout.addWidget(sidebar)

        content = QWidget(workspace)
        content.setObjectName("LibraryContent")
        workspace_layout.addWidget(content, 1)
        root = QVBoxLayout(content)
        root.setContentsMargins(18, 12, 18, 10)
        root.setSpacing(7)

        toolbar_card = L2Panel(self, fill=CONTENT_BG, border=CONTENT_BG, radius_hint=0)
        toolbar_card.setObjectName("ToolbarCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(0, 2, 0, 3)
        toolbar.setSpacing(8)
        root.addWidget(toolbar_card)

        self.btn_stop = L2Button("", self, variant="toolbar")
        self.btn_stop.setToolTip("停止扫描")
        self.btn_stop.setAccessibleName("停止扫描")
        self.btn_stop.setIcon(ui_icon("stop", "#3A3A3C", "#3A3A3C", "#1C1C1E", 20))
        self.btn_stop.setIconSize(QSize(20, 20))
        self.btn_rescan = L2Button("", self, variant="toolbar")
        self.btn_rescan.setToolTip("重新扫描")
        self.btn_rescan.setAccessibleName("重新扫描")
        self.btn_rescan.setIcon(ui_icon("refresh", ACCENT_BLUE, ACCENT_BLUE, ACCENT_BLUE_DARK, 20))
        self.btn_rescan.setIconSize(QSize(20, 20))
        self.btn_select_all = L2Button("", self, variant="toolbar")
        self.btn_select_all.setToolTip("全选当前筛选结果")
        self.btn_select_all.setAccessibleName("全选当前筛选结果")
        self.btn_select_all.setIcon(ui_icon("check-square", "#3A3A3C", "#3A3A3C", ACCENT_BLUE, 20))
        self.btn_select_all.setIconSize(QSize(20, 20))
        self.btn_settings = L2Button("", self, variant="toolbar")
        self.btn_settings.setToolTip("设置")
        self.btn_settings.setAccessibleName("设置")
        self.btn_settings.setIcon(ui_icon("gear", "#3A3A3C", "#3A3A3C", ACCENT_BLUE, 20))
        self.btn_settings.setIconSize(QSize(20, 20))
        self.btn_clear = L2Button("", self, variant="toolbar")
        self.btn_clear.setToolTip("取消选择")
        self.btn_clear.setAccessibleName("取消选择")
        self.btn_clear.setIcon(ui_icon("square", "#3A3A3C", "#3A3A3C", ACCENT_BLUE, 20))
        self.btn_clear.setIconSize(QSize(20, 20))
        # Move is intentionally no longer a main-toolbar button. It is available
        # from the right-click menu so the primary toolbar stays focused on view/state actions.
        self.btn_move = L2Button("移动选中项", self, variant="accent")
        self.btn_delete_all = L2Button("全部删除", self, variant="accent")

        self.btn_move.setObjectName("AccentToolButton")
        self.btn_delete_all.setObjectName("TrashDeleteAllButton")
        for b in [self.btn_stop, self.btn_rescan, self.btn_select_all, self.btn_settings, self.btn_clear, self.btn_move, self.btn_delete_all]:
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
        # Keep action wording complete.  These buttons must not collapse into
        # unreadable two-character fragments under layout pressure.
        self.btn_stop.setMinimumWidth(42)
        self.btn_rescan.setMinimumWidth(42)
        self.btn_select_all.setMinimumWidth(42)
        self.btn_settings.setMinimumWidth(42)
        self.btn_clear.setMinimumWidth(42)
        self.btn_move.setMinimumWidth(120)
        self.btn_delete_all.setMinimumWidth(104)

        toolbar.addWidget(self.btn_stop)
        toolbar.addWidget(self.btn_rescan)
        toolbar.addSpacing(6)
        self.filter_label = QLabel("筛选")
        self.filter_label.setObjectName("TinyToolbarLabel")
        toolbar.addWidget(self.filter_label)
        self.filter_combo = ModernComboBox()
        self.filter_combo.addItems(["全部", "仅 LIVE 实况", "仅非 LIVE", "未绑定实况 MOV", "垃圾箱"])
        self.filter_combo.setMinimumWidth(176)
        toolbar.addWidget(self.filter_combo)
        self.filter_label.setVisible(False)
        self.filter_combo.setVisible(False)
        sidebar_entries = [
            ("所有照片", 0),
            ("实况照片", 1),
            ("静态照片", 2),
            ("待绑定视频", 3),
            ("最近删除", 4),
        ]
        self.sidebar_buttons = []
        for text, filter_index in sidebar_entries:
            icon_name = SIDEBAR_LIBRARY_ICONS.get(text, "photo")
            button = L2SidebarButton(text, sidebar)
            button.setObjectName("SidebarItem")
            button.setIcon(ui_icon(icon_name, SYSTEM_GRAY_6, "#FFFFFF", ACCENT_BLUE, 20))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, index=filter_index: self.select_library_filter(index)
            )
            self.sidebar_layout.addWidget(button)
            self.sidebar_buttons.append(button)
        self.sidebar_buttons[0].setChecked(True)
        self.sidebar_layout.addSpacing(16)
        self.smart_heading = QLabel("智能分类", sidebar)
        self.smart_heading.setObjectName("SidebarHeading")
        self.sidebar_layout.addWidget(self.smart_heading)
        self.smart_category_tree = QTreeWidget(sidebar)
        self.smart_category_tree.setObjectName("SmartCategoryTree")
        self.smart_category_tree.setColumnCount(2)
        self.smart_category_tree.setHeaderHidden(True)
        self.smart_category_tree.setRootIsDecorated(True)
        self.smart_category_tree.setIndentation(16)
        self.smart_category_tree.setAnimated(True)
        self.smart_category_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.smart_category_tree.setFocusPolicy(Qt.NoFocus)
        self.smart_category_tree.setMouseTracking(True)
        self.smart_category_tree.viewport().setMouseTracking(True)
        self.smart_category_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.smart_category_tree.setItemDelegate(L2CategoryTreeDelegate(self.smart_category_tree))
        self.smart_category_tree.setIconSize(QSize(20, 20))
        self.smart_category_tree.setColumnWidth(0, 164)
        self.smart_category_tree.setColumnWidth(1, 42)
        prepare_scroll_area(self.smart_category_tree)
        self.sidebar_layout.addWidget(self.smart_category_tree, 1)
        self.rebuild_auto_category_tree()
        self.view_label = QLabel("视图")
        self.view_label.setObjectName("TinyToolbarLabel")
        toolbar.addWidget(self.view_label)
        self.view_label.setVisible(False)
        self.view_combo = L2SegmentedControl()
        self.view_combo.addItems(["照片墙", "表格"])
        self.view_combo.setMinimumWidth(176)
        self.view_combo.setObjectName("SegmentedViewControl")
        toolbar.addWidget(self.view_combo)
        self.auto_resort_check = L2CheckBox("EXIF 自动重排")
        self.auto_resort_check.setChecked(True)
        self.auto_resort_check.setToolTip("大量照片时，自动重排会延后执行，避免拖选时卡顿。")
        toolbar.addWidget(self.auto_resort_check)
        toolbar.addStretch(1)
        toolbar.addWidget(self.btn_select_all)
        toolbar.addWidget(self.btn_settings)
        toolbar.addWidget(self.btn_clear)
        toolbar.addWidget(self.btn_delete_all)
        self.btn_clear.setVisible(False)
        self.btn_move.setVisible(False)
        self.btn_delete_all.setVisible(False)

        search_card = L2Panel(self, fill=CONTENT_BG, border=CONTENT_BG, radius_hint=0)
        search_card.setObjectName("MainSearchCard")
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(0, 2, 0, 4)
        search_layout.setSpacing(7)
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.main_search_edit = L2LineEdit(search_card, fill="#F5F5F7", border="#E5E5EA", leading_icon="search")
        self.main_search_edit.setPlaceholderText("搜索当前筛选结果：支持 *.HEIC、IMG_12??、*.MOV;*.HEIC")
        self.main_search_edit.setClearButtonEnabled(True)
        self.main_search_edit.setObjectName("MainSearchEdit")
        self.main_search_status = QLabel("未搜索")
        self.main_search_status.setObjectName("SearchStatus")
        search_row.addWidget(self.main_search_edit, 1)
        search_row.addWidget(self.main_search_status)
        search_layout.addLayout(search_row)
        self.main_search_model = SearchResultsModel(self)
        self.main_search_results = QTableView(search_card)
        self.main_search_results.setModel(self.main_search_model)
        self.main_search_results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.main_search_results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.main_search_results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.main_search_results.verticalHeader().setVisible(False)
        self.main_search_results.setShowGrid(False)
        self.main_search_results.setMaximumHeight(150)
        self.main_search_results.setVisible(False)
        self.main_search_results.setStyleSheet("QTableView { background: #FFFFFF; border: 1px solid #D1D1D6; border-radius: 8px; outline: 0; } QHeaderView::section { background: #F2F2F3; color: #6E6E73; padding: 6px 8px; border: none; }")
        prepare_scroll_area(self.main_search_results)
        try:
            self.main_search_results.horizontalHeader().setStretchLastSection(True)
            self.main_search_results.setColumnWidth(0, 260)
            self.main_search_results.setColumnWidth(1, 145)
        except Exception:
            pass
        search_layout.addWidget(self.main_search_results)
        root.addWidget(search_card)

        self.progress_card = L2Panel(self, fill="#F7F7F8", border="#EBEBEE", radius_hint=12)
        self.progress_card.setObjectName("ProgressCard")
        self.progress_card.setMaximumHeight(54)
        progress_layout = QHBoxLayout(self.progress_card)
        progress_layout.setContentsMargins(12, 6, 12, 6)
        progress_layout.setSpacing(12)
        root.addWidget(self.progress_card)
        self.progress_card.setVisible(False)

        scan_block = QVBoxLayout()
        scan_block.setSpacing(4)
        self.scan_label = StableProgressLabel("扫描：未开始")
        self.scan_label.setObjectName("ProgressLabel")
        self.scan_progress = L2ProgressBar(variant="scan")
        self.scan_progress.setObjectName("ScanProgress")
        scan_block.addWidget(self.scan_label)
        scan_block.addWidget(self.scan_progress)

        thumb_block = QVBoxLayout()
        thumb_block.setSpacing(4)
        self.thumb_label = StableProgressLabel("缩略图：未开始")
        self.thumb_label.setObjectName("ProgressLabel")
        self.thumb_progress = L2ProgressBar(variant="thumb")
        self.thumb_progress.setObjectName("ThumbProgress")
        thumb_block.addWidget(self.thumb_label)
        thumb_block.addWidget(self.thumb_progress)

        file_op_block = QVBoxLayout()
        file_op_block.setSpacing(4)
        self.file_op_label = StableProgressLabel("文件操作：空闲")
        self.file_op_label.setObjectName("ProgressLabel")
        self.file_op_progress = L2ProgressBar(variant="thumb")
        self.file_op_progress.setObjectName("FileOpProgress")
        self.file_op_progress.setRange(0, 1)
        self.file_op_progress.setValue(0)
        file_op_block.addWidget(self.file_op_label)
        file_op_block.addWidget(self.file_op_progress)

        progress_layout.addLayout(scan_block, 2)
        progress_layout.addLayout(thumb_block, 2)
        progress_layout.addLayout(file_op_block, 2)

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.stack.setStyleSheet("#ContentStack { background: #FFFFFF; border: none; }")
        root.addWidget(self.stack, 1)

        self.grid = PhotoGridView()
        self.grid_model = PhotoGridModel(self)
        self.grid.setModel(self.grid_model)
        self.stack.addWidget(self.grid)

        self.table = PhotoTableView()
        self.table.setHorizontalHeader(FadingHeaderView(Qt.Horizontal, self.table))
        self.table_model = PhotoTableModel(self)
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(TABLE_ICON_SIZE, TABLE_ICON_SIZE))
        self.table.setItemDelegate(PhotoTableDelegate(TABLE_ICON_SIZE, TABLE_ROW_HEIGHT, self.table))
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setMinimumSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        try:
            self.table.verticalScrollBar().setSingleStep(18)
            self.table.horizontalScrollBar().setSingleStep(18)
        except Exception:
            pass
        prepare_scroll_area(self.table)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setStyleSheet(
            "QTableView { background: #FFFFFF; gridline-color: transparent; border: none; outline: 0; }"
            "QTableView::item { border: none; padding: 0px; background: transparent; }"
            "QTableView::item:selected { background: transparent; color: white; }"
            "QTableView::item:selected:!active { background: transparent; color: white; }"
            "QHeaderView { background: #F2F2F3; border: none; }"
            "QHeaderView::section { background: #F2F2F3; color: #6E6E73; font-weight: 650; padding: 8px 10px; border: none; }"
        )
        prepare_scroll_area(self.table)
        try:
            table_pal = self.table.viewport().palette()
            table_pal.setColor(QPalette.Window, QColor("#FFFFFF"))
            table_pal.setColor(QPalette.Base, QColor("#FFFFFF"))
            self.table.viewport().setPalette(table_pal)
            self.table.viewport().setAutoFillBackground(True)
        except Exception:
            pass
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setMinimumHeight(40)
        header.setDefaultAlignment(Qt.AlignCenter)
        # A table-like list should breathe with the window, not keep old fixed
        # widths.  Column widths are managed by update_table_column_layout().
        for c in range(6):
            header.setSectionResizeMode(c, QHeaderView.Fixed)
        self.stack.addWidget(self.table)
        QTimer.singleShot(0, self.update_table_column_layout)

        footer = QWidget(content)
        footer.setObjectName("FooterBar")
        footer.setFixedHeight(30)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(6, 0, 4, 0)
        footer_layout.setSpacing(12)
        self.status_label = QLabel("说明：此版使用 Qt 虚拟模型；切换视图不会重建上千个控件，避免卡死。")
        self._ready_status_source = self.status_label.text()
        self._ready_status_values = {self._ready_status_source}
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        footer_layout.addWidget(self.status_label, 1)
        self.stats_label = QLabel("0 张照片 · 已选 0 · 0 B", footer)
        self.stats_label.setObjectName("StatsLabel")
        self.stats_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer_layout.addWidget(self.stats_label)
        root.addWidget(footer)

        # Give important text-bearing widgets the same no-hinting antialiased font.
        # This helps especially when Windows display scaling is 125%/150%.
        for w in [
            self.source_label, self.scan_label, self.thumb_label, self.stats_label, self.status_label,
            self.btn_source, self.btn_stop, self.btn_rescan, self.btn_select_all, self.btn_settings, self.btn_clear, self.btn_move, self.btn_delete_all,
            self.filter_combo, self.view_combo, self.auto_resort_check, self.grid, self.table,
            self.main_search_edit, self.main_search_status, self.main_search_results,
            self.table.horizontalHeader(), self.table.verticalHeader(), self.main_search_results.horizontalHeader(),
        ]:
            apply_smooth_font(w, 10)
        apply_smooth_font(self.stats_label, 10, bold=True)

    # ---------- persistent trash / cache ----------

    def _trash_state_path(self) -> Path:
        return _ensure_state_dir() / TRASH_STATE_FILE_NAME

    def _trash_journal_path(self) -> Path:
        return _ensure_state_dir() / TRASH_JOURNAL_FILE_NAME

    def _item_info_cache_path(self) -> Path:
        return _ensure_state_dir() / ITEM_INFO_CACHE_FILE_NAME

    def _mov_bindings_path(self) -> Path:
        return _ensure_state_dir() / MOV_BINDINGS_FILE_NAME

    def _normalize_trash_record(self, key: str, rec: dict) -> tuple[str, dict] | None:
        try:
            paths = rec.get("paths") if isinstance(rec, dict) else None
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                return None
            norm_paths = sorted(os.path.normcase(os.path.normpath(p)) for p in paths if p)
            if not norm_paths:
                return None
            real_key = stable_key_for_paths(norm_paths)
            display_name = str(rec.get("display_name") or Path(norm_paths[0]).name)
            kind = str(rec.get("kind") or "item")
            if kind not in {"item", "live_mov"}:
                kind = "item"
            bound_raw = rec.get("bound_image_paths") if isinstance(rec, dict) else []
            bound_paths = []
            if isinstance(bound_raw, list):
                bound_paths = [os.path.normcase(os.path.normpath(str(x))) for x in bound_raw if str(x)]
            return real_key, {
                "paths": norm_paths,
                "display_name": display_name,
                "kind": kind,
                "bound_image_paths": bound_paths,
                "trashed_at": float(rec.get("trashed_at") or time.time()),
            }
        except Exception:
            return None

    def _normalize_info_cache_record(self, key: str, rec: dict) -> tuple[str, dict] | None:
        try:
            if not isinstance(rec, dict):
                return None
            paths = rec.get("paths")
            signature = rec.get("signature")
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                return None
            if not isinstance(signature, str) or not signature:
                return None
            norm_paths = sorted(os.path.normcase(os.path.normpath(p)) for p in paths if p)
            if not norm_paths:
                return None
            real_key = stable_key_for_paths(norm_paths)
            out = dict(rec)
            out["paths"] = norm_paths
            out["signature"] = signature
            out["updated_at"] = float(out.get("updated_at") or 0.0)
            return real_key, out
        except Exception:
            return None

    def _normalize_mov_binding_record(self, key: str, rec: dict) -> tuple[str, dict] | None:
        """Normalize one manually chosen ambiguous-MOV binding record.

        Keyed by normalized MOV path.  The record is valid only while the MOV,
        the chosen still image and their cheap signatures still match.  If the
        folder changes in a way that affects the original ambiguity, the next
        scan silently ignores/removes the stale record and exposes the MOV for
        manual handling again.
        """
        try:
            if not isinstance(rec, dict):
                return None
            mov_path = normalize_item_path(Path(str(rec.get("mov_path") or key or "")))
            image_path = normalize_item_path(Path(str(rec.get("image_path") or "")))
            if not mov_path or not image_path:
                return None
            out = {
                "mov_path": mov_path,
                "image_path": image_path,
                "mov_signature": str(rec.get("mov_signature") or ""),
                "image_signature": str(rec.get("image_signature") or ""),
                "candidate_paths": [normalize_item_path(Path(str(x))) for x in (rec.get("candidate_paths") or []) if str(x)],
                "updated_at": float(rec.get("updated_at") or time.time()),
            }
            return mov_path, out
        except Exception:
            return None

    def _append_trash_journal(self, op: str, item: PhotoItemData | None = None, key: str | None = None):
        try:
            stable_key = key or (item.stable_key if item is not None else "")
            if not stable_key:
                return
            record = {"version": STATE_VERSION, "op": op, "key": stable_key, "ts": time.time()}
            if item is not None and op == "trash":
                record.update({
                    "paths": item_paths_for_state(item.files),
                    "display_name": item.display_name,
                    "kind": "live_mov" if item_is_mov_only(item) else "item",
                    "bound_image_paths": item_paths_for_state(getattr(item, "bound_image_paths", []) or []),
                })
            _append_jsonl_durable(self._trash_journal_path(), record)
        except Exception:
            pass

    def _replay_trash_journal(self, records: dict[str, dict]) -> dict[str, dict]:
        for ev in _load_jsonl_records(self._trash_journal_path()):
            try:
                op = ev.get("op")
                key = str(ev.get("key") or "")
                if not key:
                    continue
                if op == "trash":
                    normalized = self._normalize_trash_record(key, {
                        "paths": ev.get("paths"),
                        "display_name": ev.get("display_name"),
                        "kind": ev.get("kind") or "item",
                        "bound_image_paths": ev.get("bound_image_paths") or [],
                        "trashed_at": ev.get("ts") or time.time(),
                    })
                    if normalized is not None:
                        nkey, nrec = normalized
                        records[nkey] = nrec
                elif op in ("restore", "untrash", "move", "permanent_delete", "delete_to_deleted_dir"):
                    records.pop(key, None)
            except Exception:
                continue
        return records

    def load_trash_records(self) -> dict[str, dict]:
        data = _json_load(_ensure_state_dir() / TRASH_STATE_FILE_NAME, {})
        raw_records = data.get("items", {}) if isinstance(data, dict) else {}
        records: dict[str, dict] = {}
        if isinstance(raw_records, dict):
            for key, rec in raw_records.items():
                normalized = self._normalize_trash_record(str(key), rec if isinstance(rec, dict) else {})
                if normalized is not None:
                    nkey, nrec = normalized
                    records[nkey] = nrec
        records = self._replay_trash_journal(records)
        return records

    def ensure_trash_records_loaded(self) -> dict[str, dict]:
        """Load persistent trash state only when it is actually needed."""
        if not getattr(self, "_trash_records_loaded", False):
            self.trash_records = self.load_trash_records()
            self._trash_records_loaded = True
        return self.trash_records

    def save_trash_records(self):
        if not getattr(self, "_trash_records_loaded", True):
            # Do not create/overwrite state files during a session that never
            # touched the trash state.
            return True
        data = {
            "version": STATE_VERSION,
            "saved_at": time.time(),
            "items": self.trash_records,
        }
        ok = _json_save_atomic(self._trash_state_path(), data)
        if ok:
            self._persistent_state_dirty = False
        return ok

    def load_mov_bindings(self) -> dict[str, dict]:
        data = _json_load(_ensure_state_dir() / MOV_BINDINGS_FILE_NAME, {})
        raw_records = data.get("items", {}) if isinstance(data, dict) else {}
        records: dict[str, dict] = {}
        if isinstance(raw_records, dict):
            for key, rec in raw_records.items():
                normalized = self._normalize_mov_binding_record(str(key), rec if isinstance(rec, dict) else {})
                if normalized is not None:
                    nkey, nrec = normalized
                    records[nkey] = nrec
        return records

    def ensure_mov_bindings_loaded(self) -> dict[str, dict]:
        if not getattr(self, "_mov_bindings_loaded", False):
            self.mov_bindings = self.load_mov_bindings()
            self._mov_bindings_loaded = True
        return self.mov_bindings

    def save_mov_bindings(self):
        if not getattr(self, "_mov_bindings_loaded", True):
            return True
        data = {
            "version": STATE_VERSION,
            "saved_at": time.time(),
            "items": self.mov_bindings,
        }
        ok = _json_save_atomic(self._mov_bindings_path(), data)
        if ok:
            self._mov_bindings_dirty = False
        return ok

    def load_item_info_cache(self) -> dict[str, dict]:
        data = _json_load(_ensure_state_dir() / ITEM_INFO_CACHE_FILE_NAME, {})
        raw_records = data.get("items", {}) if isinstance(data, dict) else {}
        records: dict[str, dict] = {}
        if isinstance(raw_records, dict):
            for key, rec in raw_records.items():
                normalized = self._normalize_info_cache_record(str(key), rec if isinstance(rec, dict) else {})
                if normalized is not None:
                    nkey, nrec = normalized
                    records[nkey] = nrec
        return records

    def ensure_item_info_cache_loaded(self) -> dict[str, dict]:
        """Load the potentially large list-info cache only when it is needed.

        The previous version parsed item_info_cache.json during window creation,
        so a large photo library could make the program feel slow before the user
        even selected/rescanned a folder.  Keeping this lazy preserves the cache
        benefits without penalizing startup.
        """
        if self.item_info_cache is None:
            self.item_info_cache = self.load_item_info_cache()
        return self.item_info_cache

    def save_item_info_cache(self):
        if self.item_info_cache is None:
            self._item_info_cache_dirty = False
            return True
        data = {
            "version": STATE_VERSION,
            "saved_at": time.time(),
            "items": self.item_info_cache,
        }
        ok = _json_save_atomic(self._item_info_cache_path(), data)
        if ok:
            self._item_info_cache_dirty = False
        return ok

    def request_persistent_state_save(self, item_info: bool = False, trash: bool = False, mov_bindings: bool = False, delay_ms: int = 900):
        """Debounce durable writes during normal use.

        We still flush synchronously during shutdown.  During scanning/background
        EXIF completion, batching avoids hundreds of fsync calls while keeping a
        recent recoverable state on disk.
        """
        if item_info:
            self._item_info_cache_dirty = True
        if trash:
            self._persistent_state_dirty = True
        if mov_bindings:
            self._mov_bindings_dirty = True
        try:
            if getattr(self, "_closing", False):
                self.flush_persistent_state()
                return
            if not self._state_save_timer.isActive():
                self._state_save_timer.start(max(100, int(delay_ms)))
        except Exception:
            pass

    def flush_persistent_state(self):
        try:
            if getattr(self, "_persistent_state_dirty", False):
                if self.save_trash_records():
                    self._persistent_state_dirty = False
            if getattr(self, "_item_info_cache_dirty", False):
                self.save_item_info_cache()
            if getattr(self, "_mov_bindings_dirty", False):
                self.save_mov_bindings()
        except Exception:
            pass

    def cache_info_for_item(self, item: PhotoItemData, meta_state: str = "exif"):
        if not item.stable_key:
            return
        cache = self.ensure_item_info_cache_loaded()
        meta_state = meta_state if meta_state in {"exif", "no_exif"} else "exif"
        record = {
            "signature": item.file_signature,
            "paths": item_paths_for_state(item.files),
            "display_name": item.display_name,
            "size_bytes": int(item.size_bytes),
            "is_live": bool(item.is_live),
            "item_type": item.item_type,
            "meta_state": meta_state,
            "source_folder": normalize_item_path(item.source_folder),
            "camera_make": item.camera_make,
            "camera_model": item.camera_model,
            "gps_latitude": item.gps_latitude,
            "gps_longitude": item.gps_longitude,
            "image_width": int(item.image_width),
            "image_height": int(item.image_height),
            "updated_at": time.time(),
        }
        if meta_state == "exif":
            record["shot_time"] = iso_from_datetime(item.shot_time)
            record["time_source"] = item.time_source
        else:
            # Cache the fact that this exact file signature had no readable EXIF
            # capture time, but do not cache a quick fallback timestamp as if it
            # were authoritative shooting metadata.
            record["shot_time"] = ""
            record["time_source"] = "无可用 EXIF 拍摄时间（缓存）"
        cache[item.stable_key] = record

    def apply_cached_info_to_item(self, item: PhotoItemData):
        cache = self.ensure_item_info_cache_loaded()
        rec = cache.get(item.stable_key) if item.stable_key else None
        if not isinstance(rec, dict):
            return
        if rec.get("signature") != item.file_signature:
            # Folder content changed at this path; discard stale cached info and
            # let the scan/background EXIF pass rebuild it.
            cache.pop(item.stable_key, None)
            self._item_info_cache_dirty = True
            return
        item.camera_make = str(rec.get("camera_make") or "")
        item.camera_model = str(rec.get("camera_model") or "")
        try:
            item.gps_latitude = (
                float(rec["gps_latitude"])
                if rec.get("gps_latitude") is not None
                else None
            )
            item.gps_longitude = (
                float(rec["gps_longitude"])
                if rec.get("gps_longitude") is not None
                else None
            )
        except (TypeError, ValueError):
            item.gps_latitude = None
            item.gps_longitude = None
        try:
            item.image_width = int(rec.get("image_width") or 0)
            item.image_height = int(rec.get("image_height") or 0)
        except (TypeError, ValueError):
            item.image_width = 0
            item.image_height = 0
        meta_state = normalize_meta_state(rec)
        if meta_state == "exif":
            cached_dt = datetime_from_iso(str(rec.get("shot_time", "")))
            if cached_dt is not None:
                item.shot_time = cached_dt
                source = str(rec.get("time_source") or "EXIF拍摄时间")
                item.time_source = source if "缓存" in source else f"{source}（缓存）"
                item.meta_cached = True
        elif meta_state == "no_exif":
            # We already checked this unchanged file and found no capture-time
            # metadata.  Keep the fresh quick fallback computed from the current
            # file system state, but skip another slow metadata probe.
            item.meta_cached = True
        else:
            # Legacy v30-v33 quick-time cache: do not trust it, otherwise copied
            # photos may keep Windows import time and never receive an EXIF pass.
            cache.pop(item.stable_key, None)
            self._item_info_cache_dirty = True
        # Current scan remains authoritative for paths, size, type and existence.

    def _make_trash_mov_item_from_record(self, key: str, rec: dict) -> PhotoItemData | None:
        try:
            paths = rec.get("paths") if isinstance(rec, dict) else []
            mov_paths = [Path(x) for x in paths if str(x).lower().endswith(tuple(VIDEO_EXTENSIONS))]
            if not mov_paths:
                return None
            mov = mov_paths[0]
            if not mov.exists():
                return None
            bound_raw = rec.get("bound_image_paths") if isinstance(rec, dict) else []
            bound_images = [Path(x) for x in bound_raw if Path(str(x)).suffix.lower() in IMAGE_EXTENSIONS]
            existing_bound = [p for p in bound_images if p.exists()]
            rep = existing_bound[0] if existing_bound else mov
            item = PhotoItemData(
                item_id=f"trashmov_{key[:16]}",
                display_name=str(rec.get("display_name") or f"实况 MOV：{mov.name}"),
                files=[mov],
                size_bytes=group_size_bytes([mov]),
                representative_image=rep,
                is_live=False,
                item_type="垃圾箱中的实况 MOV",
                shot_time=get_fast_group_time([rep, mov] if rep != mov else [mov]),
                time_source="文件修改时间（快速）",
                source_folder=mov.parent,
                stable_key=key,
                file_signature=signature_for_files([mov]),
                item_kind="mov_only",
                bound_image_paths=bound_images,
                needs_binding=not bool(existing_bound),
            )
            return item
        except Exception:
            return None

    def reconcile_trash_after_scan(self):
        """Map persistent path-based trash records to this scan's temporary IDs.

        Normal photo trash records hide whole items. Live-MOV trash records hide
        only the motion component and expose a standalone MOV entry in the trash
        view. If an external file operation makes a record impossible, it is
        removed instead of blocking scan/update.
        """
        self.ensure_trash_records_loaded()
        self.trash_ids.clear()
        changed = False

        # First honor MOV-only trash records by removing those MOV files from any
        # freshly scanned live item. This keeps the still photo visible as a normal
        # photo while its motion component is in the program trash.
        mov_trash_records: list[tuple[str, dict]] = []
        for key, rec in list(self.trash_records.items()):
            paths = rec.get("paths") if isinstance(rec, dict) else None
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                self.trash_records.pop(key, None); changed = True; continue
            if not live_photo_item_paths_still_exist(paths):
                self.trash_records.pop(key, None); changed = True; continue
            if rec.get("kind") == "live_mov":
                mov_trash_records.append((key, rec))

        trashed_mov_norms = {normalize_item_path(Path(p)) for _key, rec in mov_trash_records for p in (rec.get("paths") or [])}
        for item in list(self.all_items):
            if item_is_mov_only(item):
                continue
            old_files = list(item.files)
            new_files = [f for f in item.files if normalize_item_path(f) not in trashed_mov_norms]
            if len(new_files) != len(old_files):
                item.files = new_files
                item.is_live = any(f.suffix.lower() in VIDEO_EXTENSIONS for f in new_files)
                item.size_bytes = group_size_bytes(new_files)
                ext_set = sorted({f.suffix.upper().lstrip(".") for f in new_files})
                if item.is_live:
                    item.item_type = f"LIVE 实况照片 ({' + '.join(ext_set)})"
                else:
                    item.item_type = f"普通照片 ({item.representative_image.suffix.upper().lstrip('.')})"
                assign_stable_identity(item)

        # Add synthetic trash-view entries for MOV-only trash records that belong
        # to the scanned folder. They are real operations on a real MOV path, but
        # represented separately from the still image.
        existing_ids = {item.item_id for item in self.all_items}
        for key, rec in mov_trash_records:
            mov_item = self._make_trash_mov_item_from_record(key, rec)
            if mov_item is None:
                self.trash_records.pop(key, None); changed = True; continue
            if self.source_dir and not path_is_under_folder(mov_item.files[0], self.source_dir):
                continue
            if mov_item.item_id not in existing_ids:
                self.all_items.append(mov_item)
                self.item_map[mov_item.item_id] = mov_item
                existing_ids.add(mov_item.item_id)
            self.trash_ids.add(mov_item.item_id)

        current_keys = {item.stable_key for item in self.all_items if item.stable_key}
        for key, rec in list(self.trash_records.items()):
            if rec.get("kind") == "live_mov":
                continue
            paths = rec.get("paths") if isinstance(rec, dict) else None
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                self.trash_records.pop(key, None); changed = True; continue
            if not live_photo_item_paths_still_exist(paths):
                self.trash_records.pop(key, None); changed = True; continue
            if key in current_keys:
                for item in self.all_items:
                    if item.stable_key == key:
                        self.trash_ids.add(item.item_id)
                        break
        if changed:
            self._persistent_state_dirty = True
            self.save_trash_records()

    def remove_trash_record_for_item(self, item: PhotoItemData, op: str = "move"):
        self.ensure_trash_records_loaded()
        if item.stable_key and item.stable_key in self.trash_records:
            self._append_trash_journal(op, item)
            self.trash_records.pop(item.stable_key, None)
            self._persistent_state_dirty = True
            self.save_trash_records()

    def update_table_column_layout(self):
        """Adaptive iTunes-like table widths.

        The table should read like a media/library list rather than a spreadsheet.
        Compact metadata columns keep stable readable widths; filename and source
        path take the elastic space.  This keeps headers visually centered in
        their actual columns while the window is resized.
        """
        try:
            if not hasattr(self, 'table') or self.table is None:
                return
            header = self.table.horizontalHeader()
            if header is None:
                return
            viewport_w = max(1, int(self.table.viewport().width()))
            # ModernScrollBar is a real scrollbar widget; if visible, it already
            # lives at the viewport edge in Qt's layout.  Keep the calculation on
            # viewport width only so columns match what is actually visible.
            # Fixed-ish metadata columns.
            time_w = 178
            type_w = 142
            count_w = 78
            time_source_w = 214
            fixed = time_w + type_w + count_w + time_source_w
            remaining = max(420, viewport_w - fixed)
            # Filename and source path are the two elastic reading columns.
            # The file column includes thumbnail + name, so it needs a generous
            # floor; the path column gets the larger share on wide windows.
            file_w = max(330, int(remaining * 0.43))
            path_w = max(300, remaining - file_w)
            # If minimums overflow the viewport, shrink the elastic columns first.
            total = file_w + time_w + type_w + count_w + time_source_w + path_w
            if total > viewport_w:
                overflow = total - viewport_w
                take_file = min(max(0, file_w - 300), overflow // 2 + overflow % 2)
                file_w -= take_file
                overflow -= take_file
                take_path = min(max(0, path_w - 260), overflow)
                path_w -= take_path
                overflow -= take_path
                if overflow > 0:
                    # Last resort for very narrow windows: metadata columns stay
                    # usable but compact.
                    time_w = max(150, time_w - overflow // 4)
                    type_w = max(112, type_w - overflow // 4)
                    time_source_w = max(170, time_source_w - overflow // 3)
            widths = [file_w, time_w, type_w, count_w, time_source_w, path_w]
            # Consume rounding error in the path column.
            diff = viewport_w - sum(widths)
            widths[5] = max(240, widths[5] + diff)
            for i, w in enumerate(widths):
                w = int(max(48, w))
                if header.sectionSize(i) != w:
                    self.table.setColumnWidth(i, w)
            try:
                header.viewport().update()
            except Exception:
                pass
        except Exception:
            pass

    def bind_signals(self):
        self.btn_source.clicked.connect(self.choose_source)
        self.btn_stop.clicked.connect(self.stop_scan)
        self.btn_rescan.clicked.connect(self.rescan)
        self.btn_select_all.clicked.connect(self.select_all_visible)
        self.btn_settings.clicked.connect(self.show_settings)
        self.btn_clear.clicked.connect(lambda _checked=False: self.clear_selection(record_history=True))
        self.btn_delete_all.clicked.connect(self.delete_all_trash_items)
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        self.view_combo.currentIndexChanged.connect(self.switch_view)
        self.main_search_edit.textChanged.connect(self.update_main_search_results)
        self.main_search_edit.returnPressed.connect(lambda: self.focus_first_main_search_result())
        self.main_search_results.clicked.connect(self.focus_main_search_result)
        self.main_search_results.doubleClicked.connect(self.focus_main_search_result)
        self.smart_category_tree.itemClicked.connect(self.on_auto_category_clicked)
        self.grid.selectionModel().selectionChanged.connect(self.on_grid_selection_changed)
        self.grid.range_dragged.connect(self.on_grid_range_dragged)
        self.grid.item_open_requested.connect(self.open_item_detail)
        self.grid.doubleClicked.connect(self.open_detail_from_model_index)
        self.grid.hover_item_changed.connect(self.on_hover_item_changed)
        self.grid.context_menu_requested.connect(self.show_item_context_menu)
        self.grid.clear_selection_requested.connect(self.clear_selection)
        # Do not connect QListView.entered directly to on_hover_item_changed().
        # entered fires for every item under fast mouse movement and bypasses the
        # debounce inside PhotoGridView, causing priority thumbnail/LIVE work and
        # status updates to storm the UI.  Route it back into the view's debounced
        # hover tracker instead.
        try:
            self.grid.entered.connect(lambda idx: self.grid._set_hover_item_from_index(idx))
            self.grid.viewportEntered.connect(lambda: self.grid._set_hover_item_from_index(QModelIndex()))
        except Exception:
            pass
        self.table.selectionModel().selectionChanged.connect(self.on_table_selection_changed)
        self.table.hover_item_changed.connect(self.on_hover_item_changed)
        self.table.item_open_requested.connect(self.open_item_detail)
        self.table.doubleClicked.connect(self.open_detail_from_model_index)
        self.table.context_menu_requested.connect(self.show_item_context_menu)
        self.table.clear_selection_requested.connect(self.clear_selection)

        self.signals.scan_found.connect(self.on_scan_found)
        self.signals.scan_items_ready.connect(self.on_scan_items_ready)
        self.signals.scan_error.connect(self.on_scan_error)
        self.signals.scan_cancelled.connect(self.on_scan_cancelled)
        self.signals.thumb_done.connect(self.on_thumb_done)
        self.signals.thumb_failed.connect(self.on_thumb_failed)
        self.signals.priority_thumb_done.connect(self.on_priority_thumb_done)
        self.signals.meta_done.connect(self.on_meta_done)
        self.signals.live_frames_ready.connect(self.on_live_frames_ready)
        self.signals.file_op_progress.connect(self.on_file_op_progress)
        self.signals.file_op_done.connect(self.on_file_op_done)

    # ---------- settings / localization / desktop integration ----------

    def show_settings(self):
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(
                self.settings_service,
                self.translation_service,
                resource_path("assets"),
                self,
            )
            self.settings_dialog.action_requested.connect(self.handle_settings_action)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def handle_settings_action(self, action: str, payload):
        if action == "open_data_folder":
            folder = app_state_dir()
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            return
        if action == "clear_thumbnail_cache":
            target = (app_state_dir() / THUMB_CACHE_DIR_NAME).resolve()
            root = app_state_dir().resolve()
            try:
                target.relative_to(root)
            except Exception:
                return
            reply = QMessageBox.question(
                self,
                "清除缩略图缓存",
                f"确定清除缩略图缓存吗？\n\n{target}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                try:
                    shutil.rmtree(target, ignore_errors=False)
                    target.mkdir(parents=True, exist_ok=True)
                    self.icon_cache.clear()
                    self.status_label.setText("缩略图缓存已清除；当前资料库会按需重新生成。")
                except FileNotFoundError:
                    target.mkdir(parents=True, exist_ok=True)
                except Exception as exc:
                    QMessageBox.warning(self, "无法清除缓存", str(exc))
            return
        if action == "rebuild_classification":
            self.rebuild_classification_service_from_settings()
            self.classification_snapshot = ClassificationSnapshot(
                categories=self.classification_service.base_categories(),
                rule_versions=self.classification_service.rule_versions(),
            )
            self.schedule_auto_classification(force=True)
            self.status_label.setText("正在按当前设置重建智能分类缓存。")

    def apply_all_settings(self):
        self.translation_service.set_locale(str(self.settings_service.get("language.locale", "system")))
        self.apply_runtime_appearance()
        self.apply_titlebar_setting()
        self.apply_main_translations()
        self.apply_thumbnail_size_setting()
        self.rebuild_classification_service_from_settings()
        try:
            self.grid._hover_emit_timer.setInterval(
                max(0, int(self.settings_service.get("live_photo.hover_delay_ms", 90)))
            )
        except Exception:
            pass
        default_view = str(self.settings_service.get("general.default_view", "grid"))
        self.view_combo.setCurrentIndex(1 if default_view == "table" else 0)

    def apply_setting_change(self, key: str, value):
        if key.startswith("appearance."):
            self.apply_runtime_appearance()
            if key == "appearance.titlebar_style":
                self.apply_titlebar_setting()
        elif key == "language.locale":
            self.translation_service.set_locale(str(value))
            if bool(self.settings_service.get("language.hot_reload", True)):
                self.apply_runtime_appearance()
                self.apply_main_translations()
        elif key == "general.default_view":
            self.view_combo.setCurrentIndex(1 if value == "table" else 0)
        elif key == "general.thumbnail_size":
            self.apply_thumbnail_size_setting()
        elif key == "live_photo.hover_delay_ms":
            try:
                self.grid._hover_emit_timer.setInterval(max(0, int(value)))
            except Exception:
                pass
        elif key.startswith("classification."):
            if key in {
                "classification.ai_enabled",
                "classification.content_model_path",
                "classification.content_confidence_percent",
                "classification.content_top_k",
            }:
                self.plus_feature_analyzer = self.build_plus_feature_analyzer()
            self.rebuild_classification_service_from_settings()

    def build_plus_feature_analyzer(self) -> PlusFeatureAnalyzer:
        provider = None
        enabled = bool(self.settings_service.get("classification.ai_enabled", True))
        model_path = str(self.settings_service.get("classification.content_model_path", "")).strip()
        threshold = max(
            0.01,
            min(1.0, float(self.settings_service.get("classification.content_confidence_percent", 35)) / 100.0),
        )
        if enabled and model_path:
            provider = TransformersImageClassifierProvider(
                model_path,
                threshold=threshold,
                top_k=int(self.settings_service.get("classification.content_top_k", 8)),
            )
        return PlusFeatureAnalyzer(
            content_provider=provider,
            content_confidence_threshold=threshold,
        )

    def _poll_system_theme(self):
        if str(self.settings_service.get("appearance.theme", "system")) != "system":
            self._system_theme_timer.stop()
            return
        current = windows_apps_use_light_theme()
        if current != self._last_system_light_theme:
            self._last_system_light_theme = current
            self.apply_runtime_appearance()

    def _set_main_aero_surface(self, enabled: bool):
        """Keep the legacy call site, but deliberately use a solid window."""

        self._aero_translucent_surface = False
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        window_color = QColor(APP_BG)
        try:
            palette = self.palette()
            palette.setColor(QPalette.Window, window_color)
            self.setPalette(palette)
            central = self.centralWidget()
            if central is not None:
                central.setAutoFillBackground(True)
                central_palette = central.palette()
                central_palette.setColor(QPalette.Window, window_color)
                central.setPalette(central_palette)
            handle = self.windowHandle()
            if handle is not None:
                handle.setColor(window_color)
        except Exception:
            pass

    def apply_runtime_appearance(self):
        global APP_BG, APP_BG_2, APP_PANEL, APP_PANEL_2, APP_BORDER, APP_TEXT, APP_MUTED
        global SIDEBAR_BG, CONTENT_BG, SYSTEM_GRAY_1, SYSTEM_GRAY_2, SYSTEM_GRAY_3, SYSTEM_GRAY_4, SYSTEM_GRAY_6
        global ACCENT_BLUE, ACCENT_BLUE_DARK, RUNTIME_THEME_STYLE, RUNTIME_THEME_LOCALE, RUNTIME_THEME_PROFILE
        theme = normalize_theme_id(str(self.settings_service.get("appearance.theme", "system")))
        RUNTIME_THEME_STYLE = theme
        RUNTIME_THEME_LOCALE = self.translation_service.locale
        accent_name = str(self.settings_service.get("appearance.accent", "blue"))
        accent_map = {
            "blue": "#007AFF", "purple": "#AF52DE", "pink": "#FF2D55",
            "orange": "#FF9500", "green": "#34C759", "red": "#FF3B30",
        }
        is_dark = (not windows_apps_use_light_theme()) if theme == "system" else theme == "dark"
        profile = resolve_theme_profile(
            theme,
            system_dark=is_dark,
            accent=accent_map.get(accent_name, "#007AFF"),
        )
        RUNTIME_THEME_PROFILE = profile
        ACCENT_BLUE, ACCENT_BLUE_DARK = profile.accent, profile.accent_dark
        self._last_system_light_theme = not is_dark
        if theme == "system":
            if not self._system_theme_timer.isActive():
                self._system_theme_timer.start()
        else:
            self._system_theme_timer.stop()
        APP_BG, APP_BG_2 = profile.app_bg, profile.app_bg_2
        APP_PANEL, APP_PANEL_2 = profile.panel, profile.panel_2
        APP_BORDER, APP_TEXT, APP_MUTED = profile.border, profile.text, profile.muted
        SIDEBAR_BG, CONTENT_BG = profile.sidebar, profile.content
        SYSTEM_GRAY_1, SYSTEM_GRAY_2, SYSTEM_GRAY_3 = profile.gray_1, profile.gray_2, profile.gray_3
        SYSTEM_GRAY_4, SYSTEM_GRAY_6 = profile.gray_4, profile.gray_6
        self._set_main_aero_surface(False)
        app = QApplication.instance()
        if app is not None:
            install_global_style(app)
            palette = app.palette()
            palette.setColor(QPalette.Window, QColor(APP_BG))
            palette.setColor(QPalette.Base, QColor(CONTENT_BG))
            palette.setColor(QPalette.Text, QColor(APP_TEXT))
            palette.setColor(QPalette.WindowText, QColor(APP_TEXT))
            palette.setColor(QPalette.Highlight, QColor(ACCENT_BLUE))
            app.setPalette(palette)
        try:
            shell_layout = getattr(self, "_window_shell_layout", None)
            if shell_layout is not None:
                shell_layout.setContentsMargins(0, 0, 0, 0)
            self.window_shell.fill = QColor(APP_BG)
            self.window_shell.border = QColor(APP_BORDER)
            self.window_shell._normal_border_color = QColor(APP_BORDER)
            self.window_shell.update()
            for panel in self.findChildren(L2Panel):
                name = panel.objectName()
                if name in ("ToolbarCard", "MainSearchCard"):
                    panel.fill = QColor(CONTENT_BG)
                    panel.border = QColor(CONTENT_BG)
                elif name == "ProgressCard":
                    panel.fill = QColor(APP_PANEL_2)
                    panel.border = QColor(APP_BORDER)
                panel.update()
            for line in self.findChildren(L2LineEdit):
                line.fill = QColor(CONTENT_BG if profile.is_flavor else (APP_PANEL_2 if is_dark else SYSTEM_GRAY_1))
                line.border = QColor(APP_BORDER)
                line.focus_border = QColor(ACCENT_BLUE)
                line.update()
            for scrollbar in self.findChildren(ModernScrollBar):
                scrollbar.apply_theme(profile)
            for progress in self.findChildren(L2ProgressBar):
                progress.apply_theme(profile)
            self.grid.viewport().setStyleSheet(f"background:{CONTENT_BG};")
            self.table.viewport().setStyleSheet(f"background:{CONTENT_BG};")
            self.grid.setStyleSheet(
                f"QListView {{ background: {CONTENT_BG}; border: none; outline: 0; }}"
                f"QListView::item {{ padding: 0; margin: 0; background: {CONTENT_BG}; border: none; }}"
                "QListView::item:selected, QListView::item:focus { background: transparent; border: none; outline: 0; }"
            )
            self.table.setStyleSheet(
                f"QTableView {{ background: {CONTENT_BG}; gridline-color: transparent; border: none; outline: 0; }}"
                "QTableView::item { border: none; padding: 0; background: transparent; }"
                "QTableView::item:selected { background: transparent; color: white; }"
                f"QHeaderView {{ background: {SYSTEM_GRAY_1}; border: none; }}"
                f"QHeaderView::section {{ background: {SYSTEM_GRAY_1}; color: {APP_MUTED}; font-weight: 650; padding: 8px 10px; border: none; }}"
            )
            self.main_search_results.setStyleSheet(
                f"QTableView {{ background: {CONTENT_BG}; border: 1px solid {APP_BORDER}; border-radius: {profile.control_radius}px; outline: 0; }}"
                f"QHeaderView::section {{ background: {SYSTEM_GRAY_1}; color: {APP_MUTED}; padding: 6px 8px; border: none; }}"
            )
            for view in (self.grid, self.table, self.main_search_results):
                pal = view.viewport().palette()
                pal.setColor(QPalette.Window, QColor(CONTENT_BG))
                pal.setColor(QPalette.Base, QColor(CONTENT_BG))
                view.viewport().setPalette(pal)
                view.viewport().setAutoFillBackground(True)
            self.stack.setStyleSheet(f"#ContentStack {{ background: {CONTENT_BG}; border: none; }}")
            for bar in self.findChildren(FramelessTitleBar):
                bar.apply_theme(profile)
            self.refresh_theme_typography()
            self.refresh_theme_icons()
            self.apply_titlebar_setting()
            for widget in QApplication.topLevelWidgets():
                if profile.corner_style != "square":
                    widget._dwm_native_round_corners = None
                apply_opaque_rounded_window_mask(widget, 10)
            self.update()
        except Exception:
            pass
        if self.settings_dialog is not None:
            self.settings_dialog.setProperty("darkMode", is_dark)
            self.settings_dialog.style().unpolish(self.settings_dialog)
            self.settings_dialog.style().polish(self.settings_dialog)

    def refresh_theme_typography(self):
        """Reapply theme/locale fonts to widgets and delegate-side cached fonts."""
        theme = RUNTIME_THEME_STYLE
        locale = self.translation_service.locale
        base_size = theme_display_point_size(
            theme, locale, 9 if RUNTIME_THEME_PROFILE.is_flavor else 10
        )
        for widget in [self, *self.findChildren(QWidget)]:
            try:
                old = widget.font()
                stored_size = widget.property("themeBasePointSize")
                if not isinstance(stored_size, int) or stored_size <= 0:
                    stored_size = old.pointSize() if old.pointSize() > 0 else (9 if RUNTIME_THEME_PROFILE.is_flavor else 10)
                    widget.setProperty("themeBasePointSize", stored_size)
                size = theme_display_point_size(theme, locale, stored_size)
                font = make_theme_font(theme, locale, size, bold=old.bold())
                font.setWeight(old.weight())
                widget.setFont(font)
            except Exception:
                pass
        delegates = [
            self.grid.itemDelegate(), self.table.itemDelegate(), self.smart_category_tree.itemDelegate(),
            self.table.horizontalHeader(), self.main_search_results.horizontalHeader(),
        ]
        for owner in delegates:
            base_sizes = getattr(owner, "_theme_base_font_sizes", {})
            for attr in ("_font", "placeholder_font", "badge_font", "text_font", "name_font"):
                old = getattr(owner, attr, None)
                if not isinstance(old, QFont):
                    continue
                if attr not in base_sizes:
                    base_sizes[attr] = old.pointSize() if old.pointSize() > 0 else (9 if RUNTIME_THEME_PROFILE.is_flavor else 10)
                size = theme_display_point_size(theme, locale, base_sizes[attr])
                setattr(owner, attr, make_theme_font(theme, locale, size, bold=old.bold()))
            owner._theme_base_font_sizes = base_sizes

    def refresh_theme_icons(self):
        """Remove modern decoration from historical themes and restore it losslessly."""
        profile = RUNTIME_THEME_PROFILE
        modern = profile.uses_modern_icons
        icon_size = 18 if profile.control_style == "win11" else 20
        text_labels = {
            self.btn_stop: "停止",
            self.btn_rescan: "扫描",
            self.btn_select_all: "全选",
            self.btn_settings: "设置",
            self.btn_clear: "取消",
        }
        if modern:
            self.btn_source.setIcon(ui_icon("folder", ACCENT_BLUE, ACCENT_BLUE, ACCENT_BLUE, icon_size))
            icon_specs = (
                (self.btn_stop, "stop", APP_TEXT),
                (self.btn_rescan, "refresh", ACCENT_BLUE),
                (self.btn_select_all, "check-square", APP_TEXT),
                (self.btn_settings, "gear", APP_TEXT),
                (self.btn_clear, "square", APP_TEXT),
            )
            for button, icon_name, color in icon_specs:
                button.setText("")
                button.setIcon(ui_icon(icon_name, color, color, ACCENT_BLUE, icon_size))
                button.setIconSize(QSize(icon_size, icon_size))
                button.setMinimumWidth(42)
            for button, icon_name in zip(
                self.sidebar_buttons,
                ("photo-stack", "live-photo", "photo", "video", "trash"),
            ):
                button.setIcon(ui_icon(icon_name, SYSTEM_GRAY_6, "#FFFFFF", ACCENT_BLUE, icon_size))
                button.setIconSize(QSize(icon_size, icon_size))
            self.main_search_edit.leading_icon = "search"
            self.main_search_edit.setTextMargins(38, 0, 13, 0)
        else:
            self.btn_source.setIcon(QIcon())
            for button, source in text_labels.items():
                label = self.translation_service.text(source)
                button.setIcon(QIcon())
                button.setText(label)
                button.setMinimumWidth(max(54, button.fontMetrics().horizontalAdvance(label) + 24))
            for button in self.sidebar_buttons:
                button.setIcon(QIcon())
            self.main_search_edit.leading_icon = None
            self.main_search_edit.setTextMargins(13, 0, 13, 0)
        self.rebuild_auto_category_tree()
        for view in (self.grid, self.table):
            overlay = getattr(view, "_empty_state_overlay", None)
            if overlay is not None:
                overlay.update()
        self.main_search_edit.update()

    def apply_titlebar_setting(self):
        global TITLEBAR_STYLE
        requested = str(self.settings_service.get("appearance.titlebar_style", "macos"))
        TITLEBAR_STYLE = RUNTIME_THEME_PROFILE.titlebar_skin if RUNTIME_THEME_PROFILE.is_flavor else requested
        for widget in QApplication.topLevelWidgets():
            for bar in widget.findChildren(FramelessTitleBar):
                bar.set_control_style(TITLEBAR_STYLE)
                bar.apply_theme(RUNTIME_THEME_PROFILE)

    def apply_main_translations(self):
        tr = self.translation_service.tr
        text = self.translation_service.text
        self.setWindowTitle(PRODUCT_DISPLAY_NAME)
        self.main_title_bar.set_title(PRODUCT_DISPLAY_NAME)
        self.library_title.setText(tr("app.library"))
        if not self.source_dir:
            self.btn_source.setText(tr("app.choose_folder"))
        labels = ["app.all_photos", "app.live_photos", "app.still_photos", "app.unbound_video", "app.recently_deleted"]
        for button, key in zip(self.sidebar_buttons, labels):
            button.setText(tr(key))
        self.sidebar_heading.setText(text("资料库"))
        self.smart_heading.setText(tr("app.smart_categories"))
        self.filter_label.setText(text("筛选"))
        self.view_label.setText(tr("app.view"))
        self.btn_settings.setToolTip(tr("settings.title"))
        self.btn_settings.setAccessibleName(tr("settings.title"))
        for widget, source in (
            (self.btn_stop, "停止扫描"),
            (self.btn_rescan, "重新扫描"),
            (self.btn_select_all, "全选当前筛选结果"),
            (self.btn_clear, "取消选择"),
        ):
            widget.setToolTip(text(source))
            widget.setAccessibleName(text(source))
        self.btn_move.setText(text("移动选中项"))
        self.btn_delete_all.setText(text("全部删除"))
        self.auto_resort_check.setText(tr("app.exif_reorder"))
        self.auto_resort_check.setToolTip(text("大量照片时，自动重排会延后执行，避免拖选时卡顿。"))
        self.view_combo.setItems([tr("app.photo_wall"), tr("app.table")])
        self.main_search_edit.setPlaceholderText(tr("app.search_placeholder"))
        if self.main_search_status.text() in {"未搜索", "尚未搜尋", "Not searched"}:
            self.main_search_status.setText(tr("app.not_searched"))
        if self.scan_label.text() in {"扫描：未开始", "掃描：尚未開始", "Scan: Not started"}:
            self.scan_label.setText(tr("app.scan_idle"))
        if self.thumb_label.text() in {"缩略图：未开始", "縮圖：尚未開始", "Thumbnails: Not started"}:
            self.thumb_label.setText(tr("app.thumbnail_idle"))
        if self.file_op_label.text() in {"文件操作：空闲", "檔案操作：閒置", "File operations: Idle"}:
            self.file_op_label.setText(tr("app.file_idle"))
        if self.status_label.text() in self._ready_status_values:
            ready = text(self._ready_status_source)
            self._ready_status_values.add(ready)
            self.status_label.setText(ready)
        for index, source in enumerate(("全部", "仅 LIVE 实况", "仅非 LIVE", "未绑定实况 MOV", "垃圾箱")):
            if index < self.filter_combo.count():
                self.filter_combo.setItemText(index, text(source))
        for model in (self.table_model, self.main_search_model):
            source_headers = getattr(model, "_i18n_source_headers", None)
            if source_headers is None:
                source_headers = list(getattr(model, "HEADERS", []))
                model._i18n_source_headers = source_headers
            model.HEADERS = [text(header) for header in source_headers]
            if model.HEADERS:
                model.headerDataChanged.emit(Qt.Horizontal, 0, len(model.HEADERS) - 1)
        self.rebuild_auto_category_tree()
        self.update_stats_label()
        for view in (self.grid, self.table):
            overlay = getattr(view, "_empty_state_overlay", None)
            if overlay is not None:
                overlay.update()

    def apply_thumbnail_size_setting(self):
        sizes = {
            "small": (92, 108),
            "medium": (ICON_SIZE, GRID_SIZE),
            "large": (156, 174),
        }
        icon_size, grid_size = sizes.get(str(self.settings_service.get("general.thumbnail_size", "medium")), sizes["medium"])
        self.grid.setIconSize(QSize(icon_size, icon_size))
        self.grid.setGridSize(QSize(grid_size, grid_size))
        self.grid.setItemDelegate(PhotoGridDelegate(icon_size, grid_size, self.grid))

    def rebuild_classification_service_from_settings(self):
        enabled = self.settings_service.get("classification.rules", {}) or {}
        rules = []
        if enabled.get("time", True):
            rules.append(TimeRule())
        if enabled.get("media", True):
            rules.append(MediaRule())
        if enabled.get("file", True):
            file_rule = FileRule(image_size_resolver=get_image_display_size)
            file_rule.large_file_min_bytes = max(1, int(self.settings_service.get("classification.large_file_mb", 50))) * 1024 * 1024
            rules.append(file_rule)
        if enabled.get("device", True):
            rules.append(DeviceRule())
        if enabled.get("location", True):
            rules.append(LocationRule())
        rules.append(SourceRule())
        if enabled.get("plus", True):
            rules.append(PlusAIRule())
        self.classification_service = ClassificationService(rules)

    def restore_startup_folder(self):
        if not bool(self.settings_service.get("general.restore_last_folder", True)):
            return
        text = str(self.settings_service.get("general.last_folder", "") or "")
        folder = Path(text) if text else None
        if folder is None or not folder.is_dir():
            return
        self.source_dir = folder
        parts = [part for part in folder.parts if part]
        self.source_label.setText("  ›  ".join(parts[-2:]) if parts else str(folder))
        self.source_label.setToolTip(str(folder))
        if bool(self.settings_service.get("general.auto_scan_on_start", False)):
            self.start_scan()


    # ---------- undo / redo history ----------

    def _session_history_path(self) -> Path:
        return _ensure_state_dir() / UNDO_SESSION_FILE_NAME

    def _reset_session_history_file(self):
        """Start each run with a clean transient undo/redo journal."""
        try:
            self._session_history_path().unlink(missing_ok=True)
        except Exception:
            pass
        self._session_history_file_initialized = True

    def _cleanup_session_history_file(self):
        """Undo/redo history is intentionally not persistent across restarts."""
        try:
            self.undo_stack.clear()
            self.redo_stack.clear()
        except Exception:
            pass
        try:
            self._session_history_path().unlink(missing_ok=True)
        except Exception:
            pass

    def _append_session_history_record(self, record: dict):
        try:
            if not getattr(self, "_session_history_file_initialized", False):
                self._reset_session_history_file()
            path = self._session_history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                f.write("\n")
        except Exception:
            pass

    def _install_undo_redo_shortcuts(self):
        try:
            self.undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
            self.undo_shortcut.setContext(Qt.WindowShortcut)
            self.undo_shortcut.activated.connect(self.undo_last_history_action)
            self.redo_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
            self.redo_shortcut.setContext(Qt.WindowShortcut)
            self.redo_shortcut.activated.connect(self.redo_last_history_action)
        except Exception:
            pass

    def _snapshot_for_history_item(self, item: PhotoItemData | None, item_id: str = "") -> dict:
        try:
            if item is not None:
                return {
                    "item_id": item.item_id,
                    "stable_key": item.stable_key,
                    "paths": item_paths_for_state(item.files),
                    "display_name": item.display_name,
                }
        except Exception:
            pass
        return {"item_id": str(item_id or ""), "stable_key": "", "paths": [], "display_name": str(item_id or "")}

    def _history_item_snapshots(self, item_ids: list[str] | set[str]) -> list[dict]:
        snapshots: list[dict] = []
        for iid in item_ids:
            item = self.item_map.get(iid)
            snapshots.append(self._snapshot_for_history_item(item, iid))
        return snapshots

    def _resolve_history_item_id(self, snap: dict) -> str | None:
        try:
            iid = str(snap.get("item_id") or "")
            if iid and iid in self.item_map:
                return iid
            stable_key = str(snap.get("stable_key") or "")
            if stable_key:
                for item in self.all_items:
                    if item.item_id in self.item_map and item.stable_key == stable_key:
                        return item.item_id
            paths = snap.get("paths")
            if isinstance(paths, list) and paths:
                key = stable_key_for_paths([str(p) for p in paths])
                for item in self.all_items:
                    if item.item_id in self.item_map and item.stable_key == key:
                        return item.item_id
        except Exception:
            pass
        return None

    def _resolve_history_item_ids(self, snapshots: list[dict]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for snap in snapshots:
            iid = self._resolve_history_item_id(snap if isinstance(snap, dict) else {})
            if iid and iid not in seen:
                ids.append(iid)
                seen.add(iid)
        return ids

    def _push_history_action(self, action: dict):
        if getattr(self, "_history_replaying", False) or getattr(self, "_history_suspended", False) or getattr(self, "_closing", False):
            return
        try:
            action = dict(action)
            action.setdefault("version", 1)
            action.setdefault("ts", time.time())
            self.undo_stack.append(action)
            if len(self.undo_stack) > UNDO_HISTORY_LIMIT:
                del self.undo_stack[:-UNDO_HISTORY_LIMIT]
            self.redo_stack.clear()
            self._append_session_history_record({"kind": "push", "action": action})
        except Exception:
            pass

    def _order_item_ids_for_history(self, ids: set[str]) -> list[str]:
        return sorted(ids, key=lambda iid: (self.visible_row_by_id.get(iid, 10**9), str(iid)))

    def _record_selection_diff(self, old_ids: set[str], new_ids: set[str], origin: str = ""):
        if getattr(self, "_history_replaying", False) or getattr(self, "_history_suspended", False):
            return
        old_set = {iid for iid in set(old_ids) if iid in self.item_map}
        new_set = {iid for iid in set(new_ids) if iid in self.item_map}
        if old_set == new_set:
            return

        removed = self._order_item_ids_for_history(old_set - new_set)
        added = self._order_item_ids_for_history(new_set - old_set)
        before = self._history_item_snapshots(self._order_item_ids_for_history(old_set))
        after = self._history_item_snapshots(self._order_item_ids_for_history(new_set))

        # A single user gesture may replace a large existing selection with one
        # item, e.g. accidentally clicking a photo after a big batch selection.
        # Store that gesture as one atomic transaction so Ctrl+Z restores the
        # whole previous selection in one step.  The per-item changes are still
        # kept inside the transaction for the session journal / diagnostics.
        changes: list[dict] = []
        for iid in removed:
            changes.append({
                "type": "select_remove",
                "item": self._snapshot_for_history_item(self.item_map.get(iid), iid),
            })
        for iid in added:
            changes.append({
                "type": "select_add",
                "item": self._snapshot_for_history_item(self.item_map.get(iid), iid),
            })
        self._push_history_action({
            "type": "selection_change",
            "origin": origin,
            "before": before,
            "after": after,
            "removed": self._history_item_snapshots(removed),
            "added": self._history_item_snapshots(added),
            "changes": changes,
            "changed_count": len(changes),
        })

    def _discard_last_temporary_selection_action(self, item_id: str):
        """Right-click single-item temporary selection must not pollute undo."""
        try:
            if not self.undo_stack:
                return
            last = self.undo_stack[-1]
            typ = str(last.get("type") or "")
            if typ == "selection_change":
                after = last.get("after")
                if not isinstance(after, list):
                    return
                after_ids = []
                for snap in after:
                    if isinstance(snap, dict):
                        after_ids.append(str(snap.get("item_id") or ""))
                if after_ids == [str(item_id)]:
                    self.undo_stack.pop()
                    self._append_session_history_record({"kind": "discard_temp", "item_id": item_id, "ts": time.time()})
                return
            if typ != "select_add":
                return
            snap = last.get("item") or {}
            if isinstance(snap, dict) and str(snap.get("item_id") or "") == str(item_id):
                self.undo_stack.pop()
                self._append_session_history_record({"kind": "discard_temp", "item_id": item_id, "ts": time.time()})
        except Exception:
            pass

    def _set_selection_state(self, new_ids: set[str] | list[str], record_history: bool = True, origin: str = "program"):
        old_ids = set(self.selected_ids)
        new_set = {iid for iid in set(new_ids) if iid in self.item_map}
        if record_history:
            self._record_selection_diff(old_ids, new_set, origin=origin)
        self.selected_ids = set(new_set)
        self.apply_selection_to_current_view()
        self.update_stats_label()
        self.status_label.setText(f"当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项。")

    def _apply_selection_history_action(self, action: dict, undo: bool) -> bool:
        typ = str(action.get("type") or "")
        if typ == "selection_change":
            snapshots = action.get("before" if undo else "after")
            if not isinstance(snapshots, list):
                return False
            ids = self._resolve_history_item_ids([s for s in snapshots if isinstance(s, dict)])
            # An empty target selection is valid, e.g. undoing the first select or
            # redoing a clear-selection gesture.  Missing deleted/moved items are
            # ignored, and the remaining live items are restored.
            self._set_selection_state(set(ids), record_history=False, origin="history")
            return True

        # Backward-compatible fallback for history entries made by v40 before this
        # atomic transaction format existed.
        snap = action.get("item") if isinstance(action.get("item"), dict) else {}
        iid = self._resolve_history_item_id(snap)
        if not iid:
            return False
        should_select = False
        if typ == "select_add":
            should_select = not undo
        elif typ == "select_remove":
            should_select = bool(undo)
        else:
            return False
        new_ids = set(self.selected_ids)
        if should_select:
            new_ids.add(iid)
        else:
            new_ids.discard(iid)
        self._set_selection_state(new_ids, record_history=False, origin="history")
        return True

    def _apply_trash_history_action(self, action: dict, undo: bool) -> bool:
        typ = str(action.get("type") or "")
        snapshots = action.get("items")
        if not isinstance(snapshots, list):
            return False
        ids = self._resolve_history_item_ids([s for s in snapshots if isinstance(s, dict)])
        if not ids:
            return False
        if typ == "trash_many":
            if undo:
                return bool(self.restore_items_from_trash_by_ids(ids, show_message=False, record_history=False))
            return bool(self.move_items_to_trash_by_ids(ids, show_message=False, record_history=False))
        if typ == "restore_many":
            if undo:
                return bool(self.move_items_to_trash_by_ids(ids, show_message=False, record_history=False))
            return bool(self.restore_items_from_trash_by_ids(ids, show_message=False, record_history=False))
        return False

    def _apply_history_action(self, action: dict, undo: bool) -> bool:
        typ = str(action.get("type") or "")
        if typ in {"selection_change", "select_add", "select_remove"}:
            return self._apply_selection_history_action(action, undo)
        if typ in {"trash_many", "restore_many"}:
            return self._apply_trash_history_action(action, undo)
        return False

    def undo_last_history_action(self):
        if getattr(self, "_closing", False):
            return
        if not self.undo_stack:
            self.status_label.setText("没有可撤销的选取 / 非永久删除操作。")
            return
        action = self.undo_stack.pop()
        self._history_replaying = True
        ok = False
        try:
            ok = self._apply_history_action(action, undo=True)
        finally:
            self._history_replaying = False
        if ok:
            self.redo_stack.append(action)
            if len(self.redo_stack) > UNDO_HISTORY_LIMIT:
                del self.redo_stack[:-UNDO_HISTORY_LIMIT]
            self._append_session_history_record({"kind": "undo", "action": action, "ts": time.time()})
            self.status_label.setText("已撤销上一步选取 / 非永久删除操作。")
        else:
            self.status_label.setText("上一步操作涉及的文件项已不存在，已跳过该撤销记录。")

    def redo_last_history_action(self):
        if getattr(self, "_closing", False):
            return
        if not self.redo_stack:
            self.status_label.setText("没有可重做的选取 / 非永久删除操作。")
            return
        action = self.redo_stack.pop()
        self._history_replaying = True
        ok = False
        try:
            ok = self._apply_history_action(action, undo=False)
        finally:
            self._history_replaying = False
        if ok:
            self.undo_stack.append(action)
            if len(self.undo_stack) > UNDO_HISTORY_LIMIT:
                del self.undo_stack[:-UNDO_HISTORY_LIMIT]
            self._append_session_history_record({"kind": "redo", "action": action, "ts": time.time()})
            self.status_label.setText("已重做上一步选取 / 非永久删除操作。")
        else:
            self.status_label.setText("上一步操作涉及的文件项已不存在，已跳过该重做记录。")

    # ---------- stats ----------

    def update_total_stats_cache(self):
        self.total_items_count = len(self.all_items)
        self.total_files_count = sum(len(item.files) for item in self.all_items)
        self.total_size_bytes = sum(item.size_bytes for item in self.all_items)

    def rebuild_visible_prefix_stats(self):
        file_prefix = [0]
        size_prefix = [0]
        self.visible_row_by_id = {}
        for row, iid in enumerate(self.visible_ids):
            self.visible_row_by_id[iid] = row
            item = self.item_map.get(iid)
            file_prefix.append(file_prefix[-1] + (len(item.files) if item else 0))
            size_prefix.append(size_prefix[-1] + (item.size_bytes if item else 0))
        self.visible_file_prefix = file_prefix
        self.visible_size_prefix = size_prefix

    def _visible_range_stats(self, ranges: list[tuple[int, int]]):
        if not self.visible_ids:
            return 0, 0, 0
        max_row = len(self.visible_ids) - 1
        selected_count = 0
        selected_files = 0
        selected_size = 0
        for a, b in ranges:
            a = max(0, min(a, max_row))
            b = max(0, min(b, max_row))
            if a > b:
                a, b = b, a
            selected_count += b - a + 1
            selected_files += self.visible_file_prefix[b + 1] - self.visible_file_prefix[a]
            selected_size += self.visible_size_prefix[b + 1] - self.visible_size_prefix[a]
        return selected_count, selected_files, selected_size

    def _selected_stats_from_ids(self):
        selected_count = 0
        selected_files = 0
        selected_size = 0
        for iid in self.selected_ids:
            item = self.item_map.get(iid)
            if item is None:
                continue
            selected_count += 1
            selected_files += len(item.files)
            selected_size += item.size_bytes
        return selected_count, selected_files, selected_size

    def update_selection_action_visibility(self, selected_count: Optional[int] = None):
        if selected_count is None:
            selected_count, _, _ = self._selected_stats_from_ids()
        has_selection = selected_count > 0
        if hasattr(self, "btn_clear"):
            self.btn_clear.setVisible(has_selection)
        # "移动选中项" is intentionally only in the context menu now.
        if hasattr(self, "btn_move"):
            self.btn_move.setVisible(False)
        if hasattr(self, "btn_delete_all"):
            pending = getattr(self, "deleting_to_deleted_ids", set())
            has_visible_trash = any((item.item_id in self.trash_ids) and (item.item_id not in pending) for item in self.all_items)
            self.btn_delete_all.setVisible(self.is_trash_view() and has_visible_trash)

    def _scope_items_for_stats(self) -> list[PhotoItemData]:
        pending = getattr(self, "deleting_to_deleted_ids", set())
        if self.is_trash_view():
            return [item for item in self.all_items if item.item_id in self.trash_ids and item.item_id not in pending]
        return [item for item in self.all_items if item.item_id not in self.trash_ids and item.item_id not in pending]

    def update_stats_label(self, selected_count: Optional[int] = None, selected_files: Optional[int] = None, selected_size: Optional[int] = None):
        if selected_count is None or selected_files is None or selected_size is None:
            selected_count, selected_files, selected_size = self._selected_stats_from_ids()
        self.update_selection_action_visibility(selected_count)
        scope_items = self._scope_items_for_stats()
        total_items = len(scope_items)
        total_files = sum(len(item.files) for item in scope_items)
        total_size = sum(item.size_bytes for item in scope_items)
        percent = (selected_size / total_size * 100.0) if total_size > 0 else 0.0
        scope_name = "垃圾箱" if self.is_trash_view() else "全部"
        if hasattr(self, "library_count_label"):
            self.library_count_label.setText(self.translation_service.tr("app.items", count=total_items))
        self.stats_label.setText(
            self.translation_service.tr(
                "app.summary",
                photos=len(self.visible_ids),
                selected=selected_count,
                size=(
                    f"{format_bytes(selected_size)} / {format_bytes(total_size)} · {percent:.1f}%"
                ),
            )
        )

    # ---------- scanning ----------

    def choose_source(self):
        folder = QFileDialog.getExistingDirectory(self, "请选择源文件夹")
        if not folder:
            return
        self.source_dir = Path(folder)
        self.settings_service.set("general.last_folder", str(self.source_dir))
        parts = [part for part in self.source_dir.parts if part]
        self.source_label.setText("  ›  ".join(parts[-2:]) if parts else str(self.source_dir))
        self.source_label.setToolTip(str(self.source_dir))
        self.start_scan()

    def rescan(self):
        if not self.source_dir:
            QMessageBox.information(self, "提示", "请先选择源文件夹。")
            return
        self.start_scan()

    def stop_scan(self):
        self.stop_event.set()
        self.status_label.setText("正在停止扫描。已显示的项目仍可继续操作。")

    def _show_activity_strip(self):
        try:
            self.progress_card.setVisible(True)
        except Exception:
            pass

    def _hide_activity_strip_if_idle(self):
        try:
            thumbnails_busy = self.thumb_total > 0 and self.thumb_done_count < self.thumb_total
            scan_busy = bool(getattr(self, "_scan_activity_busy", False))
            file_busy = bool(getattr(self, "_file_activity_busy", False)) or bool(getattr(self, "deleting_to_deleted_ids", set()))
            if not thumbnails_busy and not scan_busy and not file_busy:
                self.progress_card.setVisible(False)
        except Exception:
            pass

    def _rotate_library_generation_executors(self):
        """Drop queued work belonging to the previous library generation.

        Generation checks prevent stale callbacks from changing the UI, but they
        do not remove thousands of already queued decode jobs. A fresh pool lets
        the newly selected folder start immediately while running old jobs wind
        down in their retired pools.
        """
        for name in (
            "thumb_executor",
            "hover_thumb_executor",
            "meta_executor",
            "live_executor",
            "classification_executor",
        ):
            executor = getattr(self, name, None)
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
        configured_workers = int(self.settings_service.get("scan.workers", 0) or 0)
        thumbnail_workers = configured_workers if configured_workers > 0 else THUMB_WORKERS
        self.thumb_executor = AppThreadPoolExecutor(max_workers=max(1, min(16, thumbnail_workers)))
        self.hover_thumb_executor = AppThreadPoolExecutor(max_workers=1)
        self.meta_executor = AppThreadPoolExecutor(max_workers=META_WORKERS)
        self.live_executor = AppThreadPoolExecutor(max_workers=LIVE_WORKERS)
        self.classification_executor = AppThreadPoolExecutor(max_workers=1)

    def start_scan(self):
        if getattr(self, "_closing", False):
            return
        if not self.source_dir:
            return
        self.stop_event.set()
        self.generation += 1
        self._rotate_library_generation_executors()
        self._scan_activity_busy = True
        self.classification_generation = int(
            getattr(self, "classification_generation", 0)
        ) + 1
        gen = self.generation
        self.stop_event = threading.Event()

        self.all_items.clear()
        self.item_map.clear()
        self.visible_ids.clear()
        self.visible_row_by_id.clear()
        self.selected_ids.clear()
        self.trash_ids.clear()
        self.icon_cache.clear()
        self.thumb_requested.clear()
        self.hover_thumb_requested.clear()
        self.pending_thumb_data.clear()
        self.live_frame_cache.clear()
        self.live_frame_requested.clear()
        self.live_frame_failed.clear()
        self.live_preview_item_id = None
        self.live_preview_frame_index = 0
        self.live_preview_timer.stop()
        self.thumb_flush_timer.stop()
        self.thumb_total = 0
        self.thumb_done_count = 0
        self.thumb_failure_count = 0
        self.thumb_failed_ids.clear()
        self.thumb_failure_examples.clear()
        self.meta_done_count = 0
        self.update_total_stats_cache()
        self.rebuild_visible_prefix_stats()
        self.grid_model.set_visible_ids([])
        self.table_model.set_visible_ids([])

        self.scan_progress.setRange(0, 0)
        self._show_activity_strip()
        self.scan_label.setText("扫描：正在扫描文件……")
        self.thumb_progress.setRange(0, 1)
        self.thumb_progress.setValue(0)
        self.thumb_label.setText("缩略图：等待列表生成")
        self.status_label.setText("正在后台扫描，界面不会卡死。")
        self.update_stats_label()

        recursive = bool(self.settings_service.get("scan.recursive", True))
        exclude_patterns = list(self.settings_service.get("scan.exclude_patterns", []) or [])
        self.scan_thread = threading.Thread(
            target=self.scan_worker,
            args=(gen, self.source_dir, self.stop_event, recursive, exclude_patterns),
            daemon=True,
        )
        self.scan_thread.start()

    def scan_worker(self, gen: int, source_dir: Path, stop_event: threading.Event, recursive: bool = True, exclude_patterns: list[str] | None = None):
        if getattr(self, "_closing", False):
            return
        try:
            image_map: dict[tuple[str, str], list[Path]] = defaultdict(list)
            mov_map: dict[tuple[str, str], list[Path]] = defaultdict(list)
            found = 0
            for file in fast_iter_files(source_dir, stop_event, recursive=recursive, exclude_patterns=exclude_patterns):
                if stop_event.is_set():
                    self.signals.scan_cancelled.emit(gen)
                    return
                ext = file.suffix.lower()
                if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
                    continue
                key = (str(file.parent.resolve()), file.stem)
                if ext in IMAGE_EXTENSIONS:
                    image_map[key].append(file)
                    found += 1
                elif ext in VIDEO_EXTENSIONS:
                    mov_map[key].append(file)
                    found += 1
                if found % 300 == 0:
                    self.signals.scan_found.emit(found)

            items: list[PhotoItemData] = []
            counter = itertools.count()

            def next_item_id() -> str:
                return f"g{gen}_{next(counter)}"

            def add_still_item(img: Path):
                fast_time = get_fast_group_time([img])
                items.append(PhotoItemData(
                    item_id=next_item_id(), display_name=img.name, files=[img],
                    size_bytes=group_size_bytes([img]), representative_image=img, is_live=False,
                    item_type=f"普通照片 ({img.suffix.upper().lstrip('.')})",
                    shot_time=fast_time, time_source="文件修改时间（快速）", source_folder=img.parent,
                ))

            def add_live_item(img: Path, mov: Path):
                files = [img, mov]
                fast_time = get_fast_group_time(files)
                ext_set = sorted({f.suffix.upper().lstrip(".") for f in files})
                items.append(PhotoItemData(
                    item_id=next_item_id(), display_name=img.name, files=files,
                    size_bytes=group_size_bytes(files), representative_image=img, is_live=True,
                    item_type=f"LIVE 实况照片 ({' + '.join(ext_set)})",
                    shot_time=fast_time, time_source="文件修改时间（快速）", source_folder=img.parent,
                ))

            def add_unbound_mov_item(mov: Path, candidates: list[Path]):
                rep = candidates[0] if candidates else mov
                fast_time = get_fast_group_time([rep, mov] if rep != mov else [mov])
                display = f"未绑定实况 MOV：{mov.name}"
                item = PhotoItemData(
                    item_id=next_item_id(), display_name=display, files=[mov],
                    size_bytes=group_size_bytes([mov]), representative_image=rep, is_live=False,
                    item_type="未绑定实况 MOV（需手动绑定）",
                    shot_time=fast_time, time_source="文件修改时间（快速）", source_folder=mov.parent,
                    item_kind="mov_only", bound_image_paths=list(candidates), needs_binding=True,
                )
                items.append(item)

            all_keys = sorted(set(image_map.keys()) | set(mov_map.keys()))
            for key in all_keys:
                if stop_event.is_set():
                    self.signals.scan_cancelled.emit(gen)
                    return
                image_files = sorted(image_map.get(key, []), key=lambda p: (
                    IMAGE_PRIORITY.index(p.suffix.lower()) if p.suffix.lower() in IMAGE_PRIORITY else 999,
                    p.name.lower(),
                ))
                mov_files = sorted(mov_map.get(key, []), key=lambda p: p.name.lower())
                if not image_files:
                    for mov in mov_files:
                        add_unbound_mov_item(mov, [])
                    continue
                if not mov_files:
                    for img in image_files:
                        add_still_item(img)
                    continue
                if len(image_files) == 1:
                    add_live_item(image_files[0], mov_files[0])
                    for extra_mov in mov_files[1:]:
                        add_unbound_mov_item(extra_mov, image_files)
                else:
                    # Same basename but different image extensions is ambiguous.
                    # If the user previously chose which still image a MOV belongs to,
                    # restore that binding only while the exact MOV/image signatures are
                    # still valid.  Otherwise expose the MOV as a manual-binding item;
                    # never guess randomly and never block the scan.
                    used_image_norms: set[str] = set()
                    bound_mov_norms: set[str] = set()
                    for mov in mov_files:
                        bound_img = self._binding_image_for_ambiguous_mov(mov, image_files, used_image_norms)
                        if bound_img is not None:
                            add_live_item(bound_img, mov)
                            used_image_norms.add(normalize_item_path(bound_img))
                            bound_mov_norms.add(normalize_item_path(mov))
                    for img in image_files:
                        if normalize_item_path(img) not in used_image_norms:
                            add_still_item(img)
                    for mov in mov_files:
                        if normalize_item_path(mov) not in bound_mov_norms:
                            add_unbound_mov_item(mov, image_files)
            for item in items:
                item.library_root = source_dir
                assign_stable_identity(item)
                # Cached list metadata is used only when the path+size+mtime signature still matches.
                try:
                    self.apply_cached_info_to_item(item)
                except Exception:
                    pass
            items.sort(key=lambda x: (x.shot_time, x.display_name.lower()))
            self.signals.scan_items_ready.emit(gen, items)
        except Exception as e:
            self.signals.scan_error.emit(gen, str(e))

    def on_scan_found(self, count: int):
        if getattr(self, "_closing", False):
            return
        self.scan_label.setText(f"扫描：已发现 {count} 个相关文件……")

    def on_scan_items_ready(self, gen: int, items: object):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation:
            return
        self._scan_activity_busy = False
        self.all_items = list(items)  # type: ignore[arg-type]
        sort_mode = str(self.settings_service.get("general.default_sort", "time_asc"))
        if sort_mode == "time_desc":
            self.all_items.sort(key=lambda item: (item.shot_time, item.display_name.lower()), reverse=True)
        elif sort_mode == "name":
            self.all_items.sort(key=lambda item: item.display_name.lower())
        self.item_map = {item.item_id: item for item in self.all_items}
        self.reconcile_trash_after_scan()
        self.reset_auto_classification_for_new_scan()
        # Do not cache the initial quick fallback time as shooting metadata.
        # EXIF/no-EXIF results are persisted only after the background metadata pass.
        self.update_total_stats_cache()
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(1)
        self.scan_label.setText(f"扫描：完成，共 {len(self.all_items)} 项")
        self.thumb_total = len(self.all_items)
        self.thumb_done_count = 0
        self.meta_done_count = 0
        self.thumb_progress.setRange(0, max(1, self.thumb_total))
        self.thumb_progress.setValue(0)
        self.thumb_label.setText(f"缩略图：0/{self.thumb_total}")
        if self.thumb_total <= 0:
            QTimer.singleShot(700, self._hide_activity_strip_if_idle)
        self.schedule_auto_classification()
        self.schedule_background_tasks_for_all(gen)
        self.update_stats_label()
        self.status_label.setText("列表已生成。智能分类、缩略图和 EXIF 时间会在后台补齐；切换视图不会重建大量控件。")

    def on_scan_error(self, gen: int, msg: str):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation:
            return
        self._scan_activity_busy = False
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        QMessageBox.critical(self, "扫描失败", msg)
        self.scan_label.setText("扫描：失败")
        QTimer.singleShot(1200, self._hide_activity_strip_if_idle)

    def on_scan_cancelled(self, gen: int):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation:
            return
        self._scan_activity_busy = False
        self.scan_progress.setRange(0, 1)
        self.scan_progress.setValue(0)
        self.scan_label.setText("扫描：已停止")
        QTimer.singleShot(700, self._hide_activity_strip_if_idle)

    def schedule_background_tasks_for_all(self, gen: int):
        if getattr(self, "_closing", False):
            return
        for item in list(self.all_items):
            self.request_thumbnail(gen, item)
            if getattr(item, "meta_cached", False):
                self.meta_done_count += 1
            else:
                self.request_meta(gen, item)

    def request_thumbnail(self, gen: int, item: PhotoItemData):
        if getattr(self, "_closing", False):
            return
        if item.item_id in self.thumb_requested:
            return
        self.thumb_requested.add(item.item_id)
        fut = self.thumb_executor.submit(make_cached_or_fresh_thumbnail_bytes, item, (ICON_SIZE, ICON_SIZE))
        fut.add_done_callback(lambda f, g=gen, iid=item.item_id: self._thumb_callback(g, iid, f))

    def _thumb_callback(self, gen: int, item_id: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            data, error = future.result()
            if error:
                self.signals.thumb_failed.emit(gen, item_id, error)
            self.signals.thumb_done.emit(gen, item_id, data)
        except Exception as exc:
            data = pil_to_png_bytes(make_placeholder_pil("IMG", (ICON_SIZE, ICON_SIZE), False))
            self.signals.thumb_failed.emit(gen, item_id, f"{type(exc).__name__}: {exc}")
            self.signals.thumb_done.emit(gen, item_id, data)

    def request_priority_thumbnail(self, gen: int, item: PhotoItemData):
        if getattr(self, "_closing", False):
            return
        """Decode the hovered thumbnail through a tiny priority lane.

        The normal thumbnail executor may contain thousands of queued tasks. This
        priority lane intentionally has only one worker, so it improves perceived
        responsiveness for the item under the cursor without stealing too much CPU
        from the background loader.
        """
        if gen != self.generation:
            return
        if item.item_id in self.icon_cache:
            return
        if item.item_id in self.hover_thumb_requested:
            return
        self.hover_thumb_requested.add(item.item_id)
        fut = self.hover_thumb_executor.submit(make_cached_or_fresh_thumbnail_bytes, item, (ICON_SIZE, ICON_SIZE))
        fut.add_done_callback(lambda f, g=gen, iid=item.item_id: self._priority_thumb_callback(g, iid, f))

    def _priority_thumb_callback(self, gen: int, item_id: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            data, error = future.result()
            if error:
                self.signals.thumb_failed.emit(gen, item_id, error)
        except Exception as exc:
            data = pil_to_png_bytes(make_placeholder_pil("IMG", (ICON_SIZE, ICON_SIZE), False))
            self.signals.thumb_failed.emit(gen, item_id, f"{type(exc).__name__}: {exc}")
        self.signals.priority_thumb_done.emit(gen, item_id, data)

    def request_meta(self, gen: int, item: PhotoItemData):
        if getattr(self, "_closing", False):
            return
        fut = self.meta_executor.submit(
            extract_classification_metadata_for_item, item
        )
        fut.add_done_callback(lambda f, g=gen, iid=item.item_id: self._meta_callback(g, iid, f))

    def _meta_callback(self, gen: int, item_id: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            metadata = future.result()
            self.signals.meta_done.emit(gen, item_id, metadata)
        except Exception:
            self.signals.meta_done.emit(gen, item_id, None)

    # ---------- views ----------

    def update_main_search_results(self):
        query = self.main_search_edit.text().strip() if hasattr(self, "main_search_edit") else ""
        if not query:
            try:
                self.main_search_model.set_results([])
                self.main_search_results.setVisible(False)
                self.main_search_status.setText("未搜索")
            except Exception:
                pass
            return
        results: list[dict] = []
        try:
            deleting = getattr(self, "deleting_to_deleted_ids", set())
            candidate_ids = [
                item.item_id
                for item in self.all_items
                if item.item_id not in self.trash_ids and item.item_id not in deleting
            ]
            for iid in candidate_ids:
                item = self.item_map.get(iid)
                if item is None:
                    continue
                extra = list(self.classification_snapshot.item_search_fields.get(iid, []))
                # Persisted snapshots intentionally keep category relations compact
                # and may not contain the transient item_search_fields mapping.
                # Category names (including content labels) are nevertheless enough
                # to make tags searchable immediately after application startup.
                for category_id in self.classification_snapshot.item_category_ids.get(iid, set()):
                    category = self.classification_snapshot.categories.get(category_id)
                    if category is not None and category.name:
                        extra.append(category.name)
                if wildcard_query_matches(query, searchable_fields_for_item(item, extra)):
                    results.append({
                        "item_id": iid,
                        "name": item.display_name,
                        "type": item.item_type,
                        "location": str(item.source_folder),
                        "tooltip": tooltip_for_item(item),
                    })
            self.main_search_model.set_results(results)
            self.main_search_results.setVisible(True)
            self.main_search_status.setText(f"匹配 {len(results)} 项")
            try:
                self.main_search_results.resizeRowsToContents()
            except Exception:
                pass
        except Exception as e:
            self.main_search_model.set_results([])
            self.main_search_results.setVisible(True)
            self.main_search_status.setText(f"搜索失败：{e}")

    def focus_first_main_search_result(self):
        try:
            if self.main_search_model.rowCount() > 0:
                self.focus_main_search_result(self.main_search_model.index(0, 0))
        except Exception:
            pass

    def focus_main_search_result(self, index):
        try:
            if not index.isValid():
                return
            iid = str(index.sibling(index.row(), 0).data(ITEM_ID_ROLE) or "")
            if not iid or iid not in self.item_map:
                return
            if iid not in self.visible_row_by_id:
                self.select_library_filter(0)
            if iid not in self.visible_row_by_id:
                return
            self._set_selection_state({iid}, record_history=True, origin="search")
            row = self.visible_row_by_id.get(iid)
            if row is None:
                return
            self._scroll_current_view_to_row(row)
            item = self.item_map.get(iid)
            self.status_label.setText(f"已定位搜索结果：{item.display_name if item else iid}")
        except Exception:
            pass

    def _scroll_current_view_to_row(self, row: int):
        try:
            row = max(0, min(len(self.visible_ids) - 1, int(row)))
            target_view = self.grid if self.stack.currentIndex() == 0 else self.table
            model = target_view.model()
            if model is None:
                return
            idx = model.index(row, 0)
            if idx.isValid():
                target_view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                target_view.setFocus(Qt.OtherFocusReason)
                target_view.viewport().update()
        except Exception:
            pass

    def rebuild_auto_category_tree(self):
        """使用分类快照重建轻量级分类树，不创建照片控件。"""
        tree = getattr(self, "smart_category_tree", None)
        if tree is None:
            return
        expanded_ids: set[str] = set()
        for index in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(index)
            if root is not None and root.isExpanded():
                expanded_ids.add(str(root.data(0, CATEGORY_ID_ROLE) or ""))
        tree.clear()

        categories = self.classification_snapshot.categories
        current_item_ids = set(getattr(self, "item_map", {}) or {})
        items_by_id: dict[str, QTreeWidgetItem] = {}
        ordered = sorted(
            categories.values(),
            key=lambda category: (
                0 if category.parent_id is None else 1,
                category.sort_key,
                category.name,
            ),
        )
        remaining = list(ordered)
        while remaining:
            progressed = False
            for category in list(remaining):
                if category.parent_id and category.parent_id not in items_by_id:
                    continue
                if current_item_ids:
                    category_count = len(
                        self.classification_snapshot.item_ids_for(
                            category.category_id
                        )
                        & current_item_ids
                    )
                else:
                    category_count = category.item_count
                name_label = self.translation_service.text(category.name)
                count_label = str(category_count)
                parent_item = items_by_id.get(category.parent_id or "")
                tree_item = (
                    QTreeWidgetItem(parent_item, [name_label, count_label])
                    if parent_item is not None
                    else QTreeWidgetItem(tree, [name_label, count_label])
                )
                tree_item.setData(0, CATEGORY_ID_ROLE, category.category_id)
                if RUNTIME_THEME_PROFILE.uses_modern_icons:
                    icon_size = 18 if RUNTIME_THEME_PROFILE.control_style == "win11" else 20
                    tree_item.setIcon(
                        0,
                        ui_icon(
                            icon_name_for_auto_category(category.category_id),
                            SYSTEM_GRAY_6,
                            "#FFFFFF",
                            ACCENT_BLUE,
                            icon_size,
                            on_color=SYSTEM_GRAY_6,
                        ),
                    )
                tree_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                tree_item.setData(1, Qt.ForegroundRole, QColor(SYSTEM_GRAY_6))
                tree_item.setToolTip(
                    0,
                    f"{self.translation_service.text(category.name)}："
                    f"{self.translation_service.tr('app.items', count=category_count)}",
                )
                items_by_id[category.category_id] = tree_item
                remaining.remove(category)
                progressed = True
            if not progressed:
                break

        for category_id, tree_item in items_by_id.items():
            category = categories.get(category_id)
            if category and category.parent_id is None:
                tree_item.setExpanded(category_id in expanded_ids or not expanded_ids)
            if category_id == self.active_auto_category_id:
                tree.setCurrentItem(tree_item)

    def reset_auto_classification_for_new_scan(self):
        """新扫描进入时清空旧运行期分类 ID，避免旧缓存计数筛出 0 项。"""
        self._classification_previous_snapshot_for_next_scan = self.classification_snapshot
        self.active_auto_category_id = None
        with QSignalBlocker(self.filter_combo):
            self.filter_combo.setCurrentIndex(0)
        tree = getattr(self, "smart_category_tree", None)
        if tree is not None:
            tree.clearSelection()
            tree.setCurrentItem(None)
        self.classification_snapshot = ClassificationSnapshot(
            categories=self.classification_service.base_categories(),
            rule_versions=self.classification_service.rule_versions(),
        )
        self.rebuild_auto_category_tree()
        self.apply_filter()

    def select_library_filter(self, index: int):
        """切换传统资料库筛选，并退出智能分类。"""
        self.active_auto_category_id = None
        tree = getattr(self, "smart_category_tree", None)
        if tree is not None:
            tree.clearSelection()
            tree.setCurrentItem(None)
        old_index = self.filter_combo.currentIndex()
        self.filter_combo.setCurrentIndex(index)
        if old_index == index:
            self.apply_filter()

    def on_auto_category_clicked(self, tree_item: QTreeWidgetItem, column: int = 0):
        del column
        category_id = str(tree_item.data(0, CATEGORY_ID_ROLE) or "")
        if not category_id:
            return
        self.active_auto_category_id = category_id
        with QSignalBlocker(self.filter_combo):
            self.filter_combo.setCurrentIndex(0)
        for button in getattr(self, "sidebar_buttons", []):
            button.setAutoExclusive(False)
            button.setChecked(False)
            button.setAutoExclusive(True)
        self.apply_filter()
        category = self.classification_snapshot.categories.get(category_id)
        if category:
            self.status_label.setText(
                f"智能分类：{category.name}，当前显示 {len(self.visible_ids)} 项。"
            )

    def schedule_auto_classification(self, force: bool = False):
        """在独立线程池中分类当前有效项目。"""
        if getattr(self, "_closing", False):
            return
        self.classification_generation = int(
            getattr(self, "classification_generation", 0)
        ) + 1
        request_generation = self.classification_generation
        deleting = getattr(self, "deleting_to_deleted_ids", set())
        items = [
            item
            for item in self.all_items
            if item.item_id not in self.trash_ids and item.item_id not in deleting
        ]
        previous_snapshot = None if force else (
            self._classification_previous_snapshot_for_next_scan
            or self.classification_snapshot
        )
        self._classification_previous_snapshot_for_next_scan = None
        self._classification_running_generation = request_generation
        self._classification_future = self.classification_executor.submit(
            self._classification_worker,
            items,
            previous_snapshot,
            request_generation,
        )
        self._classification_future_generation = request_generation
        if not self.classification_poll_timer.isActive():
            self.classification_poll_timer.start()
        if items:
            self.status_label.setText("正在后台生成智能分类；基础分类会先可用。")

    def _classification_worker(
        self,
        items: list[PhotoItemData],
        previous_snapshot: ClassificationSnapshot | None,
        request_generation: int,
    ):
        if self.stop_event.is_set() or getattr(self, "_closing", False):
            return None
        working_items = clone_items_for_classification(items)
        stable_keys = [
            item.stable_key or item.item_id
            for item in working_items
            if item.stable_key or item.item_id
        ]
        user_metadata = self.photo_database.load_user_metadata(stable_keys)
        feature_cache = self.photo_database.load_feature_cache(stable_keys)
        custom_rules = self.photo_database.load_custom_rules()

        fast_items = clone_items_for_classification(items)
        self.plus_feature_analyzer.enrich_items(
            fast_items,
            user_metadata=user_metadata,
            feature_cache=feature_cache,
            custom_rules=custom_rules,
            read_pixels=False,
            stop_event=self.stop_event,
        )
        if self.stop_event.is_set() or getattr(self, "_closing", False):
            return None
        fast_snapshot = self.classification_service.classify_incremental(
            fast_items,
            previous_snapshot,
        )
        self._classification_progress_queue.put(
            (
                request_generation,
                fast_snapshot,
                0 if self.plus_feature_analyzer.content_provider is not None else len(fast_items),
                len(fast_items),
            )
        )

        feature_records = self.plus_feature_analyzer.enrich_items(
            working_items,
            user_metadata=user_metadata,
            feature_cache=feature_cache,
            custom_rules=custom_rules,
            read_pixels=True,
            stop_event=self.stop_event,
        )
        if self.stop_event.is_set() or getattr(self, "_closing", False):
            return None
        self.photo_database.save_feature_cache(feature_records)
        snapshot = self.classification_service.classify_incremental(
            working_items,
            previous_snapshot,
            progress_callback=lambda partial, processed, total: (
                self._classification_progress_queue.put(
                    (
                        request_generation,
                        partial,
                        processed,
                        total,
                    )
                )
            ),
        )
        if (
            self.stop_event.is_set()
            or getattr(self, "_closing", False)
            or request_generation
            != getattr(self, "classification_generation", 0)
        ):
            return None
        self.category_repository.save(snapshot)
        return snapshot

    def _poll_classification_future(self):
        latest_progress = None
        while True:
            try:
                progress = self._classification_progress_queue.get_nowait()
            except queue.Empty:
                break
            if progress[0] == getattr(self, "classification_generation", 0):
                latest_progress = progress
        if latest_progress is not None:
            _, partial, processed, total = latest_progress
            self.classification_snapshot = partial
            self.rebuild_auto_category_tree()
            if self.active_auto_category_id:
                self.apply_filter()
            self.status_label.setText(
                f"正在后台分类：{processed}/{total}；浏览和搜索仍可继续。"
            )
        future = getattr(self, "_classification_future", None)
        if future is None or not future.done():
            return
        self.classification_poll_timer.stop()
        request_generation = int(
            getattr(self, "_classification_future_generation", 0)
        )
        self._classification_future = None
        try:
            snapshot = future.result()
        except Exception as exc:
            self.on_classification_error(request_generation, str(exc))
            return
        if snapshot is not None:
            self.on_classification_ready(request_generation, snapshot)

    def on_classification_ready(self, request_generation: int, snapshot: object):
        if getattr(self, "_closing", False):
            return
        if request_generation != getattr(self, "classification_generation", 0):
            return
        if not isinstance(snapshot, ClassificationSnapshot):
            return
        self._classification_running_generation = None
        self.classification_snapshot = snapshot
        self.rebuild_auto_category_tree()
        if self.active_auto_category_id:
            self.apply_filter()
        if snapshot.errors:
            self.status_label.setText(
                f"自动分类完成；{len(snapshot.errors)} 项分类失败，基础浏览不受影响。"
            )
        else:
            self.status_label.setText(
                f"自动分类完成；当前显示 {len(self.visible_ids)} 项。"
            )

    def on_classification_error(self, request_generation: int, message: str):
        if request_generation != getattr(self, "classification_generation", 0):
            return
        self._classification_running_generation = None
        self.status_label.setText(f"自动分类失败：{message}；基础浏览不受影响。")

    def is_trash_view(self) -> bool:
        try:
            return self.filter_combo.currentText() == "垃圾箱"
        except Exception:
            return False

    def current_filter_accepts(self, item: PhotoItemData):
        # Items already confirmed for app-local deletion disappear immediately
        # from the current view while actual file moves continue in the background.
        # If a move later fails, the tile reappears so the user can retry.
        if item.item_id in getattr(self, "deleting_to_deleted_ids", set()):
            return False
        mode = self.filter_combo.currentText()
        in_trash = item.item_id in self.trash_ids
        if mode == "垃圾箱":
            return in_trash
        if in_trash:
            return False
        if self.active_auto_category_id:
            return item.item_id in self.classification_snapshot.item_ids_for(
                self.active_auto_category_id
            )
        if mode == "未绑定实况 MOV":
            return bool(getattr(item, "needs_binding", False) or getattr(item, "item_kind", "photo") == "mov_only")
        if mode == "仅 LIVE 实况":
            return item.is_live
        if mode == "仅非 LIVE":
            return not item.is_live
        return True

    def apply_filter(self):
        if hasattr(self, "sidebar_buttons") and not self.active_auto_category_id:
            index = self.filter_combo.currentIndex()
            if 0 <= index < len(self.sidebar_buttons):
                self.sidebar_buttons[index].setChecked(True)
        self.visible_ids = [item.item_id for item in self.all_items if self.current_filter_accepts(item)]
        self.rebuild_visible_prefix_stats()
        self.grid_model.set_visible_ids(self.visible_ids)
        self.table_model.set_visible_ids(self.visible_ids)
        self.apply_selection_to_current_view()
        self.update_main_search_results()
        self.update_stats_label()
        self.status_label.setText(f"当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项。")

    def switch_view(self):
        idx = self.view_combo.currentIndex()
        self.stack.setCurrentIndex(idx)
        self.apply_selection_to_current_view()
        # Keep the selection highlight identical after switching views.
        # Without focus, Windows may draw an inactive pale selection in QTableView.
        if idx == 1:
            self.table.setFocus(Qt.OtherFocusReason)
            self.table.viewport().update()
        else:
            self.grid.setFocus(Qt.OtherFocusReason)
            self.grid.viewport().update()
        self.update_stats_label()
        self.status_label.setText(f"已切换视图。当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项。")

    def refresh_models_preserve_selection(self):
        if getattr(self.grid, "_dragging", False):
            self.refresh_timer.start(1200)
            return
        self.all_items.sort(key=lambda x: (x.shot_time, x.display_name.lower()))
        self.visible_ids = [item.item_id for item in self.all_items if self.current_filter_accepts(item)]
        self.rebuild_visible_prefix_stats()
        self.grid_model.set_visible_ids(self.visible_ids)
        self.table_model.set_visible_ids(self.visible_ids)
        self.apply_selection_to_current_view()
        self.update_main_search_results()
        self.update_stats_label()

    def icon_for_item(self, item: PhotoItemData):
        if item.item_id == self.live_preview_item_id:
            frames = self.live_frame_cache.get(item.item_id)
            if frames:
                return frames[self.live_preview_frame_index % len(frames)]
        return self.icon_cache.get(item.item_id) or (self.placeholder_live_icon if item.is_live else self.placeholder_icon)

    def on_select_mode_toggled(self, enabled: bool):
        self.grid.set_selection_mode_enabled(enabled)
        self.table.set_selection_mode_enabled(enabled)
        self.status_label.setText(
            "选取模式已开启：左键拖选，右键拖动取消。" if enabled
            else "浏览模式已开启：点击照片查看细节；查看器内滚轮缩放、拖拽平移。"
        )

    def apply_selection_to_current_view(self):
        self.updating_selection = True
        try:
            target_view = self.grid if self.stack.currentIndex() == 0 else self.table
            sm = target_view.selectionModel()
            model = target_view.model()
            if sm is None or model is None:
                return
            rows = [self.visible_row_by_id[iid] for iid in self.selected_ids if iid in self.visible_row_by_id]
            selection = QItemSelection()
            max_col = 0
            if target_view is self.table:
                max_col = max(0, self.table_model.columnCount() - 1)
            for a, b in compact_ranges(rows):
                selection.select(model.index(a, 0), model.index(b, max_col))
            blocker = QSignalBlocker(sm)
            sm.select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            del blocker
            target_view.viewport().update()
        finally:
            self.updating_selection = False

    def on_grid_range_dragged(self, anchor: int, current: int, finished: bool):
        if finished:
            self.on_grid_selection_changed()
            return
        ranges = getattr(self.grid, "_active_ranges", [])
        selected_count, selected_files, selected_size = self._visible_range_stats(ranges)
        self.update_stats_label(selected_count, selected_files, selected_size)
        a, b = sorted((anchor, current))
        drag_action = getattr(self.grid, "_drag_action", "toggle")
        action = "反选" if drag_action == "toggle" else ("取消选择" if drag_action == "deselect" else "选择")
        self.status_label.setText(
            f"正在{action}范围：第 {a + 1} 到第 {b + 1} 项；"
            f"当前预览已选 {selected_count} 项，{selected_files} 个文件，{format_bytes(selected_size)}。"
        )

    def on_grid_selection_changed(self):
        if self.updating_selection:
            return
        sm = self.grid.selectionModel()
        if sm is None:
            return
        old_ids = set(self.selected_ids)
        new_ids: set[str] = set()
        for idx in sm.selectedRows():
            iid = idx.data(ITEM_ID_ROLE)
            if iid:
                new_ids.add(iid)
        self._record_selection_diff(old_ids, new_ids, origin="grid")
        self.selected_ids = new_ids
        self.update_stats_label()
        self.status_label.setText(f"当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项。")

    def on_table_selection_changed(self):
        if self.updating_selection:
            return
        sm = self.table.selectionModel()
        if sm is None:
            return
        old_ids = set(self.selected_ids)
        new_ids: set[str] = set()
        for idx in sm.selectedRows():
            iid = idx.data(ITEM_ID_ROLE)
            if iid:
                new_ids.add(iid)
        self._record_selection_diff(old_ids, new_ids, origin="table")
        self.selected_ids = new_ids
        self.update_stats_label()
        self.status_label.setText(f"当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项。")

    def select_all_visible(self):
        self._set_selection_state(set(self.visible_ids), record_history=True, origin="select_all")

    def clear_selection(self, record_history: bool = True):
        # Let the active view perform its own deselection transition.  This is
        # especially important for right-click clearing: the photo wall now gets a
        # real blue-frame retreat animation instead of the selection simply vanishing.
        old_ids = set(self.selected_ids)
        if record_history:
            self._record_selection_diff(old_ids, set(), origin="clear")
        target_view = self.grid if self.stack.currentIndex() == 0 else self.table
        self.updating_selection = True
        try:
            try:
                target_view._restore_selected_rows(set())
            except Exception:
                pass
            self.selected_ids.clear()
        finally:
            self.updating_selection = False
        self.update_stats_label()
        self.status_label.setText(f"当前显示 {len(self.visible_ids)} 项；已选择 0 项。")

    # ---------- live preview / detail viewer ----------

    def on_grid_index_entered(self, index):
        # Compatibility method for old signal wiring.  It must not call
        # on_hover_item_changed() directly, otherwise it bypasses hover debouncing.
        try:
            self.grid._set_hover_item_from_index(index if index and index.isValid() else QModelIndex())
        except Exception:
            pass

    def on_hover_item_changed(self, item_id: str):
        if not bool(self.settings_service.get("live_photo.hover_play", True)):
            self.stop_live_preview()
            return
        if not item_id:
            self.stop_live_preview()
            return
        item = self.item_map.get(item_id)
        if item is None:
            self.stop_live_preview()
            return
        # Hovered item should feel "hot": decode/load its thumbnail through a tiny
        # priority lane so it does not wait behind thousands of background thumbnails.
        self.request_priority_thumbnail(self.generation, item)
        if not item.is_live:
            self.stop_live_preview()
            return
        self.start_live_preview(item)

    def on_grid_hover_item_changed(self, item_id: str):
        # Backward-compatible alias for older signal connections.
        self.on_hover_item_changed(item_id)

    def start_live_preview(self, item: PhotoItemData):
        old = self.live_preview_item_id
        if old != item.item_id:
            self.live_preview_frame_index = 0
        self.live_preview_item_id = item.item_id
        if old and old != item.item_id:
            self.notify_grid_item(old)

        cached_frames = self.live_frame_cache.get(item.item_id)
        if cached_frames:
            if not self.live_preview_timer.isActive():
                self.live_preview_timer.start()
            self.notify_grid_item(item.item_id)
            self.status_label.setText(f"正在播放 LIVE 预览：{item.display_name}（{len(cached_frames)} 帧）")
            return

        if item.item_id in self.live_frame_failed:
            self.status_label.setText("该 LIVE 的 MOV 上次解码失败。可尝试安装/更新：pip install -U imageio-ffmpeg imageio opencv-python")
            self.notify_grid_item(item.item_id)
            return

        self.request_live_preview_frames(self.generation, item)
        # Repaint immediately so the hover state is acknowledged even while frames are
        # still decoding in the background, and make the status bar respond instantly.
        self.notify_grid_item(item.item_id)

    def stop_live_preview(self):
        old = self.live_preview_item_id
        self.live_preview_item_id = None
        self.live_preview_frame_index = 0
        self.live_preview_timer.stop()
        if old:
            self.notify_grid_item(old)

    def request_live_preview_frames(self, gen: int, item: PhotoItemData):
        if item.item_id in self.live_frame_cache:
            return
        if item.item_id in self.live_frame_requested:
            self.status_label.setText(f"正在优先解码 LIVE 预览：{item.display_name}……")
            return
        mov = find_live_video_file(item)
        if mov is None:
            self.live_frame_failed.add(item.item_id)
            self.status_label.setText(f"找不到该实况照片对应的 MOV：{item.display_name}")
            return
        self.live_frame_requested.add(item.item_id)
        self.status_label.setText(f"正在优先解码 LIVE 预览：{item.display_name}……")
        # Use a dedicated high-priority executor instead of the thumbnail pool; otherwise
        # a folder-level thumbnail backlog can make LIVE preview feel like it never starts.
        fut = self.live_executor.submit(make_live_preview_frames_bytes, mov, (ICON_SIZE, ICON_SIZE), LIVE_PREVIEW_FRAME_COUNT, LIVE_PREVIEW_DECODE_TIMEOUT)
        fut.add_done_callback(lambda f, g=gen, iid=item.item_id: self._live_frames_callback(g, iid, f))

    def _live_frames_callback(self, gen: int, item_id: str, future):
        if getattr(self, "_closing", False):
            return
        try:
            frames = future.result()
        except Exception:
            frames = []
        self.signals.live_frames_ready.emit(gen, item_id, frames)

    def on_live_frames_ready(self, gen: int, item_id: str, frames_obj):
        if getattr(self, "_closing", False):
            return
        self.live_frame_requested.discard(item_id)
        if gen != self.generation or item_id not in self.item_map:
            return
        pixmaps: list[QPixmap] = []
        for data in list(frames_obj or []):
            pix = QPixmap()
            if pix.loadFromData(data, "PNG") and not pix.isNull():
                pixmaps.append(pix)
        if pixmaps:
            self.live_frame_failed.discard(item_id)
            self.live_frame_cache[item_id] = pixmaps
            if self.live_preview_item_id == item_id:
                self.live_preview_frame_index = 0
                if not self.live_preview_timer.isActive():
                    self.live_preview_timer.start()
                self.notify_grid_item(item_id)
                self.status_label.setText(f"LIVE 预览已启动：{self.item_map[item_id].display_name}（{len(pixmaps)} 帧）")
        else:
            self.live_frame_failed.add(item_id)
            if self.live_preview_item_id == item_id:
                self.status_label.setText("未能解码该 LIVE 的 MOV。建议安装或更新：pip install -U imageio-ffmpeg imageio opencv-python")

    def advance_live_preview_frame(self):
        item_id = self.live_preview_item_id
        if not item_id:
            self.live_preview_timer.stop()
            return
        frames = self.live_frame_cache.get(item_id)
        if not frames:
            self.live_preview_timer.stop()
            return
        self.live_preview_frame_index = (self.live_preview_frame_index + 1) % len(frames)
        self.notify_grid_item(item_id)

    def notify_grid_item(self, item_id: str):
        row = self.visible_row_by_id.get(item_id)
        if row is not None:
            self.grid_model.notify_rows([row], [Qt.DecorationRole, THUMB_READY_ROLE])
            self.table_model.notify_rows([row], [Qt.DecorationRole, THUMB_READY_ROLE])
            try:
                idx = self.grid_model.index(row, 0)
                rect = self.grid.visualRect(idx)
                if rect.isValid():
                    self.grid.viewport().update(rect)
                else:
                    self.grid.viewport().update()
            except Exception:
                self.grid.viewport().update()
            try:
                tidx = self.table_model.index(row, 0)
                rect = self.table.visualRect(tidx)
                if rect.isValid():
                    self.table.viewport().update(rect)
                else:
                    self.table.viewport().update()
            except Exception:
                self.table.viewport().update()

    def _iter_detail_windows(self):
        alive = []
        for dlg in list(getattr(self, "detail_windows", set())):
            try:
                if dlg is not None and not getattr(dlg, "_closing", False):
                    alive.append(dlg)
            except RuntimeError:
                try:
                    self.detail_windows.discard(dlg)
                except Exception:
                    pass
        return alive

    def _notify_detail_windows_items_removed(self, item_ids: set[str] | list[str]):
        ids = {str(iid) for iid in (item_ids or []) if iid}
        if not ids:
            return
        for dlg in self._iter_detail_windows():
            try:
                dlg.on_owner_items_removed(ids)
            except RuntimeError:
                self.detail_windows.discard(dlg)
            except Exception:
                pass

    def _notify_detail_windows_items_trashed(self, item_ids: set[str] | list[str]):
        ids = {str(iid) for iid in (item_ids or []) if iid}
        if not ids:
            return
        for dlg in self._iter_detail_windows():
            try:
                dlg.on_owner_items_trashed(ids)
            except RuntimeError:
                self.detail_windows.discard(dlg)
            except Exception:
                pass

    def _notify_detail_windows_items_restored(self, item_ids: set[str] | list[str]):
        ids = {str(iid) for iid in (item_ids or []) if iid}
        if not ids:
            return
        for dlg in self._iter_detail_windows():
            try:
                dlg.on_owner_items_restored(ids)
            except RuntimeError:
                self.detail_windows.discard(dlg)
            except Exception:
                pass

    def open_detail_from_model_index(self, index: QModelIndex):
        """Native Qt double-click fallback for custom grid/table interactions."""
        try:
            if index.isValid():
                self.open_item_detail(str(index.data(ITEM_ID_ROLE) or ""))
        except Exception as exc:
            self.status_label.setText(f"无法打开预览：{exc}")

    def open_item_detail(self, item_id: str):
        if not item_id or item_id not in self.item_map:
            return
        now = time.monotonic()
        if (
            getattr(self, "_last_detail_open_item_id", None) == item_id
            and now - float(getattr(self, "_last_detail_open_time", 0.0)) < 0.35
        ):
            return
        self._last_detail_open_item_id = item_id
        self._last_detail_open_time = now
        ordered_ids = list(self.visible_ids)
        if item_id not in ordered_ids:
            ordered_ids = [item.item_id for item in self.all_items]
        # Keep the navigation order stable, but do not copy the entire global
        # item map or thumbnail cache.  Copying self.icon_cache is especially
        # expensive because it may hold thousands of QPixmap entries.  A minimal
        # item snapshot plus a shared read-through thumbnail cache gives the same
        # non-modal safety without making preview opening feel slow.
        item_snapshot = {iid: self.item_map[iid] for iid in ordered_ids if iid in self.item_map}
        if item_id not in item_snapshot:
            item_snapshot[item_id] = self.item_map[item_id]
        dlg = ImageDetailDialog(
            item_id,
            ordered_ids,
            item_snapshot,
            fallback_pixmaps=self.icon_cache,
            parent=self,
            trash_context=self.is_trash_view(),
        )
        try:
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        except Exception:
            pass
        self.detail_windows.add(dlg)
        dlg_ref = weakref.ref(dlg)

        def _forget_detail_window(*_args, ref=dlg_ref):
            obj = ref()
            if obj is not None:
                try:
                    self.detail_windows.discard(obj)
                except Exception:
                    pass

        try:
            dlg.destroyed.connect(_forget_detail_window)
        except Exception:
            pass
        dlg.show()
        try:
            dlg.raise_()
            dlg.activateWindow()
        except Exception:
            pass

    # ---------- background results ----------

    def on_priority_thumb_done(self, gen: int, item_id: str, data: bytes):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation or item_id not in self.item_map:
            return
        self.hover_thumb_requested.discard(item_id)
        pix = QPixmap()
        if not pix.loadFromData(data, "PNG") or pix.isNull():
            pix = self.placeholder_icon
        self.icon_cache[item_id] = pix
        self.notify_grid_item(item_id)

    def on_thumb_failed(self, gen: int, item_id: str, message: str):
        if getattr(self, "_closing", False) or gen != self.generation:
            return
        if item_id not in self.thumb_failed_ids:
            self.thumb_failed_ids.add(item_id)
            self.thumb_failure_count = len(self.thumb_failed_ids)
        item = self.item_map.get(item_id)
        display = item.display_name if item is not None else item_id
        detail = f"{display}: {message}"
        if len(self.thumb_failure_examples) < 5 and detail not in self.thumb_failure_examples:
            self.thumb_failure_examples.append(detail)
        try:
            with (app_state_dir() / "thumbnail_errors.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {detail}\n")
        except Exception:
            pass
        self.status_label.setText(
            f"有 {self.thumb_failure_count} 张缩略图解码失败；详情见 {APP_STATE_DIR_NAME}/thumbnail_errors.log。"
        )

    def on_thumb_done(self, gen: int, item_id: str, data: bytes):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation or item_id not in self.item_map:
            return
        self.pending_thumb_data[item_id] = data
        self.thumb_done_count += 1
        if self.thumb_done_count % 20 == 0 or self.thumb_done_count >= self.thumb_total:
            self.thumb_progress.setValue(self.thumb_done_count)
            suffix = f" · 失败 {self.thumb_failure_count}" if self.thumb_failure_count else ""
            self.thumb_label.setText(f"缩略图：{self.thumb_done_count}/{self.thumb_total}{suffix}")
        if self.thumb_total > 0 and self.thumb_done_count >= self.thumb_total:
            QTimer.singleShot(900, self._hide_activity_strip_if_idle)
        if not self.thumb_flush_timer.isActive():
            self.thumb_flush_timer.start(40)

    def flush_pending_thumbnail_updates(self):
        if getattr(self, "_closing", False):
            return
        if not self.pending_thumb_data:
            return
        batch = []
        # Keep UI animation responsive under heavy decoding load.  When tile/check
        # animations are active, flush fewer thumbnails per GUI tick so painting
        # work cannot monopolize the event loop.
        try:
            animating = bool(self.grid._tile_anim_timer.isActive() or self.table._check_anim_timer.isActive())
        except Exception:
            animating = False
        flush_limit = 28 if animating else THUMB_FLUSH_BATCH
        for item_id in list(self.pending_thumb_data.keys())[:flush_limit]:
            batch.append((item_id, self.pending_thumb_data.pop(item_id)))
        rows_to_update = []
        for item_id, data in batch:
            pix = QPixmap()
            if not pix.loadFromData(data, "PNG") or pix.isNull():
                pix = self.placeholder_live_icon if self.item_map.get(item_id, None) and self.item_map[item_id].is_live else self.placeholder_icon
            self.icon_cache[item_id] = pix
            row = self.visible_row_by_id.get(item_id)
            if row is not None:
                rows_to_update.append(row)
        # THUMB_READY_ROLE must be emitted too; otherwise the delegate may keep
        # drawing the placeholder even though DecorationRole already changed.
        self.grid_model.notify_rows(rows_to_update, [Qt.DecorationRole, THUMB_READY_ROLE])
        self.table_model.notify_rows(rows_to_update, [Qt.DecorationRole, THUMB_READY_ROLE])
        self.thumb_progress.setValue(self.thumb_done_count)
        suffix = f" · 失败 {self.thumb_failure_count}" if self.thumb_failure_count else ""
        self.thumb_label.setText(f"缩略图：{self.thumb_done_count}/{self.thumb_total}{suffix}")
        if self.pending_thumb_data:
            self.thumb_flush_timer.start(55 if animating else 15)

    def on_meta_done(self, gen: int, item_id: str, dt_obj):
        if getattr(self, "_closing", False):
            return
        if gen != self.generation or item_id not in self.item_map:
            return
        item = self.item_map[item_id]
        metadata = dt_obj if isinstance(dt_obj, dict) else {}
        shot_time = (
            metadata.get("shot_time")
            if metadata
            else dt_obj
        )
        if metadata:
            item.camera_make = str(metadata.get("camera_make") or "")
            item.camera_model = str(metadata.get("camera_model") or "")
            item.gps_latitude = metadata.get("gps_latitude")
            item.gps_longitude = metadata.get("gps_longitude")
            item.image_width = int(metadata.get("image_width") or 0)
            item.image_height = int(metadata.get("image_height") or 0)
        if isinstance(shot_time, datetime):
            item.shot_time = shot_time
            item.time_source = "EXIF拍摄时间（后台补齐）"
            row = self.visible_row_by_id.get(item_id)
            if row is not None:
                self.grid_model.notify_rows([row], [Qt.ToolTipRole])
                self.table_model.notify_rows([row], [Qt.DisplayRole, Qt.ToolTipRole])
            self.cache_info_for_item(item, meta_state="exif")
            self.request_persistent_state_save(item_info=True, delay_ms=1200)
            if self.auto_resort_check.isChecked() and not self.refresh_timer.isActive():
                self.refresh_timer.start(3000)
        else:
            # Persist only the negative metadata result for this exact signature.
            # The displayed quick time remains freshly computed from the current
            # representative image file, so external folder updates are still reflected.
            self.cache_info_for_item(item, meta_state="no_exif")
            self.request_persistent_state_save(item_info=True, delay_ms=2500)
        self.meta_done_count += 1
        if self.meta_done_count >= len(self.all_items):
            # EXIF 时间补齐后刷新一次时间/元数据分类，避免每张照片都重建分类树。
            self.classification_refresh_timer.start(300)
        if self.meta_done_count % 50 == 0 or self.meta_done_count >= len(self.all_items):
            self.status_label.setText(
                f"当前显示 {len(self.visible_ids)} 项；已选择 {len(self.selected_ids)} 项；"
                f"EXIF 时间 {self.meta_done_count}/{len(self.all_items)}。"
            )

    # ---------- context menu / export / metadata ----------

    def _selected_visible_item_ids(self) -> list[str]:
        return [iid for iid in self.visible_ids if iid in self.selected_ids and iid in self.item_map]

    def edit_selected_tags(self):
        ids = self._selected_visible_item_ids()
        if not ids:
            return
        items = [self.item_map[item_id] for item_id in ids]
        metadata = self.photo_database.load_user_metadata(
            [item.stable_key or item.item_id for item in items]
        )
        existing = []
        if len(items) == 1:
            key = items[0].stable_key or items[0].item_id
            existing = list(metadata.get(key).tags if key in metadata else items[0].p2_manual_tags)
        text, accepted = QInputDialog.getText(
            self,
            "编辑照片标签",
            "使用逗号或分号分隔标签：",
            QLineEdit.Normal,
            ", ".join(existing),
        )
        if not accepted:
            return
        tags = []
        seen = set()
        for value in str(text).replace("；", ";").replace("，", ",").replace(";", ",").split(","):
            tag = " ".join(value.strip().split())
            if tag and tag.casefold() not in seen:
                tags.append(tag)
                seen.add(tag.casefold())
        saved = 0
        for item in items:
            key = item.stable_key or item.item_id
            if self.photo_database.set_tags(key, tags):
                item.p2_manual_tags = list(tags)
                saved += 1
        self.status_label.setText(f"已为 {saved} 张照片保存标签：{'、'.join(tags) if tags else '无'}")
        self.schedule_auto_classification(force=True)
        self.apply_filter()

    def show_item_context_menu(self, item_id: str, global_pos: QPoint, temporary_selection: bool = False):
        if not item_id or item_id not in self.item_map:
            if temporary_selection:
                self.clear_selection(record_history=False)
            return
        if temporary_selection:
            # The view has already applied the temporary visual selection. Keep the
            # central selected_ids in sync so menu actions work on this one item,
            # but do not let a context-menu-only temporary highlight pollute Ctrl+Z.
            self._discard_last_temporary_selection_action(item_id)
            self.selected_ids = {item_id}
            self.update_stats_label()
        elif item_id not in self.selected_ids or not self.selected_ids:
            self.clear_selection()
            return

        in_trash = self.is_trash_view()
        current_item = self.item_map.get(item_id)
        menu = QMenu(self)
        copy_photo_action = menu.addAction("复制照片")
        move_action = menu.addAction("移动选中项…")
        export_action = menu.addAction("导出已选图…")
        reorder_export_action = menu.addAction("批量重排导出…")
        metadata_action = menu.addAction("查看当前图元数据…")
        menu.addSeparator()
        tag_action = menu.addAction("编辑标签…")
        lightroom_path = str(self.settings_service.get("integration.lightroom_path", "") or "")
        photoshop_path = str(self.settings_service.get("integration.photoshop_path", "") or "")
        lightroom_action = menu.addAction("在 Lightroom 中打开") if Path(lightroom_path).is_file() else None
        photoshop_action = menu.addAction("在 Photoshop 中打开") if Path(photoshop_path).is_file() else None
        default_viewer_action = menu.addAction("用默认查看器打开") if bool(self.settings_service.get("integration.default_viewer", True)) else None
        open_with_action = menu.addAction("用其他应用打开…")
        reveal_action = menu.addAction("在资源管理器中显示")
        relocate_action = None
        bind_mov_action = None
        if current_item is not None and current_item.is_live and not item_is_mov_only(current_item):
            menu.addSeparator()
            relocate_action = menu.addAction("实况重定位…")
            relocate_action.setToolTip("只处理当前右键指向的这一项，不处理当前选区。")
        if current_item is not None and item_is_mov_only(current_item) and not in_trash:
            menu.addSeparator()
            bind_mov_action = menu.addAction("绑定未归属 MOV…")
            bind_mov_action.setToolTip("手动选择同名照片，把这个 MOV 绑定为实况组件。")
        menu.addSeparator()
        if in_trash:
            restore_action = menu.addAction("从垃圾箱恢复")
            delete_action = menu.addAction("删除到“已删除”文件夹…")
        else:
            restore_action = None
            delete_action = menu.addAction("删除（移入垃圾箱）")

        action = menu.exec(global_pos)
        try:
            if action == copy_photo_action:
                self.copy_selected_photos_to_clipboard()
            elif action == move_action:
                self.move_selected()
            elif action == export_action:
                self.export_selected_items()
            elif action == reorder_export_action:
                self.reorder_export_selected_items()
            elif action == metadata_action:
                self.show_item_metadata(item_id)
            elif action == tag_action:
                self.edit_selected_tags()
            elif lightroom_action is not None and action == lightroom_action:
                self.open_selected_in_application(lightroom_path)
            elif photoshop_action is not None and action == photoshop_action:
                self.open_selected_in_application(photoshop_path)
            elif default_viewer_action is not None and action == default_viewer_action:
                self.open_selected_with_default_viewer()
            elif action == open_with_action:
                self.open_selected_with_dialog()
            elif action == reveal_action:
                self.reveal_selected_in_explorer()
            elif relocate_action is not None and action == relocate_action:
                self.relocate_live_mov_for_item(item_id)
            elif bind_mov_action is not None and action == bind_mov_action:
                self.bind_unowned_mov_item(item_id)
            elif restore_action is not None and action == restore_action:
                self.restore_selected_from_trash()
            elif action == delete_action:
                if in_trash:
                    self.delete_selected_to_deleted_folder()
                else:
                    self.move_selected_to_trash()
        finally:
            if temporary_selection:
                # Clear the one-item temporary selection after the menu closes.
                # Also force any press visual state to end; on Windows the context
                # menu can eat the right-button release event.
                row = self.visible_row_by_id.get(item_id)
                try:
                    if row is not None:
                        self.grid._release_press_effect_for_row(row)
                        self.table._release_press_effect_for_row(row)
                except Exception:
                    pass
                self.clear_selection(record_history=False)

    def selected_representative_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for item_id in self._selected_visible_item_ids():
            item = self.item_map.get(item_id)
            if item is None:
                continue
            path = Path(item.representative_image)
            if not path.is_file():
                continue
            key = normalize_item_path(path)
            if key not in seen:
                seen.add(key)
                paths.append(path)
        return paths

    def open_selected_in_application(self, executable: str):
        paths = self.selected_representative_paths()
        if not paths or not Path(executable).is_file():
            return
        try:
            subprocess.Popen(
                [str(executable), *[str(path) for path in paths]],
                cwd=str(Path(executable).parent),
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except Exception as exc:
            QMessageBox.warning(self, "无法打开外部应用", str(exc))

    def open_selected_with_default_viewer(self):
        for path in self.selected_representative_paths():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_selected_with_dialog(self):
        paths = self.selected_representative_paths()
        if not paths:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(paths[0])])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0])))
        except Exception as exc:
            QMessageBox.warning(self, "无法打开系统选择器", str(exc))

    def reveal_selected_in_explorer(self):
        paths = self.selected_representative_paths()
        if not paths:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", str(paths[0])])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))
        except Exception as exc:
            QMessageBox.warning(self, "无法打开资源管理器", str(exc))

    def copy_selected_photos_to_clipboard(self):
        """把当前选中照片文件放入系统剪贴板，便于在资源管理器中直接粘贴。"""
        item_ids = self._selected_visible_item_ids()
        if not item_ids:
            return
        paths: list[Path] = []
        seen: set[str] = set()
        for iid in item_ids:
            item = self.item_map.get(iid)
            if item is None:
                continue
            for path in item.files:
                try:
                    if not path.exists() or not path.is_file():
                        continue
                    key = normalize_item_path(path)
                except Exception:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path)
        if not paths:
            self.status_label.setText("未找到可复制的照片文件。")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        try:
            # Windows Explorer reads this preferred drop effect as “copy” rather
            # than “move”.  Other platforms safely ignore the private MIME.
            mime.setData(
                'application/x-qt-windows-mime;value="Preferred DropEffect"',
                bytes((1, 0, 0, 0)),
            )
        except Exception:
            pass
        QApplication.clipboard().setMimeData(mime)
        item_count = len(item_ids)
        file_count = len(paths)
        self.status_label.setText(
            f"已复制 {item_count} 项、{file_count} 个文件到剪贴板，可直接粘贴。"
        )

    def _create_mov_only_trash_item_for_live(self, item: PhotoItemData, mov: Path) -> PhotoItemData:
        key = stable_key_for_files([mov])
        return PhotoItemData(
            item_id=f"trashmov_{key[:16]}",
            display_name=f"实况 MOV：{mov.name}",
            files=[mov],
            size_bytes=group_size_bytes([mov]),
            representative_image=item.representative_image,
            is_live=False,
            item_type="垃圾箱中的实况 MOV",
            shot_time=get_fast_group_time([item.representative_image, mov]),
            time_source="文件修改时间（快速）",
            source_folder=mov.parent,
            stable_key=key,
            file_signature=signature_for_files([mov]),
            item_kind="mov_only",
            bound_image_paths=[item.representative_image],
            needs_binding=False,
        )

    def _mark_live_mov_to_trash(self, item: PhotoItemData) -> str | None:
        """Put only the MOV component of a live item into the program trash."""
        mov = find_live_video_file(item)
        if mov is None or not mov.exists():
            return None
        old_key = item.stable_key
        mov_item = self._create_mov_only_trash_item_for_live(item, mov)
        self.trash_records[mov_item.stable_key] = {
            "paths": item_paths_for_state([mov]),
            "display_name": mov_item.display_name,
            "kind": "live_mov",
            "bound_image_paths": item_paths_for_state([item.representative_image]),
            "trashed_at": time.time(),
        }
        self._append_trash_journal("trash", mov_item)
        # The still photo remains in the normal library; remove the MOV from its
        # current item so the visible state matches the pending trash state now.
        item.files = [f for f in item.files if normalize_item_path(f) != normalize_item_path(mov)]
        item.is_live = False
        item.size_bytes = group_size_bytes(item.files)
        item.item_type = f"普通照片 ({item.representative_image.suffix.upper().lstrip('.')})"
        assign_stable_identity(item)
        self._remove_cache_for_item_key(old_key)
        self._remove_cache_for_item_key(item.stable_key)
        if mov_item.item_id not in self.item_map:
            self.all_items.append(mov_item)
            self.item_map[mov_item.item_id] = mov_item
        self.trash_ids.add(mov_item.item_id)
        self._persistent_state_dirty = True
        return mov_item.item_id

    def move_items_to_trash_by_ids(self, item_ids: list[str], show_message: bool = True, record_history: bool = True) -> int:
        self.ensure_trash_records_loaded()
        valid_ids = [iid for iid in item_ids if iid in self.item_map]
        if record_history and valid_ids:
            self._push_history_action({
                "type": "trash_many",
                "items": self._history_item_snapshots(valid_ids),
            })
        if not valid_ids:
            if show_message:
                QMessageBox.information(self, "提示", "请先选择要删除的项目。")
            return 0
        changed = False
        count = 0
        notify_trashed: list[str] = []
        for iid in valid_ids:
            item = self.item_map.get(iid)
            if item is None:
                continue
            # In normal view, deleting a Live Photo means deleting only its MOV
            # motion component into the program trash. The still image remains.
            if (not self.is_trash_view()) and item.is_live and not item_is_mov_only(item):
                mov_iid = self._mark_live_mov_to_trash(item)
                if mov_iid:
                    count += 1
                    changed = True
                    notify_trashed.append(mov_iid)
                    continue
            self.trash_ids.add(iid)
            count += 1
            notify_trashed.append(iid)
            if item.stable_key:
                self._append_trash_journal("trash", item)
                self.trash_records[item.stable_key] = {
                    "paths": item_paths_for_state(item.files),
                    "display_name": item.display_name,
                    "kind": "live_mov" if item_is_mov_only(item) else "item",
                    "bound_image_paths": item_paths_for_state(getattr(item, "bound_image_paths", []) or []),
                    "trashed_at": time.time(),
                }
                changed = True
        if changed:
            self._persistent_state_dirty = True
            self.save_trash_records()
        self.selected_ids.difference_update(valid_ids)
        self.apply_filter()
        if notify_trashed:
            self._notify_detail_windows_items_trashed(notify_trashed)
        if count:
            self.schedule_auto_classification()
        if show_message:
            self.status_label.setText(
                f"已将 {count} 项移入垃圾箱。实况照片在普通视图下只会把 MOV 组件放入垃圾箱，照片本体仍保留。"
            )
        return count

    def move_selected_to_trash(self):
        selected_ids = self._selected_visible_item_ids()
        if not selected_ids:
            QMessageBox.information(self, "提示", "请先选择要删除的项目。")
            return
        self.move_items_to_trash_by_ids(selected_ids, show_message=True)

    def _candidate_stills_for_mov_restore(self, mov_item: PhotoItemData) -> list[PhotoItemData]:
        mov = find_live_video_file(mov_item) or (mov_item.files[0] if mov_item.files else None)
        if mov is None:
            return []
        bound_norms = {normalize_item_path(p) for p in (getattr(mov_item, "bound_image_paths", []) or [])}
        candidates: list[PhotoItemData] = []
        for item in self.all_items:
            if item_is_mov_only(item):
                continue
            if item.item_id in self.trash_ids:
                continue
            if item.representative_image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            same_stem = item.representative_image.parent == mov.parent and item.representative_image.stem == mov.stem
            recorded = normalize_item_path(item.representative_image) in bound_norms
            if same_stem or recorded:
                candidates.append(item)
        candidates.sort(key=lambda x: (0 if normalize_item_path(x.representative_image) in bound_norms else 1, x.display_name.lower()))
        return candidates

    def _select_restore_target_for_mov(self, mov_item: PhotoItemData, candidates: list[PhotoItemData]) -> PhotoItemData | None:
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            QMessageBox.warning(self, "无法恢复实况 MOV", "找不到与此 MOV 同名、同目录的照片。请先把对应照片放回原位置，或重新扫描后再试。")
            return None
        # Reuse the target selector. It shows the candidate photos and avoids guessing
        # when IMG_1234.HEIC / IMG_1234.JPG 等不同扩展名同时存在。
        dialog = LiveRelocationTargetDialog(self, mov_item.item_id, [i.item_id for i in candidates], self)
        dialog.setWindowTitle("选择要绑定回的照片 - 恢复实况 MOV")
        try:
            dialog.ok_btn.setText("恢复并绑定")
        except Exception:
            pass
        if dialog.exec() != QDialog.Accepted:
            return None
        return self.item_map.get(dialog.selected_item_id)

    def _restore_mov_only_trash_item(self, mov_item: PhotoItemData) -> bool:
        mov = find_live_video_file(mov_item) or (mov_item.files[0] if mov_item.files else None)
        if mov is None or not mov.exists():
            QMessageBox.warning(self, "无法恢复", "此实况 MOV 已经不存在，垃圾箱条目将被清理。")
            if mov_item.stable_key:
                self._append_trash_journal("restore", mov_item)
                self.trash_records.pop(mov_item.stable_key, None)
            self.trash_ids.discard(mov_item.item_id)
            self.item_map.pop(mov_item.item_id, None)
            self.all_items = [x for x in self.all_items if x.item_id != mov_item.item_id]
            return True
        candidates = self._candidate_stills_for_mov_restore(mov_item)
        target = self._select_restore_target_for_mov(mov_item, candidates)
        if target is None:
            return False
        if target.is_live and find_live_video_file(target) is not None:
            QMessageBox.warning(self, "无法恢复", "目标照片已经绑定了另一个 MOV。请先处理目标照片的实况 MOV，再恢复此项。")
            return False
        old_target_key = target.stable_key
        target.files = list(target.files) + [mov]
        target.is_live = True
        target.size_bytes = group_size_bytes(target.files)
        ext_set = sorted({f.suffix.upper().lstrip(".") for f in target.files})
        target.item_type = f"LIVE 实况照片 ({' + '.join(ext_set)})"
        assign_stable_identity(target)
        self._remove_cache_for_item_key(old_target_key)
        self._remove_cache_for_item_key(target.stable_key)
        self._remove_cache_for_item_key(mov_item.stable_key)
        if mov_item.stable_key:
            self._append_trash_journal("restore", mov_item)
            self.trash_records.pop(mov_item.stable_key, None)
        self.trash_ids.discard(mov_item.item_id)
        self.item_map.pop(mov_item.item_id, None)
        self.all_items = [x for x in self.all_items if x.item_id != mov_item.item_id]
        return True

    def restore_items_from_trash_by_ids(self, item_ids: list[str], show_message: bool = True, record_history: bool = True) -> int:
        self.ensure_trash_records_loaded()
        valid_ids = [iid for iid in item_ids if iid in self.item_map]
        if record_history and valid_ids:
            self._push_history_action({
                "type": "restore_many",
                "items": self._history_item_snapshots(valid_ids),
            })
        if not valid_ids:
            if show_message:
                QMessageBox.information(self, "提示", "请先选择要恢复的项目。")
            return 0
        count = 0
        restored_ids: list[str] = []
        for iid in valid_ids:
            item = self.item_map.get(iid)
            if item is None:
                continue
            if item_is_mov_only(item):
                if self._restore_mov_only_trash_item(item):
                    count += 1
                    restored_ids.append(iid)
                continue
            self.trash_ids.discard(iid)
            if item.stable_key:
                self._append_trash_journal("restore", item)
                self.trash_records.pop(item.stable_key, None)
            count += 1
            restored_ids.append(iid)
        self.save_trash_records()
        self.selected_ids.difference_update(valid_ids)
        self.apply_filter()
        if count:
            self._notify_detail_windows_items_restored(restored_ids)
            self.schedule_auto_classification()
        if show_message:
            self.status_label.setText(f"已从垃圾箱恢复 {count} 项。")
        return count

    def restore_selected_from_trash(self):
        selected_ids = self._selected_visible_item_ids()
        return self.restore_items_from_trash_by_ids(selected_ids, show_message=True, record_history=True)

    def _confirm_delete_to_deleted_folder(self, title: str, items: list[PhotoItemData]) -> bool:
        total_files = sum(len(item.files) for item in items)
        total_size = sum(item.size_bytes for item in items)
        target_dir = deleted_items_dir()
        preview = "\n".join(f"  {item.display_name}" for item in items[:8])
        if len(items) > 8:
            preview += "\n  ……"
        reply = QMessageBox.warning(
            self, title,
            f"这不会直接粉碎文件，而是把文件移动到程序目录下的“已删除”文件夹。\n"
            f"移入后会从当前列表和程序垃圾箱记录中移除；如需找回，请手动到该文件夹中取回。\n\n"
            f"目标文件夹：\n{target_dir}\n\n"
            f"项目：{len(items)} 项\n文件：{total_files} 个\n容量：{format_bytes(total_size)}\n\n"
            f"将删除：\n{preview}\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def delete_selected_to_deleted_folder(self):
        selected_ids = self._selected_visible_item_ids()
        if not selected_ids:
            QMessageBox.information(self, "提示", "请先选择要删除的项目。")
            return False
        return self.delete_items_to_deleted_folder_by_ids(selected_ids, title="确认删除到“已删除”文件夹", show_message=True)

    # Backward-compatible name used by older menu wiring; semantics are no longer
    # physical unlink.  Files are moved into the app-local “已删除” folder instead.
    def permanently_delete_selected_items(self):
        return self.delete_selected_to_deleted_folder()

    def delete_all_trash_items(self):
        trash_ids = [item.item_id for item in self.all_items if item.item_id in self.trash_ids and item.item_id in self.item_map]
        if not trash_ids:
            QMessageBox.information(self, "提示", "垃圾箱为空。")
            return False
        return self.delete_items_to_deleted_folder_by_ids(trash_ids, title="确认全部删除到“已删除”文件夹", show_message=True, all_trash=True)

    def delete_items_to_deleted_folder_by_ids(self, item_ids: list[str], title: str = "确认删除到“已删除”文件夹", show_message: bool = True, all_trash: bool = False) -> bool:
        deleting_ids = getattr(self, "deleting_to_deleted_ids", set())
        items = [self.item_map[iid] for iid in item_ids if iid in self.item_map and iid not in deleting_ids]
        if not items:
            if show_message:
                QMessageBox.information(self, "提示", "没有可删除的项目，或项目已经在删除队列中。")
            return False
        if not self._confirm_delete_to_deleted_folder(title, items):
            return False

        ids = {item.item_id for item in items}
        self.deleting_to_deleted_ids.update(ids)
        self.selected_ids.difference_update(ids)
        self._pending_delete_message_flags = {"show_message": bool(show_message), "all_trash": bool(all_trash)}

        # Let tiles disappear immediately. The real file moves happen in a worker;
        # failed items are made visible again in on_file_op_done().
        self.refresh_models_preserve_selection()
        total_files = sum(len([p for p in item.files if p.exists()]) for item in items)
        try:
            self._file_activity_busy = True
            self._show_activity_strip()
            self.file_op_progress.setRange(0, max(1, len(items)))
            self.file_op_progress.setValue(0)
            self.file_op_label.setText(f"文件操作：准备移动 {len(items)} 项 / {total_files} 个文件")
        except Exception:
            pass
        self.status_label.setText(f"正在后台移动 {len(items)} 项到‘已删除’文件夹；界面可继续操作。")

        entries = [{"item_id": item.item_id, "display_name": item.display_name, "files": [str(p) for p in item.files]} for item in items]
        try:
            self.file_op_executor.submit(_move_items_to_deleted_folder_worker, entries, self.signals, {"show_message": bool(show_message), "all_trash": bool(all_trash)})
        except Exception as e:
            self.deleting_to_deleted_ids.difference_update(ids)
            self._file_activity_busy = False
            self.refresh_models_preserve_selection()
            try:
                self.file_op_label.setText("文件操作：空闲")
                self.file_op_progress.setRange(0, 1)
                self.file_op_progress.setValue(0)
            except Exception:
                pass
            if show_message:
                QMessageBox.critical(self, "无法启动删除任务", str(e))
            return False
        return True

    def on_file_op_progress(self, done: int, total: int, message: str):
        try:
            self._file_activity_busy = True
            self._show_activity_strip()
            self.file_op_progress.setRange(0, max(1, int(total)))
            self.file_op_progress.setValue(max(0, min(int(done), max(1, int(total)))))
            self.file_op_label.setText(f"文件操作：{done}/{total}")
            if message:
                self.status_label.setText(message)
        except Exception:
            pass

    def on_file_op_done(self, result: object):
        if not isinstance(result, dict):
            return
        if result.get("op") == "reorder_export":
            self.on_reorder_export_done(result)
            return
        success_ids = {str(x) for x in result.get("success_ids", []) if x}
        stale_ids = {str(x) for x in result.get("stale_ids", []) if x}
        failed_ids = {str(x) for x in result.get("failed_ids", []) if x}
        finished_ids = success_ids | stale_ids
        if finished_ids:
            self.remove_items_after_move(finished_ids, trash_op="delete_to_deleted_dir")
        # Failed items were only hidden from the view. Reveal them again.
        self.deleting_to_deleted_ids.difference_update(finished_ids | failed_ids)
        self.update_total_stats_cache()
        self.refresh_models_preserve_selection()

        deleted_items = int(result.get("deleted_items") or 0)
        moved_files = int(result.get("moved_files") or 0)
        stale_items = int(result.get("stale_items") or 0)
        failed = list(result.get("failed") or [])
        total = int(result.get("total") or 0)
        target = result.get("target_dir") or str(deleted_items_dir())
        try:
            self.file_op_progress.setRange(0, max(1, total))
            self.file_op_progress.setValue(max(1, total))
            self.file_op_label.setText(f"文件操作：完成，但失败 {len(failed)} 项" if failed else "文件操作：完成")
            QTimer.singleShot(1800, self._reset_file_op_progress_if_idle)
        except Exception:
            pass

        flags = getattr(self, "_pending_delete_message_flags", {"show_message": True, "all_trash": False})
        show_message = bool(result.get("show_message", flags.get("show_message", True)))
        all_trash = bool(result.get("all_trash", flags.get("all_trash", False)))
        action_name = "垃圾箱全部删除" if all_trash else "删除"
        if show_message:
            msg = (f"{action_name}完成。\n\n" f"成功移入‘已删除’：{deleted_items} 项\n" f"成功移动文件：{moved_files} 个\n" f"目标文件夹：\n{target}")
            if stale_items:
                msg += f"\n\n已自动清理失效项目：{stale_items} 项"
            if failed:
                msg += f"\n\n失败：{len(failed)} 项\n" + "\n".join(failed[:10])
                if len(failed) > 10:
                    msg += "\n……"
            QMessageBox.information(self, "完成", msg)
        else:
            self.status_label.setText(f"删除到‘已删除’完成，但失败 {len(failed)} 项；失败项已重新显示。" if failed else f"删除到‘已删除’完成：{deleted_items} 项，{moved_files} 个文件。")

    def on_reorder_export_done(self, result: dict):
        failed = list(result.get("failed") or [])
        total = int(result.get("total") or 0)
        copied_items = int(result.get("copied_items") or 0)
        copied_files = int(result.get("copied_files") or 0)
        target = str(result.get("target_dir") or "")
        try:
            self.file_op_progress.setRange(0, max(1, total))
            self.file_op_progress.setValue(max(1, total))
            self.file_op_label.setText(f"文件操作：重排导出完成，但失败 {len(failed)} 项" if failed else "文件操作：重排导出完成")
            QTimer.singleShot(1800, self._reset_file_op_progress_if_idle)
        except Exception:
            pass
        msg = (
            f"批量重排导出完成。\n\n"
            f"成功导出：{copied_items} 项\n"
            f"成功复制文件：{copied_files} 个\n"
            f"目标文件夹：\n{target}"
        )
        if failed:
            msg += f"\n\n失败：{len(failed)} 项\n" + "\n".join(failed[:12])
            if len(failed) > 12:
                msg += "\n……"
        if bool(result.get("show_message", True)):
            QMessageBox.information(self, "完成", msg)
        else:
            self.status_label.setText(f"批量重排导出完成：{copied_items} 项，失败 {len(failed)} 项。")

    def _reset_file_op_progress_if_idle(self):
        try:
            if not getattr(self, "deleting_to_deleted_ids", set()):
                self._file_activity_busy = False
                self.file_op_label.setText("文件操作：空闲")
                self.file_op_progress.setRange(0, 1)
                self.file_op_progress.setValue(0)
                self._hide_activity_strip_if_idle()
        except Exception:
            pass

    def _single_path_signature(self, path: Path) -> str:
        try:
            return signature_for_files([path])
        except Exception:
            return ""

    def _remember_mov_binding(self, mov: Path, image: Path, candidates: list[Path] | None = None):
        """Persist a manual binding for an ambiguous same-stem MOV.

        The MOV is not renamed or moved here.  The next scan will automatically
        apply this record only if the MOV and chosen image still exist, the chosen
        image is still one of the current same-stem candidates, and their cheap
        signatures have not changed.  Otherwise the stale record is dropped and
        the MOV returns to the “未绑定实况 MOV” filter.
        """
        try:
            records = self.ensure_mov_bindings_loaded()
            mov_norm = normalize_item_path(mov)
            image_norm = normalize_item_path(image)
            records[mov_norm] = {
                "mov_path": mov_norm,
                "image_path": image_norm,
                "mov_signature": self._single_path_signature(mov),
                "image_signature": self._single_path_signature(image),
                "candidate_paths": item_paths_for_state(candidates or [image]),
                "updated_at": time.time(),
            }
            self.request_persistent_state_save(mov_bindings=True, delay_ms=120)
        except Exception:
            pass

    def _forget_mov_binding(self, mov: Path | str):
        try:
            records = self.ensure_mov_bindings_loaded()
            mov_norm = normalize_item_path(Path(str(mov)))
            if mov_norm in records:
                records.pop(mov_norm, None)
                self.request_persistent_state_save(mov_bindings=True, delay_ms=120)
        except Exception:
            pass

    def _binding_image_for_ambiguous_mov(self, mov: Path, image_files: list[Path], used_image_norms: set[str] | None = None) -> Path | None:
        """Return the persisted target image for an ambiguous MOV, if still valid."""
        used_image_norms = used_image_norms or set()
        try:
            records = self.ensure_mov_bindings_loaded()
            mov_norm = normalize_item_path(mov)
            rec = records.get(mov_norm)
            if not isinstance(rec, dict):
                return None
            candidate_by_norm = {normalize_item_path(img): img for img in image_files}
            image_norm = normalize_item_path(Path(str(rec.get("image_path") or "")))
            stale = False
            if image_norm not in candidate_by_norm or image_norm in used_image_norms:
                stale = True
            elif not mov.exists() or not candidate_by_norm[image_norm].exists():
                stale = True
            else:
                mov_sig = str(rec.get("mov_signature") or "")
                img_sig = str(rec.get("image_signature") or "")
                if mov_sig and mov_sig != self._single_path_signature(mov):
                    stale = True
                if img_sig and img_sig != self._single_path_signature(candidate_by_norm[image_norm]):
                    stale = True
            if stale:
                records.pop(mov_norm, None)
                self.request_persistent_state_save(mov_bindings=True, delay_ms=600)
                return None
            return candidate_by_norm[image_norm]
        except Exception:
            return None

    def _same_stem_still_candidate_ids_for_mov(self, mov_item: PhotoItemData) -> list[str]:
        mov = find_live_video_file(mov_item) or (mov_item.files[0] if mov_item.files else None)
        if mov is None:
            return []
        ids: list[str] = []
        for item in self.all_items:
            if item.item_id == mov_item.item_id or item_is_mov_only(item):
                continue
            if item.item_id not in self.item_map or item.item_id in self.trash_ids:
                continue
            img = item.representative_image
            if img.suffix.lower() in IMAGE_EXTENSIONS and img.parent == mov.parent and img.stem == mov.stem:
                ids.append(item.item_id)
        return ids

    def bind_unowned_mov_item(self, mov_item_id: str) -> bool:
        """Manually bind an ambiguous/orphan MOV item to one still photo."""
        mov_item = self.item_map.get(mov_item_id)
        if mov_item is None or not item_is_mov_only(mov_item):
            return False
        mov = find_live_video_file(mov_item) or (mov_item.files[0] if mov_item.files else None)
        if mov is None or not mov.exists():
            QMessageBox.warning(self, "无法绑定", "这个 MOV 文件已经不存在。")
            return False
        candidate_ids = self._same_stem_still_candidate_ids_for_mov(mov_item)
        if not candidate_ids:
            QMessageBox.information(self, "没有可绑定照片", "没有找到同目录、同文件名主体的照片。")
            return False
        dialog = LiveRelocationTargetDialog(self, mov_item_id, candidate_ids, self)
        dialog.setWindowTitle("选择要绑定的照片 - 未归属 MOV")
        try:
            dialog.ok_btn.setText("绑定 MOV")
        except Exception:
            pass
        if dialog.exec() != QDialog.Accepted:
            return False
        target = self.item_map.get(dialog.selected_item_id)
        if target is None:
            return False
        if target.is_live:
            QMessageBox.warning(self, "无法绑定", "目标照片已经是实况照片。请先重定位或删除其原有 MOV。")
            return False
        old_key = target.stable_key
        target.files = list(target.files) + [mov]
        target.is_live = True
        target.size_bytes = group_size_bytes(target.files)
        ext_set = sorted({f.suffix.upper().lstrip(".") for f in target.files})
        target.item_type = f"LIVE 实况照片 ({' + '.join(ext_set)})"
        assign_stable_identity(target)
        self._remember_mov_binding(mov, target.representative_image, getattr(mov_item, "bound_image_paths", []) or [target.representative_image])
        self._remove_cache_for_item_key(old_key)
        self._remove_cache_for_item_key(target.stable_key)
        self.item_map.pop(mov_item.item_id, None)
        self.all_items = [x for x in self.all_items if x.item_id != mov_item.item_id]
        self.selected_ids.discard(mov_item.item_id)
        self.update_total_stats_cache()
        self.refresh_models_preserve_selection()
        self._notify_items_updated_after_live_relocation([target.item_id])
        self.status_label.setText(f"已将 {mov.name} 绑定到 {target.display_name}。")
        return True

    def _live_relocation_candidate_ids(self, source_item_id: str) -> list[str]:
        """Return target candidates for Live Photo MOV relocation.

        Keep the candidate pool in the same recycle-bin context as the source
        view. This avoids physically moving a MOV between a normal-view item and
        a trash-view item by accident, while still allowing both normal and trash
        views to use the feature independently.
        """
        in_trash = self.is_trash_view()
        ids: list[str] = []
        for item in self.all_items:
            if item.item_id == source_item_id:
                continue
            if item.item_id not in self.item_map:
                continue
            if (item.item_id in self.trash_ids) != in_trash:
                continue
            try:
                if not item.representative_image.exists():
                    continue
            except Exception:
                continue
            ids.append(item.item_id)
        return ids

    def relocate_live_mov_for_item(self, source_item_id: str):
        """Move/swap only the right-clicked Live Photo MOV to another photo."""
        source = self.item_map.get(source_item_id)
        if source is None:
            return False
        if not source.is_live:
            QMessageBox.information(self, "提示", "非实况照片没有可重定位的 MOV。")
            return False
        source_mov = find_live_video_file(source)
        if source_mov is None or not source_mov.exists():
            QMessageBox.warning(self, "无法重定位", "当前实况照片找不到可用的 MOV 文件，可能已在程序外被移动或删除。")
            return False
        candidate_ids = self._live_relocation_candidate_ids(source_item_id)
        if not candidate_ids:
            QMessageBox.information(self, "提示", "当前视图中没有可作为目标的其他照片。")
            return False
        dialog = LiveRelocationTargetDialog(self, source_item_id, candidate_ids, self)
        if dialog.exec() != QDialog.Accepted:
            return False
        target_id = dialog.selected_item_id
        target = self.item_map.get(target_id)
        if target is None or target.item_id == source.item_id:
            return False
        target_mov = find_live_video_file(target) if target.is_live else None
        if target.is_live and (target_mov is None or not target_mov.exists()):
            QMessageBox.warning(self, "无法交换", "目标实况照片找不到可用的 MOV 文件，可能已在程序外被移动或删除。")
            return False

        if target.is_live:
            action_desc = "交换双方 MOV"
            extra = f"目标也是实况照片，将交换：\n{source_mov}\n↔\n{target_mov}"
        else:
            action_desc = "转移 MOV"
            extra = f"目标是普通照片，将把 MOV 转移并改名为目标照片同名 MOV：\n{source_mov}"
        reply = QMessageBox.question(
            self, "确认实况重定位",
            f"源照片：\n{source.display_name}\n\n目标照片：\n{target.display_name}\n\n操作：{action_desc}\n{extra}\n\n"
            "照片本体文件名不会改变，只会移动/改名 MOV 文件。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return False
        try:
            self._perform_live_mov_relocation(source, target)
        except Exception as e:
            QMessageBox.critical(self, "实况重定位失败", str(e))
            return False
        self.status_label.setText(f"实况重定位完成：{source.display_name} → {target.display_name}。")
        return True

    def _expected_mov_path_for_image(self, image_path: Path, preferred_suffix: str = ".MOV") -> Path:
        suffix = preferred_suffix or ".MOV"
        if not suffix.startswith("."):
            suffix = "." + suffix
        return image_path.with_name(image_path.stem + suffix)

    def _path_conflicts_with_existing_file(self, path: Path, allowed: set[Path]) -> bool:
        try:
            resolved = path.resolve()
            allowed_resolved = {p.resolve() for p in allowed}
        except Exception:
            resolved = path.absolute()
            allowed_resolved = {p.absolute() for p in allowed}
        return path.exists() and resolved not in allowed_resolved

    def _unique_temp_mov_path(self, base_dir: Path) -> Path:
        for i in range(1000):
            candidate = base_dir / f".photo_mover_live_relocating_{int(time.time() * 1000)}_{i}.tmp"
            if not candidate.exists():
                return candidate
        raise RuntimeError("无法创建临时 MOV 交换文件名。")

    def _remove_cache_for_item_key(self, stable_key: str):
        if not stable_key:
            return
        try:
            cache = self.ensure_item_info_cache_loaded()
            cache.pop(stable_key, None)
            self._item_info_cache_dirty = True
        except Exception:
            pass
        try:
            thumb_cache_path_for_key(stable_key).unlink(missing_ok=True)
            for cached in (app_state_dir() / THUMB_CACHE_DIR_NAME).glob(f"{stable_key}_*.png"):
                cached.unlink(missing_ok=True)
        except Exception:
            pass

    def _refresh_item_after_live_relocation(self, item: PhotoItemData, was_trashed: bool, old_key: str):
        item.files = [Path(f) for f in item.files]
        item.is_live = any(f.suffix.lower() in VIDEO_EXTENSIONS for f in item.files)
        item.size_bytes = group_size_bytes(item.files)
        ext_set = sorted({f.suffix.upper().lstrip(".") for f in item.files})
        if item.is_live:
            item.item_type = f"LIVE 实况照片 ({' + '.join(ext_set)})"
        else:
            item.item_type = f"普通照片 ({item.representative_image.suffix.upper().lstrip('.')})"
        old_stable_key = old_key or item.stable_key
        assign_stable_identity(item)
        self._remove_cache_for_item_key(old_stable_key)
        self._remove_cache_for_item_key(item.stable_key)
        self.icon_cache.pop(item.item_id, None)
        self.pending_thumb_data.pop(item.item_id, None)
        self.thumb_requested.discard(item.item_id)
        self.hover_thumb_requested.discard(item.item_id)
        self.live_frame_cache.pop(item.item_id, None)
        self.live_frame_requested.discard(item.item_id)
        self.live_frame_failed.discard(item.item_id)
        if was_trashed:
            self.ensure_trash_records_loaded()
            if old_stable_key:
                self.trash_records.pop(old_stable_key, None)
            if item.stable_key:
                self.trash_records[item.stable_key] = {
                    "paths": item_paths_for_state(item.files),
                    "display_name": item.display_name,
                    "trashed_at": time.time(),
                }
                self._append_trash_journal("live_relocate", item)
            self.trash_ids.add(item.item_id)
            self._persistent_state_dirty = True

    def _notify_items_updated_after_live_relocation(self, item_ids: list[str]):
        rows = [self.visible_row_by_id.get(iid) for iid in item_ids]
        rows = [r for r in rows if r is not None]
        self.grid_model.notify_rows(rows, [Qt.DecorationRole, Qt.ToolTipRole, IS_LIVE_ROLE, NEEDS_BINDING_ROLE, THUMB_READY_ROLE])
        self.table_model.notify_rows(rows, [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, IS_LIVE_ROLE, NEEDS_BINDING_ROLE, THUMB_READY_ROLE])
        for iid in item_ids:
            if iid in self.item_map:
                self.request_thumbnail(self.generation, self.item_map[iid])
        for dlg in self._iter_detail_windows():
            try:
                if hasattr(dlg, "on_owner_items_updated"):
                    dlg.on_owner_items_updated(set(item_ids))
            except Exception:
                pass
        if item_ids:
            self.schedule_auto_classification()

    def _perform_live_mov_relocation(self, source: PhotoItemData, target: PhotoItemData):
        source_mov = find_live_video_file(source)
        if source_mov is None or not source_mov.exists():
            raise RuntimeError("源实况照片的 MOV 不存在。")
        target_mov = find_live_video_file(target) if target.is_live else None
        if target.is_live and (target_mov is None or not target_mov.exists()):
            raise RuntimeError("目标实况照片的 MOV 不存在。")
        source_old_key = source.stable_key
        target_old_key = target.stable_key
        source_was_trashed = source.item_id in self.trash_ids
        target_was_trashed = target.item_id in self.trash_ids

        source_target_mov = self._expected_mov_path_for_image(target.representative_image, source_mov.suffix)
        if not target.is_live:
            if self._path_conflicts_with_existing_file(source_target_mov, {source_mov}):
                raise RuntimeError(f"目标照片同名 MOV 已存在，不能覆盖：\n{source_target_mov}")
            original_source_files = list(source.files)
            original_target_files = list(target.files)
            moved = False
            try:
                source_target_mov.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_mov), str(source_target_mov))
                moved = True
                source.files = [f for f in source.files if normalize_item_path(f) != normalize_item_path(source_mov)]
                target.files = list(target.files) + [source_target_mov]
                self._refresh_item_after_live_relocation(source, source_was_trashed, source_old_key)
                self._refresh_item_after_live_relocation(target, target_was_trashed, target_old_key)
            except Exception:
                source.files = original_source_files
                target.files = original_target_files
                if moved and source_target_mov.exists() and not source_mov.exists():
                    try:
                        shutil.move(str(source_target_mov), str(source_mov))
                    except Exception:
                        pass
                raise
        else:
            assert target_mov is not None
            target_to_source_mov = self._expected_mov_path_for_image(source.representative_image, target_mov.suffix)
            if self._path_conflicts_with_existing_file(source_target_mov, {source_mov, target_mov}):
                raise RuntimeError(f"目标照片同名 MOV 已存在，不能覆盖：\n{source_target_mov}")
            if self._path_conflicts_with_existing_file(target_to_source_mov, {source_mov, target_mov}):
                raise RuntimeError(f"源照片同名 MOV 已存在，不能覆盖：\n{target_to_source_mov}")
            temp_mov = self._unique_temp_mov_path(source_mov.parent)
            original_source_files = list(source.files)
            original_target_files = list(target.files)
            stage = 0
            try:
                shutil.move(str(source_mov), str(temp_mov)); stage = 1
                shutil.move(str(target_mov), str(target_to_source_mov)); stage = 2
                shutil.move(str(temp_mov), str(source_target_mov)); stage = 3
                source.files = [target_to_source_mov if normalize_item_path(f) == normalize_item_path(source_mov) else f for f in source.files]
                target.files = [source_target_mov if normalize_item_path(f) == normalize_item_path(target_mov) else f for f in target.files]
                self._refresh_item_after_live_relocation(source, source_was_trashed, source_old_key)
                self._refresh_item_after_live_relocation(target, target_was_trashed, target_old_key)
            except Exception:
                source.files = original_source_files
                target.files = original_target_files
                try:
                    if stage >= 3:
                        if source_target_mov.exists() and not source_mov.exists():
                            shutil.move(str(source_target_mov), str(source_mov))
                    elif stage >= 1:
                        if temp_mov.exists() and not source_mov.exists():
                            shutil.move(str(temp_mov), str(source_mov))
                    if stage >= 2 and target_to_source_mov.exists() and not target_mov.exists():
                        shutil.move(str(target_to_source_mov), str(target_mov))
                except Exception:
                    pass
                raise
        self.update_total_stats_cache()
        self.refresh_models_preserve_selection()
        self.request_persistent_state_save(item_info=True, trash=True, delay_ms=100)
        self._notify_items_updated_after_live_relocation([source.item_id, target.item_id])

    def _selected_visible_items_in_display_order(self) -> list[PhotoItemData]:
        ids = [iid for iid in self.visible_ids if iid in self.selected_ids and iid in self.item_map]
        return [self.item_map[iid] for iid in ids]

    def _suffix_for_ios_export(self, src: Path) -> str:
        suf = src.suffix or ""
        if suf.lower() in IMAGE_EXTENSIONS or suf.lower() in VIDEO_EXTENSIONS:
            return suf.upper()
        return suf

    def build_reorder_export_plan(self, items: list[PhotoItemData], target_dir: Path) -> tuple[list[dict], list[str]]:
        plan: list[dict] = []
        problems: list[str] = []
        used_targets: set[str] = set()
        seq = max(1, min(9999, int(self.settings_service.get("export.dcf_start", 1) or 1)))
        for item in items:
            if item_is_mov_only(item):
                problems.append(f"{item.display_name}: 单独 MOV 不能作为照片项目重排导出，请先绑定或恢复到对应照片。")
                continue
            image_files = image_files_for_item(item)
            if not image_files:
                problems.append(f"{item.display_name}: 没有可导出的照片文件。")
                continue
            folder_no = 100 + ((seq - 1) // 9999)
            if folder_no > 999:
                problems.append("选区数量过大，已超过 DCF/DCIM 目录编号 100APPLE-999APPLE 的安全范围。")
                break
            base = ios_img_basename(seq)
            rel_dir = ios_dcf_relative_dir(seq)
            pairs = []
            for src in item.files:
                if not src.exists():
                    problems.append(f"{item.display_name}: 源文件不存在：{src}")
                    continue
                dst = target_dir / rel_dir / f"{base}{self._suffix_for_ios_export(src)}"
                norm_dst = normalize_item_path(dst)
                if norm_dst in used_targets:
                    problems.append(f"{item.display_name}: 输出文件名冲突：{dst.name}")
                    continue
                used_targets.add(norm_dst)
                pairs.append({"src": str(src), "dst": str(dst)})
            if pairs:
                plan.append({
                    "item_id": item.item_id,
                    "display_name": item.display_name,
                    "new_base": base,
                    "source_folder": str(item.source_folder),
                    "file_pairs": pairs,
                })
                seq += 1
        for entry in plan:
            for pair in entry.get("file_pairs", []):
                dst = Path(pair.get("dst") or "")
                if dst.exists():
                    problems.append(f"目标文件已存在，为避免覆盖，已阻止导出：{dst}")
        return plan, problems

    def reorder_export_selected_items(self):
        items = self._selected_visible_items_in_display_order()
        if not items:
            QMessageBox.information(self, "提示", "请先选择要重排导出的照片。")
            return
        target_folder = QFileDialog.getExistingDirectory(
            self,
            "请选择批量重排导出根文件夹（将自动生成 DCIM/100APPLE 等子目录）",
            str(self.settings_service.get("export.default_directory", "") or ""),
        )
        if not target_folder:
            return
        target_dir = Path(target_folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        plan, problems = self.build_reorder_export_plan(items, target_dir)
        if problems:
            msg = "重排导出计划存在以下问题：\n\n" + "\n".join(problems[:14])
            if len(problems) > 14:
                msg += "\n……"
            if not plan:
                QMessageBox.warning(self, "无法重排导出", msg)
                return
            reply = QMessageBox.question(
                self,
                "部分项目无法导出",
                msg + "\n\n是否继续导出其余可用项目？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        dialog = ReorderExportPreviewDialog(plan, target_dir, self)
        if dialog.exec() != QDialog.Accepted:
            return
        plan = list(dialog.plan)
        if not plan:
            QMessageBox.warning(self, "无法重排导出", "当前预览序列为空，无法导出。")
            return
        total_files = sum(len(entry.get("file_pairs", [])) for entry in plan)
        reply = QMessageBox.question(
            self,
            "确认开始复制",
            (
                f"将复制导出 {len(plan)} 项、{total_files} 个文件。\n\n"
                f"目标根文件夹：\n{target_dir}\n\n"
                "程序会在其中生成 DCIM/100APPLE、101APPLE 等子目录，避免 IMG_9999 后覆盖。\n\n"
                "原文件不会移动或改名。是否开始？"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self._file_activity_busy = True
            self._show_activity_strip()
            self.file_op_label.setText("文件操作：正在批量重排导出……")
            self.file_op_progress.setRange(0, max(1, len(plan)))
            self.file_op_progress.setValue(0)
            self.file_op_executor.submit(_copy_reordered_export_worker, plan, self.signals, {"target_dir": str(target_dir), "show_message": True})
        except Exception as e:
            self._file_activity_busy = False
            self._hide_activity_strip_if_idle()
            QMessageBox.critical(self, "重排导出失败", str(e))

    def export_selected_items(self):
        selected_ids = self._selected_visible_item_ids()
        if not selected_ids:
            QMessageBox.information(self, "提示", "请先选择要导出的项目。")
            return
        target_folder = QFileDialog.getExistingDirectory(
            self,
            "请选择导出目标文件夹",
            str(self.settings_service.get("export.default_directory", "") or ""),
        )
        if not target_folder:
            return
        target_dir = Path(target_folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        items = [self.item_map[iid] for iid in selected_ids]
        total_files = sum(len(item.files) for item in items)
        total_size = sum(item.size_bytes for item in items)
        reply = QMessageBox.question(
            self, "确认导出",
            f"本次将复制导出：\n\n{len(items)} 项\n{total_files} 个文件\n容量：{format_bytes(total_size)}\n\n目标文件夹：\n{target_dir}\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        exported_items = 0
        exported_files = 0
        failed = []
        for item in items:
            try:
                target_paths = make_unique_target_paths(target_dir, item.files)
                for src, dst in zip(item.files, target_paths):
                    if not src.exists():
                        raise FileNotFoundError(f"源文件不存在：{src}")
                    shutil.copy2(str(src), str(dst))
                    exported_files += 1
                exported_items += 1
            except Exception as e:
                failed.append(f"{item.display_name}: {e}")
        msg = f"导出完成。\n\n成功导出：{exported_items} 项\n成功复制文件：{exported_files} 个\n容量：{format_bytes(total_size)}"
        if failed:
            msg += f"\n\n失败：{len(failed)} 项\n\n" + "\n".join(failed[:10])
            if len(failed) > 10:
                msg += "\n……"
        QMessageBox.information(self, "完成", msg)

    def show_item_metadata(self, item_id: str):
        item = self.item_map.get(item_id)
        if item is None:
            return
        plain_text = metadata_text_for_item(item)
        html_text = metadata_html_for_item(item)

        dialog = QDialog(self)
        dialog.setWindowTitle(f"元数据信息 - {item.display_name}")
        dialog.resize(900, 760)
        dialog.setStyleSheet(
            "QDialog { background: #F5F5F7; }"
            "QPushButton { padding: 7px 14px; border-radius: 8px; background: #FFFFFF; border: 1px solid #D0D5DD; }"
            "QPushButton:hover { background: #F2F4F7; }"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html_text)
        browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; }"
        )
        prepare_scroll_area(browser)
        layout.addWidget(browser, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = QPushButton("复制全部信息")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(plain_text))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)
        dialog.exec()

    # ---------- move ----------

    def move_selected(self):
        selected_ids = self._selected_visible_item_ids()
        if not selected_ids:
            QMessageBox.information(self, "提示", "请先选择要移动的项目。")
            return
        target_folder = QFileDialog.getExistingDirectory(self, "请选择本次移动的目标文件夹")
        if not target_folder:
            return
        target_dir = Path(target_folder)
        if self.source_dir:
            try:
                if target_dir.resolve() == self.source_dir.resolve():
                    QMessageBox.critical(self, "错误", "目标文件夹不能与源文件夹相同。")
                    return
            except Exception:
                pass
        items = [self.item_map[iid] for iid in selected_ids]
        total_files = sum(len(item.files) for item in items)
        total_size = sum(item.size_bytes for item in items)
        reply = QMessageBox.question(
            self, "确认移动",
            f"本次将移动：\n\n{len(items)} 项\n{total_files} 个文件\n容量：{format_bytes(total_size)}\n\n目标文件夹：\n{target_dir}\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        moved_items = 0
        moved_files = 0
        moved_size = 0
        failed = []
        for item in items:
            try:
                target_paths = make_unique_target_paths(target_dir, item.files)
                for src, dst in zip(item.files, target_paths):
                    if not src.exists():
                        raise FileNotFoundError(f"源文件不存在：{src}")
                    shutil.move(str(src), str(dst))
                    moved_files += 1
                moved_size += item.size_bytes
                moved_items += 1
                self.remove_item_after_move(item.item_id)
            except Exception as e:
                failed.append(f"{item.display_name}: {e}")
        msg = f"移动完成。\n\n成功移动：{moved_items} 项\n成功移动文件：{moved_files} 个\n释放/转移容量：{format_bytes(moved_size)}"
        if failed:
            msg += f"\n\n失败：{len(failed)} 项\n\n" + "\n".join(failed[:10])
            if len(failed) > 10:
                msg += "\n……"
        QMessageBox.information(self, "完成", msg)
        self.update_total_stats_cache()
        self.refresh_models_preserve_selection()

    def remove_items_after_move(self, item_ids: set[str] | list[str], trash_op: str = "move"):
        ids = {str(iid) for iid in item_ids if iid}
        if not ids:
            return
        trash_changed = False
        cache_changed = False
        try:
            self.ensure_trash_records_loaded()
        except Exception:
            pass
        try:
            cache = self.ensure_item_info_cache_loaded()
        except Exception:
            cache = None
        for item_id in list(ids):
            item = self.item_map.get(item_id)
            if item is not None:
                if item.stable_key and item.stable_key in self.trash_records:
                    try:
                        self._append_trash_journal(trash_op, item)
                    except Exception:
                        pass
                    self.trash_records.pop(item.stable_key, None)
                    trash_changed = True
                if item.stable_key:
                    try:
                        if cache is not None:
                            cache.pop(item.stable_key, None)
                            cache_changed = True
                    except Exception:
                        pass
                    try:
                        thumb_cache_path_for_key(item.stable_key).unlink(missing_ok=True)
                        for cached in (app_state_dir() / THUMB_CACHE_DIR_NAME).glob(f"{item.stable_key}_*.png"):
                            cached.unlink(missing_ok=True)
                    except Exception:
                        pass
            self.item_map.pop(item_id, None)
            self.icon_cache.pop(item_id, None)
            self.pending_thumb_data.pop(item_id, None)
            self.thumb_requested.discard(item_id)
            self.selected_ids.discard(item_id)
            self.trash_ids.discard(item_id)
            self.deleting_to_deleted_ids.discard(item_id)
        self.all_items = [item for item in self.all_items if item.item_id not in ids]
        if trash_changed:
            self.save_trash_records()
        if cache_changed:
            self.save_item_info_cache()
        self._notify_detail_windows_items_removed(ids)
        self.schedule_auto_classification()

    def remove_item_after_move(self, item_id: str, trash_op: str = "move"):
        self.remove_items_after_move({item_id}, trash_op=trash_op)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, '_live_resizing', False):
            _update_resize_freeze_overlay(self)
            maybe_update_live_resize_window_mask(self, 10, interval_ms=0)
            return
        _update_resize_freeze_overlay(self)
        apply_opaque_rounded_window_mask(self, 10)
        try:
            self.update_table_column_layout()
        except Exception:
            pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(APP_BG))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            sync_frameless_shell_state(
                self, self._window_outer_layout, self.window_shell, self._window_shadow,
                self._window_normal_margin, normal_radius=10
            )
            apply_opaque_rounded_window_mask(self, 10)

    # ---------- frameless resize ----------

    def nativeEvent(self, eventType, message):
        """Native Windows edge/corner resize for the frameless window.

        Use Qt global cursor coordinates converted through mapFromGlobal(), not
        raw lParam arithmetic.  This avoids high-DPI physical/logical coordinate
        mismatches that make the right/bottom resize point appear inside the UI.
        The hit band is anchored to the visible L2 shell edge and extends outward
        through the transparent shadow margin, but only a few pixels inward.
        """
        nccalc_result = handle_frameless_nccalcsize(eventType, message)
        if nccalc_result is not None:
            return nccalc_result
        try:
            etype = eventType.decode() if isinstance(eventType, (bytes, bytearray)) else str(eventType)
        except Exception:
            etype = str(eventType)
        if "windows" in etype and os.name == "nt" and not self.isMaximized():
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                WM_ENTERSIZEMOVE = 0x0231
                WM_SIZING = 0x0214
                WM_EXITSIZEMOVE = 0x0232
                if msg.message == WM_ENTERSIZEMOVE:
                    begin_window_live_resize(self, getattr(self, '_active_resize_edge', ''))
                elif msg.message == WM_SIZING:
                    begin_window_live_resize(self, _resize_edge_name_from_wmsz(int(msg.wParam)))
                elif msg.message == WM_EXITSIZEMOVE:
                    finish_window_live_resize(self, 28)
                # Make taskbar-click minimize reliable on the frameless window.
                # Restored WS_MINIMIZEBOX normally handles this, but accepting
                # SC_MINIMIZE here protects against Qt/Win10 style quirks.
                if msg.message == 0x0112 and (int(msg.wParam) & 0xFFF0) == 0xF020:
                    self.showMinimized()
                    return True, 0
                # Do not change layouts, masks or overlays during live resize.
                # Native Windows resizing must be the single source of geometry.
                WM_NCHITTEST = 0x0084
                if msg.message == WM_NCHITTEST:
                    shell = getattr(self, "window_shell", None)
                    if shell is not None and shell.isVisible():
                        shell_rect = shell.geometry()
                    else:
                        shell_rect = self.rect()
                    pos = self.mapFromGlobal(QCursor.pos())
                    x, y = int(pos.x()), int(pos.y())
                    outer = self.rect()
                    # Hit testing is anchored to the *visible shell edge*.
                    # The previous wide band extended across the full transparent
                    # shadow margin, which made the right/bottom resize point feel
                    # displaced.  Keep a small symmetric band around the visible
                    # edge only.
                    band_in = 3
                    band_out = 7
                    left = (shell_rect.left() - band_out) <= x <= (shell_rect.left() + band_in)
                    right = (shell_rect.right() - band_in) <= x <= (shell_rect.right() + band_out)
                    top = (shell_rect.top() - band_out) <= y <= (shell_rect.top() + band_in)
                    bottom = (shell_rect.bottom() - band_in) <= y <= (shell_rect.bottom() + band_out)
                    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
                    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
                    if top and left:
                        self._active_resize_edge = 'top-left'
                        return True, HTTOPLEFT
                    if top and right:
                        self._active_resize_edge = 'top-right'
                        return True, HTTOPRIGHT
                    if bottom and left:
                        self._active_resize_edge = 'bottom-left'
                        return True, HTBOTTOMLEFT
                    if bottom and right:
                        self._active_resize_edge = 'bottom-right'
                        return True, HTBOTTOMRIGHT
                    if left:
                        self._active_resize_edge = 'left'
                        return True, HTLEFT
                    if right:
                        self._active_resize_edge = 'right'
                        return True, HTRIGHT
                    if top:
                        self._active_resize_edge = 'top'
                        return True, HTTOP
                    if bottom:
                        self._active_resize_edge = 'bottom'
                        return True, HTBOTTOM
            except Exception:
                pass
        return super().nativeEvent(eventType, message)


    def _object_belongs_to_this_window(self, obj) -> bool:
        try:
            if obj is self:
                return True
            if isinstance(obj, QWidget):
                return obj.window() is self
        except Exception:
            pass
        return False

    def _handle_manual_resize_event(self, obj, event) -> bool:
        # A fallback only: no hidden child handles.  It listens globally but acts
        # exclusively on this window and only within a tiny band around the
        # visible L2 shell border.  This fixes cases where WM_NCHITTEST is not
        # emitted for translucent frameless windows.
        try:
            if not self._object_belongs_to_this_window(obj):
                return False
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and not self.isMaximized():
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = event.globalPos()
                edge = frameless_edge_at_global(self, getattr(self, "window_shell", None), gp)
                if edge:
                    self._manual_resizing = True
                    self._manual_resize_edge = edge
                    self._manual_resize_start_global = gp
                    self._manual_resize_start_geom = self.geometry()
                    mark_frameless_resize_activity(self, delay_ms=260)
                    try:
                        self.grabMouse()
                    except Exception:
                        pass
                    event.accept()
                    return True
            if et == QEvent.MouseMove and self._manual_resizing and self._manual_resize_start_geom is not None:
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = event.globalPos()
                dx = gp.x() - self._manual_resize_start_global.x()
                dy = gp.y() - self._manual_resize_start_global.y()
                mark_frameless_resize_activity(self, delay_ms=260)
                apply_manual_frameless_resize(self, str(self._manual_resize_edge), self._manual_resize_start_geom, dx, dy)
                event.accept()
                return True
            if et == QEvent.MouseButtonRelease and self._manual_resizing:
                self._manual_resizing = False
                self._manual_resize_edge = None
                self._manual_resize_start_geom = None
                mark_frameless_resize_activity(self, delay_ms=60)
                try:
                    self.releaseMouse()
                except Exception:
                    pass
                event.accept()
                return True
        except Exception:
            return False
        return False

    def eventFilter(self, obj, event):
        # Do not run manual resize fallback. Native WM_NCHITTEST is the single
        # source of resizing, preventing native/manual geometry races during drag.
        return super().eventFilter(obj, event)


    # ---------- close ----------

    def _stop_owned_timers_for_shutdown(self):
        for timer_name in (
            "refresh_timer", "thumb_flush_timer", "live_preview_timer", "_state_save_timer",
            "classification_refresh_timer",
            "classification_poll_timer",
            "_system_theme_timer",
            "_live_resize_settle_timer", "_resize_freeze_fade_timer",
            "_live_resize_fallback_timer", "_rounded_mask_timer",
        ):
            try:
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        for view_name in ("grid", "table"):
            try:
                view = getattr(self, view_name, None)
                if view is None:
                    continue
                for timer_name in (
                    "_single_click_timer", "_tile_anim_timer", "_check_anim_timer",
                    "_full_tooltip_timer", "_hover_emit_timer", "_tooltip_defer_timer",
                    "_tooltip_gap_hide_timer",
                ):
                    timer = getattr(view, timer_name, None)
                    if timer is not None:
                        timer.stop()
            except Exception:
                pass

    def _close_auxiliary_windows_for_shutdown(self):
        try:
            for dlg in list(getattr(self, "detail_windows", set())):
                try:
                    dlg.close()
                    dlg.deleteLater()
                except Exception:
                    pass
            self.detail_windows.clear()
        except Exception:
            pass
        try:
            for w in list(QApplication.topLevelWidgets()):
                if w is self:
                    continue
                try:
                    w.hide()
                    w.close()
                    w.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass

    def _shutdown_background_jobs(self):
        try:
            self.stop_event.set()
        except Exception:
            pass
        self.generation += 1
        self.thumb_requested.clear()
        self.hover_thumb_requested.clear()
        self.live_frame_requested.clear()
        self.pending_thumb_data.clear()
        for executor_name in ("thumb_executor", "hover_thumb_executor", "meta_executor", "live_executor", "detail_executor", "detail_live_executor", "file_op_executor", "classification_executor"):
            try:
                executor = getattr(self, executor_name, None)
                if executor is not None:
                    executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        try:
            if self.scan_thread is not None and self.scan_thread.is_alive():
                self.scan_thread.join(timeout=0.12)
        except Exception:
            pass

    def closeEvent(self, event):
        # Closing must be immediate.  Background thumbnail / EXIF / LIVE workers
        # are cancelled cooperatively and are backed by daemon executors, so the
        # IDE process is not kept alive waiting for a slow image/video decoder.
        if getattr(self, "_closing", False):
            event.accept()
            try:
                app = QApplication.instance()
                if app is not None:
                    QTimer.singleShot(0, app.quit)
            except Exception:
                pass
            return

        self._closing = True
        event.accept()
        try:
            self.hide()
        except Exception:
            pass
        self._stop_owned_timers_for_shutdown()
        self._close_auxiliary_windows_for_shutdown()
        try:
            self.flush_persistent_state()
        except Exception:
            pass
        try:
            self._cleanup_session_history_file()
        except Exception:
            pass
        self._shutdown_background_jobs()
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
                QTimer.singleShot(0, app.quit)
        except Exception:
            pass
        super().closeEvent(event)


def install_global_style(app: QApplication):
    profile = RUNTIME_THEME_PROFILE
    base_size = theme_display_point_size(
        RUNTIME_THEME_STYLE, RUNTIME_THEME_LOCALE, 9 if profile.is_flavor else 10
    )
    ui_font = make_theme_font(RUNTIME_THEME_STYLE, RUNTIME_THEME_LOCALE, base_size)
    app.setFont(ui_font)
    app.setStyleSheet(
        f"QWidget {{ font-size: {base_size}pt; color: {APP_TEXT}; }}"
        f"#RootTransparent {{ background: {APP_BG}; }}"
        "#WindowShell, #DetailShell { background: transparent; border: none; }"
        "#MainBody { background: transparent; }"
        f"#MainTitleBar {{ background: {APP_BG}; border-bottom: 1px solid rgba(0,0,0,0.09); }}"
        f"#LibraryNavigation {{ background: {APP_BG}; border-bottom: 1px solid rgba(0,0,0,0.08); }}"
        f"#LibraryTitle {{ color: {APP_TEXT}; font-size: 13px; font-weight: 650; }}"
        f"#LibraryCount {{ color: {SYSTEM_GRAY_6}; min-width: 54px; font-size: 13px; }}"
        f"#LibraryWorkspace, #LibraryContent {{ background: {CONTENT_BG}; }}"
        f"#LibrarySidebar {{ background: {SIDEBAR_BG}; border-right: 1px solid rgba(0,0,0,0.08); }}"
        f"#SidebarHeading {{ color: {APP_MUTED}; font-size: 11px; font-weight: 650; padding: 8px 8px 4px 8px; letter-spacing: 0.2px; }}"
        f"#SidebarHint {{ color: {SYSTEM_GRAY_6}; font-size: 11px; padding: 4px 10px; }}"
        "QPushButton#SidebarItem {"
        f"  min-height: 30px; padding: 0px; text-align: left; color: {APP_TEXT};"
        "  background: transparent; border: none; font-weight: 500;"
        "}"
        "QPushButton#SidebarItem:hover { background: transparent; }"
        "QPushButton#SidebarItem:checked { background: transparent; color: white; font-weight: 650; }"
        "QTreeWidget#SmartCategoryTree {"
        f"  background: transparent; border: none; outline: 0; color: {APP_TEXT};"
        "  padding: 0px; font-weight: 500;"
        "}"
        "QTreeWidget#SmartCategoryTree::item { min-height: 28px; padding: 1px 6px; background: transparent; border: none; }"
        "QTreeWidget#SmartCategoryTree::item:hover { background: transparent; }"
        "QTreeWidget#SmartCategoryTree::item:selected { background: transparent; }"
        "QTreeWidget#SmartCategoryTree::branch { background: transparent; width: 16px; }"
        "#ToolbarCard, #ProgressCard, #MainSearchCard { background: transparent; border: none; }"
        "#SourcePickerButton { background: transparent; border: none; }"
        f"#TinyToolbarLabel {{ color: {APP_MUTED}; background: transparent; font-weight: 600; padding-left: 2px; }}"
        f"#ProgressLabel {{ color: {APP_MUTED}; background: transparent; font-size: 11px; font-weight: 600; }}"
        f"QLineEdit#MainSearchEdit {{ background: transparent; border: none; padding: 0px; color: {APP_TEXT}; selection-background-color: {ACCENT_BLUE}; }}"
        "QLineEdit#MainSearchEdit:focus { border: none; }"
        f"QLineEdit#MainSearchEdit::placeholder {{ color: {SYSTEM_GRAY_6}; }}"
        f"#SearchStatus {{ color: {SYSTEM_GRAY_6}; font-size: 11px; background: transparent; }}"
        "#StatsLabel {"
        f"  color: {APP_MUTED}; background: transparent; border: none;"
        "  padding: 0px 2px; font-size: 11px; font-weight: 500;"
        "}"
        f"#StatusLabel {{ color: {SYSTEM_GRAY_6}; background: transparent; padding: 0px 2px; font-size: 11px; }}"
        f"#FooterBar {{ background: {APP_BG}; border-top: 1px solid rgba(0,0,0,0.07); }}"
        "#ContentStack { background: transparent; border: none; }"
        f"QTableView, QListView {{ font-size: 13px; background: {CONTENT_BG}; border: none; outline: 0; }}"
        "QHeaderView::section {"
        f"  font-weight: 600; padding: 7px 8px; background: {SYSTEM_GRAY_1}; color: {APP_TEXT};"
        "  border: none; border-right: 1px solid rgba(0,0,0,0.08); border-bottom: 1px solid rgba(0,0,0,0.08);"
        "}"
        "QPushButton {"
        f"  min-height: 30px; padding: 5px 12px; border: 1px solid {SYSTEM_GRAY_3}; border-radius: 14px;"
        f"  background: {CONTENT_BG}; color: {APP_TEXT}; font-weight: 600;"
        "}"
        f"QPushButton:hover {{ background: {SYSTEM_GRAY_1}; border-color: {SYSTEM_GRAY_4 if 'SYSTEM_GRAY_4' in globals() else '#C7C7CC'}; }}"
        f"QPushButton:pressed {{ background: {SYSTEM_GRAY_2}; }}"
        f"QPushButton#PrimaryToolButton {{ background: {CONTENT_BG}; border-color: {SYSTEM_GRAY_3}; color: {ACCENT_BLUE}; }}"
        f"QPushButton#PrimaryToolButton:hover {{ background: {SYSTEM_GRAY_1}; }}"
        f"QPushButton#AccentToolButton {{ background: {ACCENT_BLUE}; border-color: {ACCENT_BLUE}; color: white; }}"
        f"QPushButton#AccentToolButton:hover {{ background: {ACCENT_BLUE_DARK}; border-color: {ACCENT_BLUE_DARK}; }}"
        "QPushButton#AccentToolButton:pressed { background: #0062CC; }"
        f"QComboBox {{ background: transparent; border: none; padding: 0px; color: {APP_TEXT}; font-weight: 600; }}"
        "QComboBox#SegmentedViewControl { background: transparent; border: none; padding: 0px; }"
        "QComboBox#SegmentedViewControl:hover { background: transparent; }"
        "QComboBox::drop-down { border: none; width: 0px; }"
        "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
        "QComboBox QAbstractItemView { background: transparent; border: none; padding: 6px; outline: 0; }"
        f"QCheckBox {{ color: {APP_TEXT}; background: transparent; font-weight: 500; spacing: 7px; }}"
        "QCheckBox::indicator { width: 0px; height: 0px; background: transparent; border: none; }"
        "QProgressBar { background: transparent; border: none; }"
        "QProgressBar::chunk { background: transparent; border: none; }"
        "QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }"
        "QScrollBar::handle:vertical { background: rgba(60,60,67,0.28); border-radius: 3px; min-height: 36px; }"
        "QScrollBar::handle:vertical:hover { background: rgba(60,60,67,0.42); width: 10px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        "QScrollBar:horizontal { background: transparent; height: 7px; margin: 2px; }"
        "QScrollBar::handle:horizontal { background: rgba(60,60,67,0.28); border-radius: 3px; min-width: 36px; }"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }"
        f"QToolTip {{ background: {CONTENT_BG}; color: {APP_TEXT}; border: 1px solid {SYSTEM_GRAY_3}; padding: 7px; border-radius: 6px; font-size: 10pt; }}"
        + (
            "QWidget { font-size: 9pt; }"
            "#MainTitleBar, #LibraryNavigation, #FooterBar { border-color: #808080; }"
            "QPushButton { border-radius: 0px; background: #D4D0C8; color: #000000; "
            "border-top: 2px solid #FFFFFF; border-left: 2px solid #FFFFFF; "
            "border-right: 2px solid #404040; border-bottom: 2px solid #404040; }"
            "QPushButton:pressed { background: #C0C0C0; border-top-color: #404040; "
            "border-left-color: #404040; border-right-color: #FFFFFF; border-bottom-color: #FFFFFF; }"
            "QHeaderView::section { background: #D4D0C8; border-radius: 0px; "
            "border-top: 1px solid #FFFFFF; border-left: 1px solid #FFFFFF; "
            "border-right: 1px solid #808080; border-bottom: 1px solid #808080; }"
            "QScrollBar:vertical { background: #D4D0C8; width: 16px; margin: 16px 0; }"
            "QScrollBar::handle:vertical { background: #C0C0C0; border: 1px solid #808080; border-radius: 0px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 16px; background: #D4D0C8; border: 1px solid #808080; }"
            "QScrollBar:horizontal { background: #D4D0C8; height: 16px; margin: 0 16px; }"
            "QScrollBar::handle:horizontal { background: #C0C0C0; border: 1px solid #808080; border-radius: 0px; min-width: 20px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 16px; background: #D4D0C8; border: 1px solid #808080; }"
            "QToolTip { border-radius: 0px; background: #FFFFE1; color: #000000; border: 1px solid #000000; }"
            if profile.control_style == "win2000"
            else (
                "QWidget { font-size: 9pt; }"
                "QPushButton { border-radius: 0px; background: #EEEEEE; color: #000000; "
                "border-top: 2px solid #FFFFFF; border-left: 2px solid #FFFFFF; border-right: 2px solid #333333; border-bottom: 2px solid #333333; }"
                "QHeaderView::section { background: #DDDDDD; border-radius: 0px; border: 1px solid #777777; }"
                "QToolTip { border-radius: 0px; background: #FFFFCC; color: #000000; border: 1px solid #000000; }"
                if profile.control_style == "macos8"
                else (
                    "QPushButton { border-radius: 3px; border-color: #7F9DB9; "
                    "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #FFFFFF,stop:0.5 #F3F8FC,stop:1 #D3E4F1); }"
                    "QPushButton:hover { background: #EAF6FD; border-color: #3C7FB1; }"
                    "QHeaderView::section { background: #E7F0F7; border-right: 1px solid #A7BBC9; border-bottom: 1px solid #A7BBC9; }"
                    "QToolTip { border-radius: 2px; background: #FFFFE1; color: #000000; border: 1px solid #767676; }"
                    if profile.control_style == "win7"
                    else (
                        "QPushButton { border-radius: 4px; }"
                        "QToolTip { border-radius: 4px; }"
                        if profile.control_style == "win11" else ""
                    )
                )
            )
        )
    )
def main():
    high_res_enabled = enable_windows_high_resolution_timers()
    if high_res_enabled:
        atexit.register(disable_windows_high_resolution_timers)

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    app = QApplication([])
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName(PRODUCT_DISPLAY_NAME)
    icon_path = resource_path("assets/photo_manager_icon.ico")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    try:
        app.setStyle(ModernScrollBarStyle(app.style()))
    except Exception:
        pass
    install_global_style(app)
    try:
        app._smooth_wheel_filter = SmoothWheelFilter()
        app.installEventFilter(app._smooth_wheel_filter)
    except Exception:
        pass
    window = PhotoMoverQt()
    try:
        app.aboutToQuit.connect(window.flush_persistent_state)
        app.aboutToQuit.connect(window._cleanup_session_history_file)
        app.aboutToQuit.connect(window._shutdown_background_jobs)
    except Exception:
        pass
    try:
        if high_res_enabled:
            window.status_label.setText("已启用 Windows 1ms 高精度计时器；动画将按当前屏幕刷新率超采样刷新。")
    except Exception:
        pass
    window.show()
    try:
        app.exec()
    finally:
        try:
            window._closing = True
            window._stop_owned_timers_for_shutdown()
            window.flush_persistent_state()
            window._cleanup_session_history_file()
            window._shutdown_background_jobs()
        except Exception:
            pass
        if high_res_enabled:
            disable_windows_high_resolution_timers()


if __name__ == "__main__":
    main()
