# AI-SVWF 落地实施规划与架构蓝图 (MVP V1.0 工业化参考升级版)

> **状态校正（2026-09-14）**：本文是初始实施蓝图，不等同于已验收清单。当前可验证实现与真实接口边界以 `INTERFACE_CONTRACT.md`、自动化测试和实际 Provider 联调结果为准。火山 Ark 的 GLM、Seedream 5.0/4.5 与 Seedance 2.0 已完成单镜真实烟测；飞书、可信人像、三镜人工 QA、Round 1/2 和云部署仍待项目配置与业务验收。

> 核心依据：
> 1. 《AI带货视频工作流_MVP技术交接文档_V1.0.md》(2073行标准规范)
> 2. 《AIGC内容资产中心实现目标.txt》
> 3. 《AIGC内容资产中心.jpg》(飞书多维表格 00_管理表资产中心架构)
> 4. 早期方案记录过若干 GitHub 项目名称作为调研线索，但当前仓库没有可核验的代码来源、版本、提交或许可证对应关系。因此本文只保留“异步任务状态机、FFmpeg 媒体标准化、受控 Prompt 变体、SQLite + outbox”等概念参考，不把项目名称、Star 数或“吸收某项目实现”作为已验证事实。

---

## 一、 系统定位与架构全景

AI-SVWF 是一个面向 AIGC 内容创作者和电商视频自动化的轻量工作流 MVP，核心定位是：
**“下接火山 Ark 模型接口，上接可选飞书多维表格镜像，内聚分镜装配、合规约束与单镜修复”**。

