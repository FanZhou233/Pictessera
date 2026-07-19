"""Small JSON-ready translation service used while legacy strings are migrated."""

from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import QObject, QLocale, Signal


TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh_CN": {
        "settings.title": "设置",
        "settings.general": "通用",
        "settings.appearance": "外观",
        "settings.language": "语言",
        "settings.integration": "集成",
        "settings.advanced": "高级",
        "app.photos": "照片",
        "app.library": "照片资料库",
        "app.choose_folder": "选择照片文件夹…",
        "app.all_photos": "所有照片",
        "app.live_photos": "实况照片",
        "app.still_photos": "静态照片",
        "app.unbound_video": "待绑定视频",
        "app.recently_deleted": "最近删除",
        "app.smart_categories": "智能分类",
        "app.view": "视图",
        "app.photo_wall": "照片墙",
        "app.table": "表格",
        "app.exif_reorder": "EXIF 自动重排",
        "app.search_placeholder": "搜索当前筛选结果：支持 *.HEIC、IMG_12??、*.MOV;*.HEIC",
        "app.not_searched": "未搜索",
        "app.scan_idle": "扫描：未开始",
        "app.thumbnail_idle": "缩略图：未开始",
        "app.file_idle": "文件操作：空闲",
        "app.items": "{count} 项",
        "app.selection_status": "当前显示 {visible} 项；已选择 {selected} 项。",
        "app.summary": "{photos} 张照片 · 已选 {selected} · {size}",
        "common.system": "跟随系统",
        "common.light": "浅色",
        "common.dark": "深色",
        "common.auto": "自动",
        "common.off": "关闭",
        "common.browse": "浏览…",
        "common.detect": "自动检测",
        "common.clear": "清除",
        "common.open": "打开",
        "common.reset": "恢复默认",
    },
    "zh_TW": {
        "settings.title": "設定",
        "settings.general": "一般",
        "settings.appearance": "外觀",
        "settings.language": "語言",
        "settings.integration": "整合",
        "settings.advanced": "進階",
        "app.photos": "照片",
        "app.library": "照片圖庫",
        "app.choose_folder": "選擇照片資料夾…",
        "app.all_photos": "所有照片",
        "app.live_photos": "原況照片",
        "app.still_photos": "靜態照片",
        "app.unbound_video": "待綁定影片",
        "app.recently_deleted": "最近刪除",
        "app.smart_categories": "智慧分類",
        "app.view": "顯示方式",
        "app.photo_wall": "照片牆",
        "app.table": "表格",
        "app.exif_reorder": "EXIF 自動重排",
        "app.search_placeholder": "搜尋目前篩選結果：支援 *.HEIC、IMG_12??、*.MOV;*.HEIC",
        "app.not_searched": "尚未搜尋",
        "app.scan_idle": "掃描：尚未開始",
        "app.thumbnail_idle": "縮圖：尚未開始",
        "app.file_idle": "檔案操作：閒置",
        "app.items": "{count} 項",
        "app.selection_status": "目前顯示 {visible} 項；已選取 {selected} 項。",
        "app.summary": "{photos} 張照片 · 已選取 {selected} · {size}",
        "common.system": "跟隨系統",
        "common.light": "淺色",
        "common.dark": "深色",
        "common.auto": "自動",
        "common.off": "關閉",
        "common.browse": "瀏覽…",
        "common.detect": "自動偵測",
        "common.clear": "清除",
        "common.open": "開啟",
        "common.reset": "回復預設值",
    },
    "en": {
        "settings.title": "Settings",
        "settings.general": "General",
        "settings.appearance": "Appearance",
        "settings.language": "Language",
        "settings.integration": "Integrations",
        "settings.advanced": "Advanced",
        "app.photos": "Photos",
        "app.library": "Photo Library",
        "app.choose_folder": "Choose Photo Folder…",
        "app.all_photos": "All Photos",
        "app.live_photos": "Live Photos",
        "app.still_photos": "Still Photos",
        "app.unbound_video": "Unbound Videos",
        "app.recently_deleted": "Recently Deleted",
        "app.smart_categories": "Smart Categories",
        "app.view": "View",
        "app.photo_wall": "Photos",
        "app.table": "Table",
        "app.exif_reorder": "EXIF Auto Reorder",
        "app.search_placeholder": "Search current results: *.HEIC, IMG_12??, *.MOV;*.HEIC",
        "app.not_searched": "Not searched",
        "app.scan_idle": "Scan: Not started",
        "app.thumbnail_idle": "Thumbnails: Not started",
        "app.file_idle": "File operations: Idle",
        "app.items": "{count} items",
        "app.selection_status": "Showing {visible} items; {selected} selected.",
        "app.summary": "{photos} photos · {selected} selected · {size}",
        "common.system": "System",
        "common.light": "Light",
        "common.dark": "Dark",
        "common.auto": "Automatic",
        "common.off": "Off",
        "common.browse": "Browse…",
        "common.detect": "Detect",
        "common.clear": "Clear",
        "common.open": "Open",
        "common.reset": "Restore Defaults",
    },
}


