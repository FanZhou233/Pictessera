# 自动分类 ToDoList

## P0：领域模型与规则（MVP）

- [x] `photo_manager/domain/categories.py`
  - [x] 定义 `AutoCategory`
  - [x] 定义 `ItemCategoryRelation`
  - [x] 定义分类枚举
- [x] `photo_manager/services/classification.py`
  - [x] 实现 `ClassificationService`
  - [x] 支持规则注册
  - [x] 支持单项分类
  - [x] 支持批量分类
  - [x] 支持增量分类
- [x] `photo_manager/services/classification_rules/time_rule.py`
  - [x] 按年份分类
  - [x] 按年月分类
  - [x] 最近 7 天
  - [x] 最近 30 天
  - [x] 时间未知
- [x] `photo_manager/services/classification_rules/media_rule.py`
  - [x] Live Photo
  - [x] 静态照片
  - [x] HEIC/HEIF
  - [x] JPEG
  - [x] PNG
  - [x] 未绑定 MOV
- [x] `photo_manager/services/classification_rules/file_rule.py`
  - [x] 小图片
  - [x] 大图片
  - [x] 大文件
  - [x] 元数据缺失
- [x] 编写与 GUI 解耦的单元测试（`tests/`）

## P0：UI

- [x] 将左侧“智能分类”改为可点击分类树
  - [x] 支持展开和折叠
  - [x] 显示项目计数
  - [x] 显示当前选中状态
- [x] 点击分类后更新 `visible_ids`
- [x] 照片墙与表格视图同步分类结果
- [x] 搜索范围限定为当前分类结果
- [x] 保留现有分类
  - [x] 所有照片
  - [x] 实况照片
  - [x] 静态照片
  - [x] 待绑定视频
  - [x] 最近删除/垃圾箱

## P1：规则补全与缓存

- [x] `photo_manager/services/classification_rules/device_rule.py`
  - [x] 按 EXIF `Make` 分类
  - [x] 按 EXIF `Model` 分类
  - [x] 未知设备
- [x] `photo_manager/services/classification_rules/location_rule.py`
  - [x] 有 GPS 信息
  - [x] 无 GPS 信息
  - [x] 经纬度分组
- [x] `photo_manager/services/classification_rules/source_rule.py`
  - [x] 按源文件夹分类
  - [x] 按一级子目录分类
- [x] `photo_manager/infrastructure/category_repository.py`
  - [x] JSON 原子写入
  - [x] 自动备份
  - [x] 损坏文件隔离
  - [x] 缓存版本迁移
- [x] 新增缓存文件
  - [x] `Pictessera_Data/auto_categories.json`
  - [x] `Pictessera_Data/item_category_relations.json`
- [x] 实现增量分类
  - [x] 使用 `stable_key + file_signature` 判断项目是否变化
  - [x] 签名未变化时不重复读取 EXIF
- [x] 实现缓存失效规则
  - [x] 路径变化
  - [x] 文件大小变化
  - [x] 修改时间变化
  - [x] 文件签名变化
  - [x] EXIF 信息变化
  - [x] MOV 绑定变化
  - [x] 分类规则版本变化
  - [x] 文件删除后清理分类关系

## P1：事件接入

- [x] 扫描期间和扫描完成后在后台执行分类
- [x] 分类过程中逐步更新分类计数
- [x] 删除项目后移出普通分类统计
- [x] 恢复项目后恢复分类关系
- [x] MOV 绑定变化后只重新分类对应项目
- [x] 分类结果分批提交到 UI
- [x] 避免高频重建照片模型和分类树

## P0/P1：性能与健壮性

- [x] 分类使用独立线程池
- [x] 使用 `stop_event + generation` 防止旧任务结果污染新扫描
- [x] 程序关闭时立即取消分类任务
- [x] 单项分类失败不终止整个任务
- [x] 记录分类失败项目和原因
- [x] 异常项目进入对应异常分类
- [x] 分类失败不阻塞照片浏览
- [x] 自动分类不修改原始文件
- [x] 使用 10,000 个项目进行性能验证
  - [x] 分类过程不卡住 UI
  - [ ] 分类切换正常
  - [ ] 照片墙和表格切换正常
  - [ ] 当前分类搜索正常