```text
┌────────────────────────────────────────────────────────────────────────┐
│               前端操作台 (Modern Storyboard Pipeline Studio)           │
│  - 商品图文输入与分析面板       - S01/S02/S03 三镜头时序看板            │
│  - 11层Prompt实时装配预览       - 单镜头生成进度与播放器               │
│  - QA质量评分与Failure Code标定 - 针对失败镜头一键Repair与V1.1单镜重跑 │
│  - 15秒成品视频缝合预览与下载   - 飞书Bitable同步指示灯 & 演示Mock开关 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP REST API (FastAPI)
┌───────────────────────────────────▼────────────────────────────────────┐
│                    核心引擎层 (Python 3.10+ / FastAPI)                 │
│                                                                        │
│  1. 商品分析与合规风控 (ProductAnalyzer & ComplianceGuard)            │
│     - 提取商品特征，计算 information_confidence 可信度分数 (0.0~1.0)   │
│     - 严格区分 confirmed_information (已确认) 与 possible (推测)      │
│     - 硬编码拦截五大禁止项：未提供检测、参数、医疗、收益、绝对化极限词 │
│                                                                        │
│  2. 11层Prompt自动化装配器 (PromptBuilder)                             │
│     - 20 个标准积木模块库（人物肤质、自然动作、商品锁定、负向约束）     │
│     - 严格遵循 11 步流水线组装逻辑，输出结构化 Schema V1.0            │
│     - 适配 Seedance 2.0 / 即梦视频模型的结构化提示词规范               │
│                                                                        │
│  3. 视频生成模型适配中枢 (VideoModelAdapter)                           │
│     - 统一接口：provider, model, prompt, image_url, duration, ratio  │
│     - 火山 Ark Seedance REST API 异步任务提交与轮询                    │
│     - 内置 Mock 发生器（生成带时间码的 5s 测试视频供离线契约验收）      │
│                                                                        │
│  4. 质量验收与修复引擎 (QAEngine & RepairEngine)                       │
│     - 10 项加权 QA 评分标准 (100分) + 7 项 HARD FAIL 强制拦截          │
│     - 人工标定 Failure Code 后映射 HAND001、PRO001 等 repair_action    │
│     - 核心能力：生成 Prompt V1.1 并【仅重跑失败镜头】，保留已PASS镜头  │
│                                                                        │
│  5. 视频缝合后处理服务 (StitcherService)                               │
│     - 基于 FFmpeg 将 PASS 的 S01 + S02 + S03 标准化编码为 15s 成片     │
│                                                                        │
│  6. 自动化契约验证套件 (verify_mvp.py / pytest)                         │
│     - 验证 Mock 接口、持久化、状态机、QA门禁、修复关系与媒体输出       │
│     - 真实 Round 1/2 与质量提升必须由业务素材和人工 QA 另行验收        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                 数据持久化与协作层 (事实源 + 条件镜像)                 │
│                                                                        │
│  1. SQLite：本地事务事实源；JSON 只作兼容镜像，断网或无Key不丢任务     │
│  2. 飞书 Bitable：仅在完整凭据、四表 ID 与实际写表成功时作为协作镜像   │
│     - 01_商品资料库 (档案、素材索引、卖点依据、可信度、合规审查结果)  │
│     - 02_分镜创作任务表 (创作层/生成层：S01/S02/S03、Prompt版本、URL)  │
│     - 03_质量验收与检查表 (检查层：QA总分、Failure Code、修复记录)     │
│     - 04_最终资产交付表 (交接层：15s成品视频、耗时/成本/通过率统计)    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、 核心模块技术落地方案

### 1. 商品档案、视觉分析与合规守卫（`core/product_analyzer.py`、`core/vision_analyzer.py`、`core/compliance.py`）
- **严格遵循文档 Section 4 & 5**：
  - 规则建档输入：`product_name`、`product_images`、`short_description`；真实识图先通过 `POST /api/assets/images` 上传，再把返回的 `asset_ids` 交给 `POST /api/products/analyze-vision`。
  - 输出结构：
    ```json
    {
      "product_name": "测试商品",
      "brand": "",
      "category": "",
      "appearance_description": "",
      "observed_information": ["视觉模型从像素中观察到的事实"],
      "packaging_claims": ["包装可见宣称，仍需人工核验"],
      "confirmed_information": ["用户明确提供或按证据策略确认的信息"],
      "possible_information": ["模型推测，不能作为硬卖点"],
      "usage_scenes": ["办公室桌面", "生活化场景"],
      "risk_information": [],
      "information_confidence": 0.75
    }
    ```
- **五大禁止行为拦截词库 (ComplianceGuard)**：
  - **检测数据类**：`通过.*检测`, `临床验证`, `有效率\d+%`, `有效率高达` 等；
  - **成分参数类**：未在已确认资料中的具体百分比或成分添加量；
  - **医疗功效类**：`治疗`, `根治`, `消炎`, `抗敏`, `药用`, `降血压`, `防脱发` 等；
  - **收益承诺类**：`稳赚`, `月入`, `暴富`, `零成本` 等；
  - **绝对化极限词**：`第一`, `顶级`, `全网最.*`, `100%`, `永久`, `首选`, `独家` 等。
  - 拦截行为：命中项写入 `risk_information` 并从安全卖点中剔除；可信度按风险类型扣减，医疗类直接限制到不可信区间，其他命中不保证一律低于 0.50。
  - 可信度低于 0.50 时仍可生成纯商品展示，但只允许外观、摆放和简单拿起/放回，禁止功效、参数、成分、检测结论和使用效果文案。

### 2. 11层 Prompt 编译系统 (`core/prompt_builder.py`)
- 严格内置 20 个标准积木模块库（`REAL_PERSON_001~003`, `SCENE_REAL_001~003`, `ACTION_001~005`, `CAMERA_001~003`, `LIGHT_001~002`, `PRODUCT_LOCK_001~002`, `NEGATIVE_001~002`）。
- 严格按顺序拼装：
  `1.镜头目标 -> 2.人物 -> 3.商品 -> 4.场景 -> 5.人物动作 -> 6.商品交互 -> 7.镜头 -> 8.光线 -> 9.人物真实感 -> 10.商品锁定 -> 11.负向约束`。
- 输出结构化 `PromptSchemaV1`，并对齐即梦模型（Seedance）的生动细节与负向词解析。
- Seedream 首帧从 11 层中抽取第 1、3、4、8、10 层作为静态依据；首帧的脸外/背影要求只是提示词尽力约束，当前没有生成后人脸检测。

### 3. 即梦 API 适配器与 Mock 引擎 (`core/adapter/jimeng.py`)
- 基于火山方舟 HTTP REST API 进行交互，不依赖网页自动化：
- 统一生成接口：
  `generate_video(product_id, shot_id, prompt, image_url, provider="jimeng", ...)`
- 任务状态流转：`CREATED -> SUBMITTED -> PROCESSING -> COMPLETED (或 FAILED)`。
- **双模设计**：
  - **真实 API 模式（Ark 已联调）**：已实现 Bearer 鉴权、Seedream 首帧、Seedance 创建/轮询/落盘、幂等指纹、真实识图/首帧/视频日限 20/6/12 和重启续查；轮询瞬时失败续接同一 Provider task ID，完整三镜仍需业务人工 QA。
  - GLM、Seedream、Seedance 提交状态不确定时保留原幂等键并进入人工复核，不自动递增 attempt；只有用户核对供应商控制台并确认新的可能计费请求后，才创建新尝试。
  - **Mock 模式**：无视频 Key 时生成带镜头编号、时间戳与提示词版本的 9:16 示例视频，用于验证工作流；Mock 结果不代表真实模型质量或真实 API 已接通。
  - 可能计费的识图、首帧、真实视频及修复端点当前仅限本机；云部署前必须增加用户认证和权限控制。
  - 可信真人/虚拟人 `asset://...` 输入及 Provider 参数映射尚未实现；固定可识别人脸仍是后续工作。