# Settings pages pre-date the key-based translation service.  Keeping their
# original Chinese text as the stable source lets us migrate the whole window
# without rebuilding it (and without losing the current control values).
EN_UI_TEXT: dict[str, str] = {
    "全部本地处理\n照片、标签和分类数据不会上传": "Processed entirely on this device\nPhotos, tags, and classification data are never uploaded",
    "通用": "General", "外观": "Appearance", "语言": "Language", "集成": "Integrations", "高级": "Advanced",
    "启动、浏览、扫描性能、删除安全与 Live Photo 的默认行为。": "Defaults for startup, browsing, scan performance, deletion safety, and Live Photos.",
    "启动与浏览": "Startup & Browsing", "恢复上次文件夹": "Restore last folder",
    "启动后恢复最后使用的照片资料库。": "Restore the most recently used photo library at startup.",
    "启动时自动扫描": "Scan automatically at startup", "恢复文件夹后立即开始扫描。": "Start scanning as soon as the folder is restored.",
    "默认视图": "Default view", "首次打开资料库时使用的呈现方式。": "View used when a library is first opened.",
    "照片墙": "Photos", "表格": "Table", "默认排序": "Default sort",
    "控制首次扫描后的默认顺序。": "Default order after the first scan.", "时间：旧到新": "Date: Oldest First",
    "时间：新到旧": "Date: Newest First", "名称": "Name", "缩略图尺寸": "Thumbnail size",
    "照片墙缩略图的默认显示大小。": "Default thumbnail size in Photos view.", "小": "Small", "中": "Medium", "大": "Large",
    "扫描与性能": "Scanning & Performance", "递归子文件夹": "Include subfolders",
    "扫描所选目录下的全部子目录。": "Scan all subfolders inside the selected folder.", "工作线程": "Worker threads",
    "0 表示根据当前设备自动选择。": "0 selects a suitable value for this device.", "缩略图缓存上限": "Thumbnail cache limit",
    "达到上限后优先清理较旧缓存。": "Older cached thumbnails are removed when the limit is reached.",
    "排除规则": "Exclusion rules", "使用分号分隔文件夹名或通配符。": "Separate folder names or wildcard patterns with semicolons.",
    "例如：.git; node_modules; *_cache": "Example: .git; node_modules; *_cache", "缓存维护": "Cache maintenance",
    "立即删除已生成的缩略图缓存。": "Delete generated thumbnail cache now.", "清除缩略图缓存": "Clear Thumbnail Cache",
    "删除与安全": "Deletion & Safety", "删除行为": "Delete behavior",
    "推荐保留应用内垃圾箱以便恢复。": "The in-app Trash is recommended so items can be restored.",
    "应用内垃圾箱": "In-app Trash", "系统回收站": "Recycle Bin", "直接删除": "Delete Permanently",
    "垃圾箱自动清理": "Automatically empty Trash", "0 表示永不自动清理。": "0 means never empty it automatically.",
    "危险操作二次确认": "Confirm dangerous actions", "直接删除、覆盖导出等操作再次确认。": "Ask again before permanent deletion or overwriting exports.",
    "悬停自动播放": "Play on hover", "鼠标停留在实况照片上时开始播放。": "Play a Live Photo when the pointer rests on it.",
    "悬停延迟": "Hover delay", "减少快速划过照片时的解码开销。": "Avoid decoding photos the pointer only passes over.",
    "播放声音": "Play sound", "当前预览解码链默认只读取画面。": "The current preview decoder normally reads video only.",
    "主题、标题栏和强调色都会即时应用，无需重新启动。": "Theme, title-bar style, and accent color apply immediately.",
    "界面": "Interface", "主题": "Theme", "跟随系统时监听 Windows 的 AppsUseLightTheme。": "Follow System monitors Windows AppsUseLightTheme.",
    "跟随系统": "Follow System", "浅色": "Light", "深色": "Dark", "标题栏风格": "Title-bar style",
    "在 macOS 红绿灯与 Windows 按钮布局之间切换。": "Switch between macOS traffic lights and Windows window controls.",
    "macOS 红绿灯": "macOS Traffic Lights", "Windows 按钮": "Windows Controls", "强调色": "Accent color",
    "用于选中状态、按钮、进度和焦点描边。": "Used for selection, buttons, progress, and focus rings.",
    "设置窗口与主界面支持即时语言切换。": "Settings and the main interface switch languages immediately.",
    "界面语言": "Interface Language", "简体中文": "Simplified Chinese", "繁體中文": "Traditional Chinese",
    "日期、文件大小和新界面文本使用所选区域设置。": "Dates, file sizes, and interface text use the selected locale.",
    "即时切换": "Switch immediately", "关闭时将在下次启动后完整应用语言。": "When disabled, the language is fully applied at next launch.",
    "所有设置页面和主界面常驻控件均会即时切换语言；个别文件操作结果保留原始文件名。": "All settings pages and persistent main-window controls switch immediately; file-operation results keep original file names.",
    "从照片右键菜单调用桌面编辑器和 Windows 打开方式。": "Open desktop editors and Windows Open With from a photo's context menu.",
    "外部应用": "External Apps", "支持注册表、常见安装目录检测和手动指定。": "Supports registry detection, common install locations, and manual selection.",
    "指定 Photoshop 可执行文件。": "Choose the Photoshop executable.", "系统默认查看器": "Default system viewer",
    "在右键菜单中显示“用默认应用打开”。": "Show “Open with default app” in the context menu.",
    "选择照片后，右键菜单可在 Lightroom、Photoshop、系统默认查看器中打开，或在资源管理器中定位。所有操作只传递本地文件路径。": "After selecting photos, use the context menu to open them in Lightroom, Photoshop, the default viewer, or File Explorer. Only local file paths are passed.",
    "浏览…": "Browse…", "自动检测": "Detect", "分类、导出、数据维护以及版本信息。": "Classification, export, data maintenance, and version information.",
    "智能分类": "Smart Classification", "时间": "Time", "媒体类型": "Media type", "拍摄设备": "Camera",
    "GPS 位置": "GPS location", "文件状态": "File status", "Plus 分析": "Plus analysis",
    "关闭后重建分类缓存即可移除该规则结果。": "Disable a rule, then rebuild the classification cache to remove its results.",
    "大文件阈值": "Large-file threshold", "文件状态分类使用的容量阈值。": "Size threshold used by file-status classification.",
    "内容识别": "Content recognition", "使用完全位于本机的视觉模型生成苹果、桌子等标签。": "Use an entirely local vision model to generate tags such as apple and table.",
    "本地模型目录": "Local model folder", "兼容 Transformers 的本地图像分类模型；程序不会上传照片。": "A local Transformers-compatible image model; photos are never uploaded.",
    "标签置信度": "Tag confidence", "低于该置信度的自动标签会被忽略。": "Automatic tags below this confidence are ignored.",
    "分类缓存": "Classification cache", "丢弃旧快照并重新执行当前启用规则。": "Discard the old snapshot and run enabled rules again.",
    "重建分类缓存": "Rebuild Classification Cache", "导出": "Export", "默认导出目录": "Default export folder",
    "导出对话框优先从此目录开始。": "Export dialogs start in this folder.", "DCF 起始编号": "DCF starting number",
    "IMG_0001 对应 1。": "1 corresponds to IMG_0001.", "冲突策略": "Conflict policy",
    "目标文件已存在时的默认处理。": "Default action when the destination file exists.", "跳过": "Skip", "自动重命名": "Rename Automatically", "覆盖": "Overwrite",
    "数据与诊断": "Data & Diagnostics", "应用数据": "App data", "打开 Pictessera_Data。": "Open Pictessera_Data.",
    "打开数据文件夹": "Open Data Folder", "日志级别": "Log level", "更详细的日志可能增加磁盘写入。": "More detailed logging may increase disk writes.",
    "错误": "Errors", "警告": "Warnings", "信息": "Info", "调试": "Debug", "设置备份": "Settings backup",
    "JSON 文件可用于迁移或恢复。": "Use a JSON file to migrate or restore settings.", "导出设置…": "Export Settings…",
    "导入设置…": "Import Settings…", "恢复默认设置": "Restore Defaults", "关于": "About",
    "全部照片分析和设置数据均在本地处理。": "All photo analysis and settings data are processed locally.",
    "资料库": "Library", "停止扫描": "Stop Scan", "重新扫描": "Rescan",
    "停止": "Stop", "扫描": "Scan", "全选": "Select All", "设置": "Settings", "取消": "Clear",
    "全选当前筛选结果": "Select All Current Results", "取消选择": "Clear Selection",
    "移动选中项": "Move Selected", "全部删除": "Delete All", "筛选": "Filter",
    "全部": "All", "仅 LIVE 实况": "Live Only", "仅非 LIVE": "Non-Live Only",
    "未绑定实况 MOV": "Unbound Live MOV", "垃圾箱": "Trash", "视图": "View",
    "EXIF 自动重排": "EXIF Auto Reorder", "大量照片时，自动重排会延后执行，避免拖选时卡顿。": "For large libraries, auto reorder is deferred to keep drag selection responsive.",
    "扫描：未开始": "Scan: Not started", "缩略图：未开始": "Thumbnails: Not started",
    "文件操作：空闲": "File operations: Idle", "说明：此版使用 Qt 虚拟模型；切换视图不会重建上千个控件，避免卡死。": "Ready",
    "这里还没有照片": "No Photos Here", "开始整理你的照片资料库": "Start Organizing Your Photo Library",
    "当前分类没有符合条件的项目": "No items match the current category",
    "选择一个照片文件夹，照片会在这里安全地显示": "Choose a photo folder and your photos will appear here",
    "文件": "File", "时间": "Date", "类型": "Type", "文件数": "Files", "时间来源": "Date Source", "来源文件夹": "Source Folder",
    "未知设备": "Unknown Camera", "位置": "Location", "有位置信息": "With Location", "无位置信息": "Without Location",
    "来源": "Source", "事件": "Events", "连拍": "Bursts", "屏幕截图": "Screenshots",
    "重复照片": "Duplicates", "相似照片": "Similar Photos", "质量": "Quality", "人脸聚类": "People",
    "自定义分类": "Custom Categories", "我的标记": "My Tags", "内容识别": "Content Recognition",
    "分类错误": "Classification Errors", "时间未知": "Unknown Date", "最近 7 天": "Last 7 Days", "最近 30 天": "Last 30 Days",
    "静态照片": "Still Photos", "实况照片": "Live Photos", "小图片": "Small Images", "大文件": "Large Files",
    "选择应用程序": "Choose Application", "应用程序 (*.exe);;所有文件 (*)": "Applications (*.exe);;All Files (*)",
    "选择文件夹": "Choose Folder", "已找到：": "Found:", "未在注册表或常见安装目录中找到 Lightroom。": "Lightroom was not found in the registry or common install locations.",
    "导出设置": "Export Settings", "导入设置": "Import Settings", "导入失败": "Import Failed",
    "确定恢复全部默认设置吗？": "Restore all default settings?",
}


