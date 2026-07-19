# Pictessera Photos v0.1.0

> 首个公开版本 / First public release

Pictessera Photos 是一款面向 Windows 的本地照片与 Live Photo 资料库工具。它直接处理用户选择的本地文件夹：不会登录 iCloud、不会自动上传照片，也不会为分类而移动或重命名原始文件。

## 下载

| 文件 | 适用场景 | 大小 |
| --- | --- | ---: |
| `Pictessera.exe` | 普通版：日常浏览、管理、HEIC/HEIF 支持与 Live Photo 配对 | 85.2 MB |
| `Pictessera-Plus.exe` | 增强媒体版：额外提供 OpenCV、NumPy、ImageIO 与 ffmpeg 回退链 | 345.9 MB |

两版均为 Windows 单文件程序。普通版的 Live Photo 动态预览依赖系统中可用的 ffmpeg；Plus 版包含更完整的本地视频解码回退能力。

## 本次亮点

- 最终命名**Pictessera Photos**。
- 扫描本地照片资料库，支持 HEIC、HEIF、JPEG、JPG、PNG 与 MOV。
- 自动按同目录、同文件名主体配对 Live Photo 的静态图与 MOV；未绑定 MOV 可人工处理。
- 虚拟化照片墙与表格视图，适合大型资料库。
- 后台缩略图、EXIF/HEIF 元数据读取与增量智能分类。
- 智能分类涵盖时间、媒体类型、来源、设备、位置、文件状态，以及 Plus 分类项目。
- 支持当前结果搜索、通配符、AND / OR、排除条件、标签与评分检索。
- 提供详情预览、缩放、旋转、批量选择、导出、应用内垃圾箱、撤销与重做。
- 支持简体中文、繁體中文与 English；提供现代、Windows 与经典 Mac 风格主题。
- 设置可实时调整外观、语言、扫描、缓存、删除策略、集成与分类选项。

## 数据与升级

- 数据默认保存在程序旁的 `Pictessera_Data/`。
- 从旧版升级时，程序会尝试将 `PhotoMoverQt_Data/` 自动迁移为 `Pictessera_Data/`，保留设置、缩略图缓存与分类数据。
- 首次处理重要资料库前，建议保留独立备份，并先使用小型测试文件夹熟悉移动、垃圾箱与导出流程。

## 已知事项

- Windows 10 / 11 是当前支持的平台。
- 普通版若系统未安装可用 ffmpeg，静态浏览、文件管理与 Live Photo 配对仍可使用，但动态预览可能不可用。
- 自动 Live Photo 配对主要依据目录与文件名主体，尚未校验 Apple asset identifier。
- 本次 Release 不携带 PyTorch、Transformers、AI 模型或外部模型文件；Plus 版定位为媒体解码增强版，不是 AI 捆绑版。
- 安装包尚未进行代码签名；首次下载和运行时，Windows SmartScreen 可能显示提示。

## 字体说明

Windows 2000 和 Mac OS 8 主题可选用 PoxiaoPixel 等复古字体。Pictessera 不分发 Chicago、Charcoal、Geneva 等专有字体，也不内置字体文件。PoxiaoPixel 可从[破晓字型仓库](https://forge.poxiao-labs.work/Fonts/fzg)取得；下载或再分发前请遵循其当前许可证。

---

# Pictessera Photos v0.1.0 — English

> First public release

Pictessera Photos is a local photo and Live Photo library for Windows. It works directly with the folders you choose: it does not sign in to iCloud, upload photos automatically, or move and rename source files just to classify them.

## Downloads

| Asset | Best for | Size |
| --- | --- | ---: |
| `Pictessera.exe` | Standard edition: everyday browsing, management, HEIC/HEIF support, and Live Photo pairing | 85.2 MB |
| `Pictessera-Plus.exe` | Enhanced-media edition: adds the OpenCV, NumPy, ImageIO, and ffmpeg fallback chain | 345.9 MB |

Both editions are single-file Windows applications. Standard-edition animated Live Photo previews use an available system ffmpeg; the Plus edition provides a broader local video-decoding fallback chain.

## Highlights

- New public identity: **Pictessera Photos**.
- Scan local libraries containing HEIC, HEIF, JPEG, JPG, PNG, and MOV files.
- Pair the still image and MOV components of a Live Photo by directory and filename stem; manually handle unbound MOV files when needed.
- Virtualized photo-wall and table views for large libraries.
- Background thumbnails, EXIF/HEIF metadata reading, and incremental smart categories.
- Categories for time, media, source, device, location, file status, and Plus-category items.
- Search the current result with wildcards, AND / OR expressions, exclusions, tags, and ratings.
- Detail preview, zoom, rotation, multi-selection, export, application trash, undo, and redo.
- Simplified Chinese, Traditional Chinese, and English UI text; modern, Windows, and classic Mac-inspired themes.
- Live settings for appearance, language, scanning, cache, deletion behavior, integrations, and classification.

## Data and Upgrades

- By default, state is stored beside the program in `Pictessera_Data/`.
- On upgrade, Pictessera attempts to migrate `PhotoMoverQt_Data/` to `Pictessera_Data/`, keeping settings, thumbnail cache, and classification data.
- Keep an independent backup before managing an important library, and try move, trash, and export workflows on a small test folder first.

## Known Notes

- Windows 10 and Windows 11 are the currently supported platforms.
- Without usable system ffmpeg, the standard edition still supports still-image browsing, file management, and Live Photo pairing, but animated previews may be unavailable.
- Automatic Live Photo pairing is based primarily on directory and filename stem; Apple asset identifiers are not yet verified.
- This release does not bundle PyTorch, Transformers, AI models, or external model folders. The Plus edition is an enhanced-media build, not an AI bundle.
- The executables are not code-signed yet. Windows SmartScreen may show a warning on first download or launch.

## Font Note

The Windows 2000 and Mac OS 8 themes can use optional retro fonts such as PoxiaoPixel. Pictessera does not redistribute proprietary Chicago, Charcoal, Geneva, or other font files. PoxiaoPixel is available from the [Poxiao Fonts repository](https://forge.poxiao-labs.work/Fonts/fzg); follow that repository's current licence before downloading or redistributing fonts.