### 4. Failure Code 与单镜头修复引擎（`core/repair_engine.py`）
- 100 分制 QA 打分模型（商品20, 人物15, 动作15, 手部10, 遵循度10, 场景10, 镜头5, 稳定5, 准确5, 合规5）。
  - `>=85` PASS, `70~84` REPAIR, `<70` FAIL；7 项 HARD FAIL 强制拦截。
- 26 个普通 Failure Code 由人工 QA 标记，系统只负责把已记录代码映射为修复动作：
  - `HAND001` (手指畸形) -> 降低动作复杂度，降级为单手拿起；
  - `PRO001` (商品变形) -> 注入强商品锁定词汇；
  - `MOT002` (动作过快) -> 调整为慢速动作；
  - `CAM001` (乱运镜) -> 强制切换为固定机位；
  - `CMP001~003` (合规问题) -> 自动剔除违规宣传词。
- **单镜重跑机制**：
  - 人工完成 QA 并提供 Failure Code 后，编译新版本并只对该失败 `shot_id` 重新发起生成，保存 `parent_task_id` 和版本演进记录，不覆盖其他分镜。

### 5. 飞书多维表格（Bitable）数据回流 (`core/feishu_sync.py`)
- 对应《00_管理表》架构，封装 REST API：
  - SQLite 始终是本地事实源，`sync_outbox` 保存待同步记录；JSON 只作兼容镜像。
  - 只有 App ID、App Secret、Bitable App Token、商品/任务/QA/交付四张表 ID 全部配置，且鉴权与写表响应成功时，才可声明远端同步完成。
  - 获取到 `tenant_access_token` 只证明鉴权成功，不能单独证明四张表已经写入。

### 6. FFmpeg 3 镜头拼接与交付（`core/stitcher.py`）
- 读取同一商品、同一 execution mode 的 S01、S02、S03 三段本地视频，禁止 Mock/real 混拼；正式路径要求三条均为 `PASS`，Mock 绕过会留下 `MOCK_QA_BYPASS` 标记。
- 使用 FFmpeg 对三段素材分别执行 scale/crop/fps、补帧与裁切，使每段精确 5 秒，再 concat 为 720×1280、24fps、约 15 秒、H.264/yuv420p 成片；这不是“无损转码”。输出使用包含商品、时间戳和随机后缀的动态文件名，不承诺固定 `final_15s.mp4`。
- 无 TTS 时显式移除音轨；有 TTS 时只映射后期口播轨，并在交付前校验时长、画幅、帧率和音频策略。
- TTS 生产路径 fail-closed：Edge TTS、单段 5 秒归一化、15 秒音轨合并或视频混音失败都会直接终止，不生成静音占位或无声兜底交付。

### 6.1 剪映草稿交付（`core/jianying_exporter.py`）

- 仅接收同一商品、同一模式的 S01/S02/S03 三条归档视频；真实任务要求三条全部 `PASS`，Mock 只生成带 `MOCK_PREVIEW` 交付依据的预览草稿。
- 商品名在用于草稿目录和 ZIP 文件名之前会清洗并截断，结果路径必须保持在指定输出目录内。
- 本机剪映草稿目录存在且复制成功时才返回 `synced_to_local_jianying=true`；否则保留 ZIP 供手动导入。TTS 失败会终止导出。

### 7. 现代化单页时序工作台 Web UI (`static/`)
- 基于 FastAPI 托管的自包含高颜值控制台（无需 Node.js，拉下即用）：
  - 极客深色毛玻璃（Dark Glassmorphism）设计风格；
  - 实时显示飞书连接状态与 Mock/Live 模式开关；
  - 完整呈现 S01 / S02 / S03 时序分镜看板、Prompt 高亮展示、视频播放、QA 打标、单镜重跑按钮以及 15s 成片预览。
  - 主看板是三镜各一条的 3-shot 预览；Section 19 矩阵才是 9 条 Round 1。当前 Web 默认同时改变多个受控轴，单轴实验需通过 API 显式只选一个 `axes` 项。