# This character-level fallback covers legacy settings strings that have not
# needed a phrase-specific Taiwanese wording yet. Phrase overrides below keep
# common UI terminology natural (資料夾, 檔案, 縮圖, etc.).
_TRADITIONAL_CHARS = str.maketrans({
    "设": "設", "启": "啟", "动": "動", "浏": "瀏", "览": "覽", "扫": "掃", "删": "刪",
    "实": "實", "况": "況", "预": "預", "为": "為", "复": "復", "资": "資", "库": "庫",
    "显": "顯", "墙": "牆", "顺": "順", "间": "間", "旧": "舊", "称": "稱", "缩": "縮",
    "略": "略", "图": "圖", "递": "遞", "归": "歸", "线": "線", "选": "選", "当": "當",
    "备": "備", "达": "達", "优": "優", "较": "較", "缓": "緩", "规": "規", "则": "則",
    "号": "號", "隔": "隔", "维": "維", "护": "護", "产": "產", "应": "應", "统": "統",
    "收": "收", "险": "險", "认": "認", "盖": "蓋", "导": "匯", "标": "標", "栏": "列",
    "强": "強", "调": "調", "进": "進", "边": "邊", "语": "語", "热": "熱",
    "历": "歷", "话": "話", "态": "態", "消": "訊", "息": "息", "将": "將", "块": "塊",
    "迁": "遷", "区": "區", "闭": "閉", "整": "整", "键": "鍵", "单": "單", "调": "調",
    "辑": "輯", "开": "開", "册": "冊", "径": "徑", "检": "檢", "测": "測", "执": "執",
    "认": "認", "查": "查", "传": "傳", "类": "類", "数": "數", "据": "據", "级": "級",
    "摄": "攝", "处": "處", "结": "結", "阈": "閾", "容": "容", "内": "內", "识": "識",
    "觉": "覺", "苹": "蘋", "签": "籤", "兼": "兼", "程": "程", "传": "傳", "过": "過",
    "滤": "濾", "弃": "棄", "编": "編", "冲": "衝", "误": "誤", "设": "設", "关": "關",
})

