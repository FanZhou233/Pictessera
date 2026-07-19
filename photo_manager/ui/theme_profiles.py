"""Shared visual profiles for native, Windows, and classic Macintosh themes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtGui import QFont, QFontDatabase


SUPPORTED_THEME_IDS = ("system", "light", "dark", "win11", "win7", "win2000", "macos8")

_REGISTERED_FONT_FILES: set[Path] = set()
_REGISTERED_FONT_FAMILIES: list[str] = []
_FONT_EXTENSIONS = {".tt", ".ttf", ".otf", ".ttc"}
_CLASSIC_FONT_FILENAME_TOKENS = (
    "poxiaopixel", "chicago", "chikare", "charcoal", "geneva", "name fixed",
)


def register_optional_theme_fonts(directories: Iterable[Path | str]) -> tuple[str, ...]:
    """Load user-supplied classic theme fonts into Qt for this process.

    The original Chicago/Charcoal files are not redistributable project assets.
    A user who owns them can place the font files in ``Pictessera_Data/fonts``;
    source builds may also use ``assets/fonts``. Qt reads the family name from
    the file, so compatible licensed recreations such as ChicagoFLF work too.
    """
    sources = [Path(directory) for directory in directories]
    # Qt's FreeType backend keeps the established modern-theme rendering, but
    # it may enumerate a per-user Windows font without opening the file. Add
    # known classic faces explicitly so installed PoxiaoPixel/Chicago fonts remain
    # usable without changing the rasterizer for every other theme.
    if os.name == "nt":
        system_font_roots = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            system_font_roots.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
        for root in system_font_roots:
            if not root.is_dir():
                continue
            try:
                sources.extend(
                    path for path in root.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in _FONT_EXTENSIONS
                    and any(token in path.stem.casefold() for token in _CLASSIC_FONT_FILENAME_TOKENS)
                )
            except OSError:
                pass

    for source in sources:
        if source.is_file() and source.suffix.lower() in _FONT_EXTENSIONS:
            font_files = [source]
        elif source.is_dir():
            try:
                font_files = sorted(
                    path for path in source.iterdir()
                    if path.is_file() and path.suffix.lower() in _FONT_EXTENSIONS
                )
            except OSError:
                continue
        else:
            continue
        for font_path in font_files:
            try:
                resolved = font_path.resolve()
            except OSError:
                resolved = font_path.absolute()
            if resolved in _REGISTERED_FONT_FILES:
                continue
            font_id = QFontDatabase.addApplicationFont(str(resolved))
            if font_id < 0:
                continue
            _REGISTERED_FONT_FILES.add(resolved)
            for family in QFontDatabase.applicationFontFamilies(font_id):
                if family and family not in _REGISTERED_FONT_FAMILIES:
                    _REGISTERED_FONT_FAMILIES.append(family)
    return tuple(_REGISTERED_FONT_FAMILIES)


def registered_theme_font_families() -> tuple[str, ...]:
    """Return application-font families loaded by ``register_optional_theme_fonts``."""
    return tuple(_REGISTERED_FONT_FAMILIES)


@dataclass(frozen=True)
class ThemeProfile:
    theme_id: str
    app_bg: str
    app_bg_2: str
    panel: str
    panel_2: str
    border: str
    text: str
    muted: str
    sidebar: str
    content: str
    gray_1: str
    gray_2: str
    gray_3: str
    gray_4: str
    gray_6: str
    accent: str
    accent_dark: str
    corner_style: str
    card_radius: int
    control_radius: int
    nav_radius: int
    control_style: str
    icon_policy: str
    titlebar_skin: str
    fixed_accent: bool = False

    @property
    def is_flavor(self) -> bool:
        return self.theme_id in {"win11", "win7", "win2000", "macos8"}

    @property
    def uses_modern_icons(self) -> bool:
        return self.icon_policy == "modern"

    @property
    def uses_bevels(self) -> bool:
        return self.control_style in {"win2000", "macos8"}


def normalize_theme_id(theme: str) -> str:
    value = str(theme or "system").lower()
    # Retire the short-lived Aero variant without stranding existing users on
    # an unknown theme.  Its closest maintained successor is Windows 7.
    if value == "win7glass":
        return "win7"
    return value if value in SUPPORTED_THEME_IDS else "system"


def resolve_theme_profile(
    theme: str,
    *,
    system_dark: bool = False,
    accent: str = "#007AFF",
) -> ThemeProfile:
    theme = normalize_theme_id(theme)
    if theme == "system":
        theme = "dark" if system_dark else "light"
        profile_id = "system"
    else:
        profile_id = theme

    if theme == "win11":
        return ThemeProfile(
            profile_id, "#F3F3F3", "#EAEAEA", "#FFFFFF", "#F7F7F7", "#DADADA",
            "#1A1A1A", "#616161", "#F0F0F0", "#FFFFFF", "#F5F5F5", "#EAEAEA",
            "#D6D6D6", "#B8B8B8", "#777777", accent, accent, "rounded", 8, 4, 4,
            "win11", "modern", "win11",
        )
    if theme == "win7":
        return ThemeProfile(
            profile_id, "#EAF2F8", "#D6E5F0", "#F8FBFD", "#EDF4F9", "#8EA9BD",
            "#1E1E1E", "#53606A", "#E7F0F7", "#FFFFFF", "#F1F6FA", "#DCE9F2",
            "#B8CBD9", "#91AABC", "#617584", accent, "#1E5F9B", "rounded", 5, 3, 3,
            "win7", "text", "win7",
        )
    if theme == "win2000":
        return ThemeProfile(
            profile_id, "#D4D0C8", "#C0C0C0", "#D4D0C8", "#C0C0C0", "#808080",
            "#000000", "#404040", "#D4D0C8", "#FFFFFF", "#D4D0C8", "#C0C0C0",
            "#808080", "#808080", "#404040", "#000080", "#000080", "square", 0, 0, 0,
            "win2000", "text", "win2000", True,
        )
    if theme == "macos8":
        return ThemeProfile(
            profile_id, "#DDDDDD", "#C9C9C9", "#EEEEEE", "#DDDDDD", "#777777",
            "#000000", "#444444", "#DDDDDD", "#FFFFFF", "#EEEEEE", "#DDDDDD",
            "#AAAAAA", "#888888", "#555555", "#3366CC", "#244C9A", "square", 0, 0, 0,
            "macos8", "text", "macos8", True,
        )
    if theme == "dark":
        return ThemeProfile(
            profile_id, "#1E1E1E", "#2C2C2E", "#2C2C2E", "#323234", "#48484A",
            "#F5F5F7", "#AEAEB2", "#252527", "#1E1E1E", "#2C2C2E", "#3A3A3C",
            "#48484A", "#636366", "#AEAEB2", accent, accent, "continuous", 14, 9, 10,
            "apple", "modern", "apple",
        )
    return ThemeProfile(
        profile_id, "#F7F7F8", "#EEEEF0", "#FFFFFF", "#F5F5F7", "#D9D9DE",
        "#1D1D1F", "#6E6E73", "#F2F2F4", "#FFFFFF", "#F2F2F7", "#E5E5EA",
        "#D1D1D6", "#C7C7CC", "#8E8E93", accent, accent, "continuous", 14, 9, 10,
        "apple", "modern", "apple",
    )


def theme_font_candidates(theme: str, locale: str) -> tuple[str, ...]:
    """Return a historically appropriate family order without bundling proprietary fonts."""
    theme = normalize_theme_id(theme)
    locale = str(locale or "zh_CN")
    is_tw = locale == "zh_TW"
    is_english = locale == "en"
    if theme == "win11":
        if is_english:
            return ("Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft JhengHei UI")
        return (("Microsoft JhengHei UI", "Microsoft JhengHei") if is_tw else ("Microsoft YaHei UI", "Microsoft YaHei"))
    if theme == "win7":
        if is_english:
            return ("Segoe UI", "Microsoft YaHei", "Microsoft JhengHei")
        return (("Microsoft JhengHei", "Microsoft JhengHei UI") if is_tw else ("Microsoft YaHei", "Microsoft YaHei UI"))
    if theme in {"win2000", "macos8"}:
        # PoxiaoPixel is the single pixel face for every Chinese retro UI.
        # Do not substitute XiaoyaPixel: it is a different low-resolution font.
        if locale in {"zh_CN", "zh_TW"}:
            return (
                "PoxiaoPixel", "Poxiao Pixel",
                *(("PMingLiU", "MingLiU") if is_tw else ("SimSun", "NSimSun")),
            )
        if theme == "win2000":
            # Chicago belongs to classic Macintosh, not Windows 2000.  Use
            # PoxiaoPixel's own Latin glyphs here for a coherent pixel variant.
            return ("PoxiaoPixel", "Poxiao Pixel", "Tahoma", "MS Sans Serif", "SimSun", "PMingLiU")
        # Chicago has no CJK glyphs, but it must still lead the family list in a
        # English UI so Qt can use it for Latin letters, digits and punctuation.
        # Include common licensed recreations by their actual internal names.
        loaded_classic = tuple(
            family for family in _REGISTERED_FONT_FAMILIES
            if any(token in family.casefold() for token in ("chicago", "chikare", "charcoal", "geneva"))
        )
        latin = (
            *loaded_classic,
            "Chicago", "ChicagoFLF", "ChiKareGo2",
            "PoxiaoPixel", "Poxiao Pixel",
            "Charcoal", "Geneva", "Arial",
        )
        if is_english:
            return (*latin, "SimSun", "PMingLiU")
        return (*latin, "SimSun", "NSimSun")
    return (
        "MiSans", "HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei UI",
        "Segoe UI Variable Text", "Segoe UI", "Noto Sans CJK SC",
    )


def theme_display_point_size(theme: str, locale: str, point_size: int) -> int:
    """Compensate pixel fonts whose visual x-height is smaller than Qt's peers."""
    theme = normalize_theme_id(theme)
    locale = str(locale or "zh_CN")
    # PoxiaoPixel has a deliberately small bitmap body at a given point size.
    # Windows 2000 uses it for both scripts; Mac OS 8 uses it only for CJK while
    # its Chicago Latin remains correctly proportioned at the normal size.
    if theme == "win2000" or (theme == "macos8" and locale in {"zh_CN", "zh_TW"}):
        return max(1, int(point_size) + 2)
    return max(1, int(point_size))


def make_theme_font(theme: str, locale: str, point_size: int = 10, *, bold: bool = False) -> QFont:
    candidates = theme_font_candidates(theme, locale)
    font = QFont()
    try:
        font.setFamilies(list(candidates))
    except Exception:
        font.setFamily(candidates[0])
    font.setPointSize(point_size)
    font.setBold(bool(bold))
    try:
        if normalize_theme_id(theme) in {"win2000", "macos8"}:
            font.setHintingPreference(QFont.PreferFullHinting)
            font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        else:
            font.setHintingPreference(QFont.PreferNoHinting)
            font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    except Exception:
        pass
    return font