### 8. 自动化验收脚本 (`verify_mvp.py`)
- 运行 pytest 契约套件，覆盖商品建档、Mock 生成、SQLite 恢复、QA 门禁、失败代码到修复版本的关系和 FFmpeg 文件输出。
- 脚本不会自动观看视频、不会自动完成 9 条真实 Round 1，也不会把模拟 QA 数据当作真实质量提升。真实验收必须由人工看片和 `/api/metrics?...&execution_mode=real` 中的实际记录完成；费用字段只是本地估算。

---

## 三、验收标准 8 项对应

| # | 验收标准项 | 模块与代码实现 | 对应产出物 |
|---|:---|:---|:---|
| 1 | 能输入商品 | `POST /api/assets/images` + `POST /api/products/analyze-vision` | 上传图片并生成结构化商品档案 |
| 2 | 能自动生成/读取结构化商品档案 | `core/product_analyzer.py` + `core/vision_analyzer.py` + `core/compliance.py` | 产出带观察/宣称/推测/局限、confirmed/possible 及可信度的 JSON |
| 3 | 能自动组装 Prompt | `core/prompt_builder.py` | 11 层顺序拼接，输出 PromptSchemaV1 |
| 4 | 能调用视频 API 并返回结果 | `core/adapter/jimeng.py` | 任务轮询与 MP4 视频结果回传 |
| 5 | 能记录 QA | `core/qa_engine.py` | 人工 100 分制评分写入 SQLite；飞书为条件镜像 |
| 6 | 能明确记录 Failure Code | `core/repair_engine.py` | 人工选择后结构化记录 26 个普通代码或 7 个 HARD FAIL |
| 7 | 能针对失败镜头生成 V1.1 并单独重跑 | `POST /api/video/tasks/{task_id}/retry` | 按实际 Failure Code 只重跑目标镜头并保留父子关系 |
| 8 | 第二轮质量或首次 PASS 率提升 | 真实 Round 1/2 + 人工 QA + `/api/metrics?execution_mode=real` | 尚未自动证明；必须在两轮真实样本完成后验收 |

---

## 四、 实施落地文件结构一览

```text
AI-SVWF/
├── main.py                    # FastAPI 应用入口与静态资源托管
├── verify_mvp.py              # pytest 契约验证入口
├── requirements.txt           # 基础 Python 依赖
├── .env.example               # 配置模板 (即梦Key、飞书Token等)
├── core/
│   ├── __init__.py
│   ├── schemas.py             # 严格对齐文档的 Pydantic 数据模型
│   ├── modules.py             # 20 个标准 Prompt 积木模块
│   ├── compliance.py          # 五大禁止项硬编码审查器
│   ├── ark_client.py          # GLM / Seedream / Seedance Ark HTTP 客户端
│   ├── asset_manager.py       # 商品图片校验、重编码、去重与归档
│   ├── product_analyzer.py    # 规则商品分析与可信度计算
│   ├── vision_analyzer.py     # GLM 视觉证据分层建档
│   ├── first_frame_service.py # Seedream 首帧生成、幂等与归档
│   ├── database.py            # SQLite 事实源、任务恢复与飞书 outbox
│   ├── prompt_builder.py      # 11 层 Prompt 组装编译器
│   ├── prompt_variant_planner.py # 有界受控 Prompt 变体
│   ├── repair_engine.py       # Failure Code 映射与 V1.1 生成
│   ├── qa_engine.py           # 100 分制 QA 与 HARD FAIL 校验
│   ├── stitcher.py            # FFmpeg 3×5s 标准化拼接与媒体校验
│   ├── tts_service.py         # 条件式 Edge TTS 与 3×5s 音轨对齐
│   ├── jianying_exporter.py   # 剪映草稿包及条件式本机写入
│   ├── feishu_sync.py         # 飞书多维表格 Bitable 同步桥
│   └── adapter/
│       ├── __init__.py
│       └── jimeng.py          # Seedance 异步任务适配器 + 本地 Mock
├── static/                    # 现代化 Web Studio 前端资产
│   ├── index.html             # 时序分镜看板页面
│   ├── app.js                 # 前端响应式交互与 API 调用
│   └── style.css              # 高质感 Dark Glassmorphism 样式
├── presets/                   # 预置商品图与分镜示例素材
├── tests/                     # API、幂等、配额、恢复与媒体契约测试
└── outputs/                   # 生成媒体和截图（被 Git 忽略）
```
