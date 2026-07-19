"""Versioned, atomic application settings and desktop integration discovery."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QObject, Signal

from photo_manager.ui.theme_profiles import normalize_theme_id


SETTINGS_VERSION = 1

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": SETTINGS_VERSION,
    "general": {
        "restore_last_folder": True,
        "auto_scan_on_start": False,
        "last_folder": "",
        "default_view": "grid",
        "default_sort": "time_asc",
        "thumbnail_size": "medium",
    },
    "appearance": {
        "theme": "system",
        "titlebar_style": "macos",
        "accent": "blue",
    },
    "language": {
        "locale": "system",
        "hot_reload": True,
    },
    "integration": {
        "lightroom_path": "",
        "photoshop_path": "",
        "default_viewer": True,
    },
    "scan": {
        "recursive": True,
        "workers": 0,
        "thumbnail_cache_mb": 1024,
        "exclude_patterns": [],
    },
    "deletion": {
        "behavior": "app_trash",
        "auto_cleanup_days": 0,
        "confirm_dangerous": True,
    },
    "classification": {
        "rules": {
            "time": True,
            "media": True,
            "device": True,
            "location": True,
            "file": True,
            "plus": True,
        },
        "large_file_mb": 50,
        "ai_enabled": True,
        "content_model_path": "",
        "content_confidence_percent": 35,
        "content_top_k": 8,
    },
    "live_photo": {
        "hover_play": True,
        "hover_delay_ms": 90,
        "play_sound": False,
        "decoder_priority": ["ffmpeg", "opencv", "imageio"],
    },
    "export": {
        "default_directory": "",
        "dcf_start": 1,
        "conflict_policy": "rename",
    },
    "advanced": {
        "log_level": "INFO",
        "global_shortcuts": {},
    },
}


def _deep_merge(defaults: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(defaults))
    for key, value in values.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _migrate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(dict(document))
    version = int(raw.get("version", 0) or 0)
    if version > SETTINGS_VERSION:
        raise ValueError(f"Unsupported settings version: {version}")
    if version == 0:
        # Early experimental builds stored several values at the root.
        appearance = raw.setdefault("appearance", {})
        language = raw.setdefault("language", {})
        general = raw.setdefault("general", {})
        if "theme" in raw:
            appearance.setdefault("theme", raw.pop("theme"))
        if "accent" in raw:
            appearance.setdefault("accent", raw.pop("accent"))
        if "locale" in raw:
            language.setdefault("locale", raw.pop("locale"))
        if "last_folder" in raw:
            general.setdefault("last_folder", raw.pop("last_folder"))
        version = 1
    raw["version"] = version
    appearance = raw.setdefault("appearance", {})
    if isinstance(appearance, dict):
        appearance["theme"] = normalize_theme_id(appearance.get("theme", "system"))
    return _deep_merge(DEFAULT_SETTINGS, raw)


class SettingsService(QObject):
    """Settings repository with dot-path access and real-time Qt broadcasts."""

    setting_changed = Signal(str, object)
    settings_reloaded = Signal(object)
    settings_reset = Signal(object)
    persistence_error = Signal(str)

    def __init__(self, data_directory: Path | str, parent: QObject | None = None):
        super().__init__(parent)
        self.data_directory = Path(data_directory)
        self.path = self.data_directory / "settings.json"
        self._lock = threading.RLock()
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value: Any = self._settings
            for part in str(key).split("."):
                if not isinstance(value, Mapping) or part not in value:
                    return copy.deepcopy(default)
                value = value[part]
            return copy.deepcopy(value)

    def set(self, key: str, value: Any, *, persist: bool = True) -> bool:
        parts = [part for part in str(key).split(".") if part]
        if not parts or parts[0] == "version":
            return False
        with self._lock:
            node = self._settings
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            clean_value = copy.deepcopy(value)
            if ".".join(parts) == "appearance.theme":
                clean_value = normalize_theme_id(str(clean_value))
            if node.get(parts[-1]) == clean_value:
                return False
            node[parts[-1]] = clean_value
            if persist:
                self._write_locked()
        self.setting_changed.emit(".".join(parts), copy.deepcopy(clean_value))
        return True

    def update(self, values: Mapping[str, Any], *, persist: bool = True) -> None:
        changed: list[tuple[str, Any]] = []

        def visit(prefix: str, mapping: Mapping[str, Any]) -> None:
            for name, value in mapping.items():
                key = f"{prefix}.{name}" if prefix else name
                if isinstance(value, Mapping):
                    visit(key, value)
                elif key != "version" and self.get(key) != value:
                    changed.append((key, value))

        visit("", values)
        for key, value in changed:
            self.set(key, value, persist=False)
        if changed and persist:
            self.save()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                self._settings = copy.deepcopy(DEFAULT_SETTINGS)
                return self.snapshot()
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(document, Mapping):
                    raise ValueError("settings document must be an object")
                self._settings = _migrate_document(document)
            except Exception as exc:
                corrupt = self.path.with_suffix(".corrupt.json")
                try:
                    shutil.copy2(self.path, corrupt)
                except Exception:
                    pass
                self._settings = copy.deepcopy(DEFAULT_SETTINGS)
                self.persistence_error.emit(str(exc))
        snapshot = self.snapshot()
        self.settings_reloaded.emit(snapshot)
        return snapshot

    def save(self) -> None:
        with self._lock:
            self._write_locked()

    def _write_locked(self) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True)
        document = copy.deepcopy(self._settings)
        document["version"] = SETTINGS_VERSION
        payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.data_directory),
                prefix="settings.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except Exception:
                    pass
            self.persistence_error.emit(str(exc))
            raise

    def reset_defaults(self) -> dict[str, Any]:
        with self._lock:
            self._settings = copy.deepcopy(DEFAULT_SETTINGS)
            self._write_locked()
        snapshot = self.snapshot()
        self.settings_reset.emit(snapshot)
        self.settings_reloaded.emit(snapshot)
        return snapshot

    def export_to(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def import_from(self, path: Path | str) -> dict[str, Any]:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise ValueError("settings document must be an object")
        migrated = _migrate_document(document)
        with self._lock:
            self._settings = migrated
            self._write_locked()
        snapshot = self.snapshot()
        self.settings_reloaded.emit(snapshot)
        return snapshot


def windows_apps_use_light_theme() -> bool:
    if os.name != "nt":
        return True
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(int(value))
    except Exception:
        return True


def detect_lightroom_path() -> str:
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
            keys = (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Lightroom.exe",
                r"SOFTWARE\Adobe\Lightroom",
            )
            for root in roots:
                for key_name in keys:
                    try:
                        with winreg.OpenKey(root, key_name) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                            candidates.append(Path(str(value)))
                    except Exception:
                        pass
        except Exception:
            pass
        program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
        for base in [Path(value) for value in program_files if value]:
            adobe = base / "Adobe"
            if adobe.exists():
                candidates.extend(adobe.glob("Adobe Lightroom Classic*/*Lightroom.exe"))
                candidates.extend(adobe.glob("Adobe Lightroom*/*Lightroom.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return ""