ZH_TW_PHRASES = {
    "文件夹": "資料夾", "文件": "檔案", "缩略图": "縮圖", "照片资料库": "照片圖庫",
    "全部本地处理": "全部在本機處理", "不会上传": "不會上傳", "应用内垃圾箱": "應用程式垃圾桶",
    "自动检测": "自動偵測", "智能分类": "智慧分類", "简体中文": "簡體中文",
    "通用": "一般", "集成": "整合", "高级": "進階", "默认": "預設", "信息": "資訊",
    "查看器": "檢視器", "资源管理器": "檔案總管", "回收站": "資源回收筒",
    "鼠标": "滑鼠", "缓存": "快取", "线程": "執行緒", "目录": "目錄", "程序": "程式",
}


def _to_traditional(value: str) -> str:
    """Use Windows' built-in Chinese conversion; keep a portable fallback."""
    if os.name == "nt" and value:
        try:
            # LCMAP_TRADITIONAL_CHINESE is available on every supported Windows
            # release and avoids shipping a second language-conversion package.
            convert = ctypes.windll.kernel32.LCMapStringEx
            convert.argtypes = [
                ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int,
                ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ]
            convert.restype = ctypes.c_int
            flags = 0x04000000
            size = convert("zh-TW", flags, value, len(value), None, 0, None, None, 0)
            if size > 0:
                buffer = ctypes.create_unicode_buffer(size)
                written = convert("zh-TW", flags, value, len(value), buffer, size, None, None, 0)
                if written > 0:
                    return buffer[:written]
        except Exception:
            pass
    return value.translate(_TRADITIONAL_CHARS)


