import json
import tempfile
import unittest
from pathlib import Path

from photo_manager.services.i18n import TranslationService
from photo_manager.services.settings import DEFAULT_SETTINGS, SETTINGS_VERSION, SettingsService
from photo_manager.ui.theme_profiles import normalize_theme_id, resolve_theme_profile, theme_display_point_size, theme_font_candidates


class SettingsServiceTests(unittest.TestCase):
    def test_defaults_are_available_without_creating_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SettingsService(Path(tmp))
            self.assertEqual(service.get("appearance.theme"), "system")
            self.assertTrue(service.get("scan.recursive"))
            self.assertFalse((Path(tmp) / "settings.json").exists())

    def test_set_persists_atomically_and_emits_dot_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SettingsService(Path(tmp))
            changes = []
            service.setting_changed.connect(lambda key, value: changes.append((key, value)))
            self.assertTrue(service.set("appearance.accent", "purple"))
            document = json.loads((Path(tmp) / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(document["appearance"]["accent"], "purple")
            self.assertEqual(changes, [("appearance.accent", "purple")])
            self.assertEqual(list(Path(tmp).glob("settings.*.tmp")), [])

    def test_version_zero_document_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"version": 0, "theme": "dark", "locale": "en"}), encoding="utf-8")
            service = SettingsService(Path(tmp))
            self.assertEqual(service.get("appearance.theme"), "dark")
            self.assertEqual(service.get("language.locale"), "en")
            self.assertEqual(service.get("version"), SETTINGS_VERSION)

    def test_windows_theme_variants_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SettingsService(Path(tmp))
            for theme in ("win11", "win7", "win2000", "macos8"):
                service.set("appearance.theme", theme)
                reloaded = SettingsService(Path(tmp))
                self.assertEqual(reloaded.get("appearance.theme"), theme)

    def test_unknown_theme_falls_back_to_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SettingsService(Path(tmp))
            service.set("appearance.theme", "future-glass")
            self.assertEqual(service.get("appearance.theme"), "system")

    def test_import_merges_missing_defaults_and_reset_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "import.json"
            source.write_text(json.dumps({"version": 1, "general": {"default_view": "table"}}), encoding="utf-8")
            service = SettingsService(Path(tmp) / "data")
            service.import_from(source)
            self.assertEqual(service.get("general.default_view"), "table")
            self.assertEqual(service.get("appearance.theme"), DEFAULT_SETTINGS["appearance"]["theme"])
            service.reset_defaults()
            self.assertEqual(service.get("general.default_view"), "grid")


class TranslationServiceTests(unittest.TestCase):
    def test_manual_language_switch_and_fallback(self):
        service = TranslationService("en")
        self.assertEqual(service.tr("settings.title"), "Settings")
        service.set_locale("zh_TW")
        self.assertEqual(service.tr("settings.title"), "設定")
        self.assertEqual(service.tr("missing.key", "Fallback"), "Fallback")

    def test_legacy_settings_text_switches_losslessly(self):
        service = TranslationService("zh_CN")
        source = "恢复上次文件夹"
        self.assertEqual(service.text(source), source)
        service.set_locale("en")
        self.assertEqual(service.text(source), "Restore last folder")
        service.set_locale("zh_TW")
        self.assertEqual(service.text(source), "恢復上次資料夾")
        service.set_locale("zh_CN")
        self.assertEqual(service.text(source), source)

    def test_main_window_format_strings_are_localized(self):
        service = TranslationService("en")
        self.assertEqual(service.tr("app.items", count=3), "3 items")
        self.assertEqual(
            service.tr("app.selection_status", visible=12, selected=2),
            "Showing 12 items; 2 selected.",
        )

    def test_privacy_note_uses_text_without_emoji(self):
        source = "全部本地处理\n照片、标签和分类数据不会上传"
        translated = TranslationService("en").text(source)
        self.assertEqual(
            translated,
            "Processed entirely on this device\nPhotos, tags, and classification data are never uploaded",
        )
        self.assertNotIn(chr(0x1F512), source + translated)


class ThemeProfileTests(unittest.TestCase):
    def test_flavor_geometry_and_icon_policy(self):
        win11 = resolve_theme_profile("win11")
        win7 = resolve_theme_profile("win7")
        win2000 = resolve_theme_profile("win2000")
        macos8 = resolve_theme_profile("macos8")
        self.assertEqual((win11.corner_style, win11.control_radius), ("rounded", 4))
        self.assertEqual((win7.corner_style, win7.icon_policy), ("rounded", "text"))
        self.assertEqual(normalize_theme_id("win7glass"), "win7")
        self.assertEqual((win2000.corner_style, win2000.icon_policy), ("square", "text"))
        self.assertEqual((macos8.corner_style, macos8.titlebar_skin), ("square", "macos8"))
        self.assertTrue(win2000.fixed_accent)
        self.assertTrue(macos8.fixed_accent)

    def test_theme_fonts_are_locale_and_period_aware(self):
        self.assertEqual(theme_font_candidates("win11", "en")[0], "Segoe UI Variable Text")
        self.assertEqual(theme_font_candidates("win7", "zh_CN")[0], "Microsoft YaHei")
        self.assertEqual(theme_font_candidates("win2000", "en")[0], "PoxiaoPixel")
        self.assertEqual(theme_font_candidates("win2000", "zh_CN")[0], "PoxiaoPixel")
        self.assertEqual(theme_font_candidates("win2000", "zh_TW")[0], "PoxiaoPixel")
        self.assertEqual(theme_font_candidates("macos8", "en")[0], "Chicago")
        self.assertEqual(theme_font_candidates("macos8", "zh_CN")[0], "PoxiaoPixel")
        self.assertEqual(theme_font_candidates("macos8", "zh_TW")[0], "PoxiaoPixel")
        self.assertEqual(theme_display_point_size("win2000", "en", 9), 11)
        self.assertEqual(theme_display_point_size("win2000", "zh_CN", 9), 11)
        self.assertEqual(theme_display_point_size("macos8", "zh_CN", 9), 11)
        self.assertEqual(theme_display_point_size("macos8", "en", 9), 9)


if __name__ == "__main__":
    unittest.main()
