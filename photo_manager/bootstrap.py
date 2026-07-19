"""Process-level setup that must run before importing Qt."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_qt_environment() -> None:
    """Configure predictable Qt behavior for Conda and high-DPI Windows."""

    if os.name == "nt":
        # Preserve the app's established, lighter modern-theme rasterization.
        # Legacy private fonts such as PoxiaoPixel are registered from their font
        # files explicitly, so they no longer require changing every theme to
        # the heavier native DirectWrite rendering.
        os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")
        # A root-level Conda qt.conf can otherwise hide pip PySide6's plugins.
        plugins = Path(sys.prefix) / "Lib" / "site-packages" / "PySide6" / "plugins"
        if plugins.is_dir():
            os.environ.setdefault("QT_PLUGIN_PATH", str(plugins))
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")


def resource_path(relative_path: str) -> Path:
    """Resolve a project asset in source and PyInstaller builds."""

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return bundle_root / relative_path