## P2：可选 Plus/AI 模块

- [x] 事件聚合
- [x] 连拍识别
- [x] 屏幕截图识别
- [x] 重复照片识别
- [x] 相似照片识别
- [x] 模糊照片识别
- [x] 人脸聚类
- [x] 图片内容识别
- [x] 自定义分类规则
- [x] 收藏
- [x] 评分
- [x] 人工标签
- [x] 数据量较大时迁移到 SQLite
  - [x] 数据库文件：`Pictessera_Data/photo_manager.db`

## 验收核对

- [ ] 对照 `SYSTEM_OVERVIEW_AND_AUTO_CLASSIFICATION_REQUIREMENTS.txt`
      第 4.11 节的十二条验收标准逐项验证
- [x] 自动分类不移动原始文件
- [x] 自动分类不复制原始文件
- [x] 自动分类不重命名原始文件
- [x] Live Photo 始终作为一个项目，不拆分图片和 MOV
- [x] 同一个项目可以属于多个分类
- [x] 程序重启后能够恢复分类结果
- [x] 未变化项目不会重复执行昂贵的元数据读取
- [x] 轻量版不引入 AI 或大型模型依赖

## 实施顺序

1. P0：领域模型、分类服务、时间/媒体/文件规则和分类树。
2. P1：设备/GPS/来源规则、缓存、增量分类和事件接入。
3. P2：事件、相似度、人脸、内容识别和其他可选模块。

## P3 额外要求
- [x] 右键图片的框里能够直接选择复制照片
- [x] 双击打开图片后能够自由选择左旋转90或右旋转90度图片

## 前端 ToDoList

### P0 设计基础（Design Tokens）
- [x] 定义 QSS 变量表：颜色（systemGray 1-6、accentBlue #007AFF/#0A84FF）、圆角（6/8/10px）、间距（4/8/12/16）
- [x] 字体层级：标题 13px semibold / 正文 13px / 辅助 11px 灰色（Segoe UI 或 SF 替代）
- [x] 统一分隔线：1px rgba(0,0,0,0.08)

### P0 侧栏（重点）
- [x] 每项添加图标（照片/心形/时钟/相机/定位等，使用轻量单色符号）
- [x] 分组标题灰色 11px，组间距 16px
- [x] 智能分类计数右对齐、灰色、无背景
- [x] 选中态：整行圆角 6px 填充
- [x] hover 态：极浅灰填充
- [x] 分类树展开/折叠 chevron，并启用展开动画
- [x] 缩进层级：每级 +16px
- [x] 侧栏底色比内容区略深（#F5F5F7 vs #FFFFFF）

### P0 工具栏
- [x] 扫描按钮图标化
- [x] 视图切换使用分段控件风格
- [x] 搜索框：圆角胶囊、内嵌搜索图标、占位灰字、聚焦蓝色描边
- [x] 进度区域轻量化为细线指示
- [x] 全选按钮改为工具栏图标

### P1 照片墙
- [x] 缩略图间距统一 8px，圆角 4px
- [x] hover：轻微变暗遮罩
- [x] 选中：3px 蓝色描边 + 右下角蓝色✓圆形徽章
- [x] LIVE 角标：左上角半透明黑胶囊
- [ ] 空状态：居中灰色图标 + 提示文字

### P1 状态栏与细节
- [x] 底部合并为单行居中灰字摘要
- [x] 标题栏：路径按钮胶囊样式，右侧计数灰字
- [x] 滚动条：细窄、圆角、半透明，悬停加深

### P2 动效与深色模式
- [x] 选中切换使用现有 150-200ms 级 ease 动效
- [ ] 展开/折叠 chevron 旋转动画
- [ ] 深色模式主题（#1E1E1E 底、#0A84FF 强调），跟随系统或手动切换
- [ ] 侧栏毛玻璃效果（Windows 11 Mica/Acrylic，失败纯色回退）