class TranslationService(QObject):
    language_changed = Signal(str)

    def __init__(self, locale: str = "system", parent: QObject | None = None):
        super().__init__(parent)
        self._locale_setting = "system"
        self._locale = "zh_CN"
        self.set_locale(locale, emit=False)

    @staticmethod
    def system_locale() -> str:
        name = QLocale.system().name().replace("-", "_")
        if name.lower().startswith("zh_tw") or name.lower().startswith("zh_hk"):
            return "zh_TW"
        if name.lower().startswith("zh"):
            return "zh_CN"
        return "en"

    @property
    def locale(self) -> str:
        return self._locale

    def set_locale(self, locale: str, *, emit: bool = True) -> None:
        setting = str(locale or "system")
        resolved = self.system_locale() if setting == "system" else setting
        if resolved not in TRANSLATIONS:
            resolved = "en"
        changed = resolved != self._locale
        self._locale_setting = setting
        self._locale = resolved
        if changed and emit:
            self.language_changed.emit(resolved)

    def tr(self, key: str, default: str | None = None, **values) -> str:
        text = TRANSLATIONS.get(self._locale, {}).get(key)
        if text is None:
            text = TRANSLATIONS["zh_CN"].get(key, default if default is not None else key)
        try:
            return text.format(**values)
        except Exception:
            return text

    def text(self, source: str) -> str:
        """Translate a legacy static UI string while preserving unknown text."""
        value = str(source or "")
        if self._locale == "zh_CN":
            return value
        if self._locale == "en":
            # Version labels contain a stable translated second line.
            if value.startswith("照片资料库  ") and "\n" in value:
                first, _second = value.split("\n", 1)
                return first.replace("照片资料库", "Photo Library") + "\n" + EN_UI_TEXT["全部照片分析和设置数据均在本地处理。"]
            return EN_UI_TEXT.get(value, value)
        translated = _to_traditional(value)
        for source_phrase, target_phrase in ZH_TW_PHRASES.items():
            translated = translated.replace(_to_traditional(source_phrase), target_phrase)
        return translated
