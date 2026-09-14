# AI-SVWF 落地实施规划与架构蓝图 (MVP V1.0 工业化参考升级版)

> **状态校正（2026-09-14）**：本文是初始实施蓝图，不等同于已验收清单。当前可验证实现与真实接口边界以 `INTERFACE_CONTRACT.md`、自动化测试和实际 Provider 联调结果为准。Seedance/即梦/可灵在获得真实接口资料与 Key 前不得表述为“已经接通”。

> 核心依据：
> 1. 《AI带货视频工作流_MVP技术交接文档_V1.0.md》(2073行标准规范)
> 2. 《AIGC内容资产中心实现目标.txt》
> 3. 《AIGC内容资产中心.jpg》(飞书多维表格 00_管理表资产中心架构)
> 4. **GitHub 顶级开源最佳实践吸收**：
>    - **`dramaclaw/dramaclaw`** (2026顶级活跃 AIGC 视频引擎，原生适配即梦/Seedance 2.0，单进程安全队列)
>    - **`harry0703/MoneyPrinterTurbo`** (15k+ Stars 视频自动化工业级架构，FFmpeg 精准拼接)
>    - **`xixihhhh/clipforge`** (电商商品锁定与无损卖点提炼)
>    - **`liangdabiao/make-prompt-seedance2`** (即梦 Seedance 结构化提示词配方标准)

---

## 一、 系统定位与架构全景

AI-SVWF 是一个面向 AIGC 内容创作者和电商视频自动化的**工业级轻量工作流引擎**，核心定位是：
**“下接模型算力（即梦/Jimeng REST API），上接协同中枢（飞书多维表格 Bitable），内聚分镜装配、合规阻断与精准单镜修复”**。

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
│  3. 视频生成模型适配中枢 (VideoModelAdapter - 吸收 DramaClaw 经验)    │
│     - 统一接口：provider, model, prompt, image_url, duration, ratio  │
│     - 字节即梦 (Jimeng / Seedance) 官方企业级 REST API 异步调度        │
│     - 内置高保真 Mock 发生器（动态生成带时间码的 5s 测试视频兜底）     │
│                                                                        │
│  4. 质量验收与修复引擎 (QAEngine & RepairEngine)                       │
│     - 10 项加权 QA 评分标准 (100分) + 7 项 HARD FAIL 强制拦截          │
│     - Failure Code (HAND001, PRO001等) 映射自动触发 repair_action      │
│     - 核心能力：生成 Prompt V1.1 并【仅重跑失败镜头】，保留已PASS镜头  │
│                                                                        │
│  5. 视频缝合后处理服务 (StitcherService - 吸收 MoneyPrinter 经验)      │
│     - 基于 FFmpeg 将通过验收的 S01 + S02 + S03 无损拼为 15s 带货成品   │
│                                                                        │
│  6. 自动化验收检验套件 (VerificationRunner)                            │
│     - 一键跑通 Round 1 (9条视频) -> QA打标 -> 单镜重跑 -> 15s成片     │
│     - 自动统计耗时、成本与两轮通过率对比，直通试岗验收                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                    数据持久化与协作层 (双模双写)                       │
│                                                                        │
│  1. 本地轻量存储 (Local SQLite/JSON)：本地状态快照，断网或无Key不崩   │
│  2. 飞书多维表格 (Feishu Bitable API)：对应《AIGC内容资产中心.jpg》    │
│     - 01_商品资料库 (档案、素材索引、卖点依据、可信度、合规审查结果)  │
│     - 02_分镜创作任务表 (创作层/生成层：S01/S02/S03、Prompt版本、URL)  │
│     - 03_质量验收与检查表 (检查层：QA总分、Failure Code、修复记录)     │
│     - 04_最终资产交付表 (交接层：15s成品视频、耗时/成本/通过率统计)    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、 核心模块技术落地方案

### 1. 商品档案与合规守卫 (`core/compliance.py` & `core/product.py`)
- **严格遵循文档 Section 4 & 5**：
  - 输入：`product_name`, `product_images`, `short_description` (可选)。
  - 输出结构：
    ```json
    {
      "product_name": "测试商品",
      "brand": "",
      "category": "",
      "appearance_description": "",
      "confirmed_information": ["从商品图或明确输入的属性"],
      "possible_information": ["AI合理推测，不能作为硬卖点"],
      "usage_scenes": ["办公室桌面", "生活化场景"],
      "risk_information": [],
      "information_confidence": 0.95
    }
    ```
- **五大禁止行为拦截词库 (ComplianceGuard)**：
  - **检测数据类**：`通过.*检测`, `临床验证`, `有效率\d+%`, `有效率高达` 等；
  - **成分参数类**：未在已确认资料中的具体百分比或成分添加量；
  - **医疗功效类**：`治疗`, `根治`, `消炎`, `抗敏`, `药用`, `降血压`, `防脱发` 等；
  - **收益承诺类**：`稳赚`, `月入`, `暴富`, `零成本` 等；
  - **绝对化极限词**：`第一`, `顶级`, `全网最.*`, `100%`, `永久`, `首选`, `独家` 等。
  - 拦截行为：命中立即计入 `risk_information`，剔除出卖点，可信度降级至 `<0.50`，强制转入普通纯展示模板。

### 2. 11层 Prompt 编译系统 (`core/prompt_builder.py`)
- 严格内置 20 个标准积木模块库（`REAL_PERSON_001~003`, `SCENE_REAL_001~003`, `ACTION_001~005`, `CAMERA_001~003`, `LIGHT_001~002`, `PRODUCT_LOCK_001~002`, `NEGATIVE_001~002`）。
- 严格按顺序拼装：
  `1.镜头目标 -> 2.人物 -> 3.商品 -> 4.场景 -> 5.人物动作 -> 6.商品交互 -> 7.镜头 -> 8.光线 -> 9.人物真实感 -> 10.商品锁定 -> 11.负向约束`。
- 输出结构化 `PromptSchemaV1`，并对齐即梦模型（Seedance）的生动细节与负向词解析。

### 3. 即梦 API 适配器与 Mock 引擎 (`core/adapter/jimeng.py`)
- 吸收 `dramaclaw` 适配器设计思想，基于 HTTP REST API 进行企业级交互（不搞脆弱的网页浏览器自动化）：
- 统一生成接口：
  `generate_video(product_id, shot_id, prompt, image_url, provider="jimeng", ...)`
- 任务状态流转：`CREATED -> SUBMITTED -> PROCESSING -> COMPLETED (或 FAILED)`。
- **双模设计**：
  - **真实 API 模式（待联调）**：统一请求、任务 ID、轮询、结果落盘和失败状态已经定义；供应商鉴权与字段映射必须在拿到官方资料和真实 Key 后完成并验收。
  - **Mock 模式**：无视频 Key 时生成带镜头编号、时间戳与提示词版本的 9:16 示例视频，用于验证工作流；Mock 结果不代表真实模型质量或真实 API 已接通。

### 4. Failure Code 与单镜头修复引擎 (`core/repair.py`)
- 100 分制 QA 打分模型（商品20, 人物15, 动作15, 手部10, 遵循度10, 场景10, 镜头5, 稳定5, 准确5, 合规5）。
  - `>=85` PASS, `70~84` REPAIR, `<70` FAIL；7 项 HARD FAIL 强制拦截。
- Failure Code 映射自动修复：
  - `HAND001` (手指畸形) -> 降低动作复杂度，降级为单手拿起；
  - `PRO001` (商品变形) -> 注入强商品锁定词汇；
  - `MOT002` (动作过快) -> 调整为慢速动作；
  - `CAM001` (乱运镜) -> 强制切换为固定机位；
  - `CMP001~003` (合规问题) -> 自动剔除违规宣传词。
- **单镜重跑机制**：
  - 自动编译生成 `S02_V1.1`，仅对 `shot_id="S02"` 重新发起生成，**绝对不重新调用 S01 和 S03**，保存版本演进记录。

### 5. 飞书多维表格（Bitable）数据回流 (`core/feishu_sync.py`)
- 对应《00_管理表》架构，封装 REST API：
  - 支持免 SDK 原生 HTTPS 请求（更轻量稳定），自动换取 `tenant_access_token`；
  - 支持向 Bitable 写入商品资料、分镜生成任务记录、QA 质检打分与 Failure Code；
  - 双模：本地 JSON/SQLite 实时自动镜像备份，断网离线也不丢数据。

### 6. FFmpeg 3 镜头拼接与交付 (`core/stitcher.py`)
- 吸收 `MoneyPrinterTurbo` 拼接经验，自动读取 S01、S02、S03 三段 5 秒视频；
- 利用 FFmpeg concat 协议进行无损转码拼接，输出标准 15 秒 9:16 带货视频成片 `final_15s.mp4`。

### 7. 现代化单页时序工作台 Web UI (`static/`)
- 基于 FastAPI 托管的自包含高颜值控制台（无需 Node.js，拉下即用）：
  - 极客深色毛玻璃（Dark Glassmorphism）设计风格；
  - 实时显示飞书连接状态与 Mock/Live 模式开关；
  - 完整呈现 S01 / S02 / S03 时序分镜看板、Prompt 高亮展示、视频播放、QA 打标、单镜重跑按钮以及 15s 成片预览。

### 8. 自动化验收脚本 (`verify_mvp.py`)
- 一键自动化执行明日交接文档的 8 项验收标准：
  - 自动导入测试商品 -> 提取建档 -> 组装 Prompt -> 触发 3 镜生成 -> 执行 QA 判定 -> 针对 S02 模拟 HAND001 并触发 V1.1 重跑 -> 拼接 15 秒视频 -> 输出耗时与 PASS 率对比总结！

---

## 三、 明日验收标准 8 项刚性对应

| # | 验收标准项 | 模块与代码实现 | 对应产出物 |
|---|:---|:---|:---|
| 1 | 能输入商品 | `POST /api/products/input` | 接收图片与商品名称 |
| 2 | 能自动生成/读取结构化商品档案 | `core/product_analyzer.py` + `compliance.py` | 产出带 confirmed/possible 及可信度的 JSON |
| 3 | 能自动组装 Prompt | `core/prompt_builder.py` | 11 层顺序拼接，输出 PromptSchemaV1 |
| 4 | 能调用视频 API 并返回结果 | `core/adapter/jimeng.py` | 任务轮询与 MP4 视频结果回传 |
| 5 | 能记录 QA | `core/qa_engine.py` | 100 分制加权评分回写本地与飞书 |
| 6 | 能明确记录 Failure Code | `core/repair_engine.py` | 结构化记录 HAND001、PRO001 等故障码 |
| 7 | 能针对失败镜头生成 V1.1 并单独重跑 | `POST /api/video/repair` | 仅重新生成 S02_V1.1，保留 S01/S03 |
| 8 | 第二轮质量或首次 PASS 率提升 | `verify_mvp.py` 统计比对 | 产出 V1.0 vs V1.1 对比分析表 |

---

## 四、 实施落地文件结构一览

```text
AI-SVWF/
├── main.py                    # FastAPI 应用入口与静态资源托管
├── verify_mvp.py              # 一键自动化验收 8 项指标脚本
├── requirements.txt           # 基础 Python 依赖
├── .env.example               # 配置模板 (即梦Key、飞书Token等)
├── core/
│   ├── __init__.py
│   ├── schemas.py             # 严格对齐文档的 Pydantic 数据模型
│   ├── modules.py             # 20 个标准 Prompt 积木模块
│   ├── compliance.py          # 五大禁止项硬编码审查器
│   ├── product_analyzer.py    # 商品分析与可信度计算
│   ├── prompt_builder.py      # 11 层 Prompt 组装编译器
│   ├── repair_engine.py       # Failure Code 映射与 V1.1 生成
│   ├── qa_engine.py           # 100 分制 QA 与 HARD FAIL 校验
│   ├── stitcher.py            # FFmpeg 3×5s 视频无缝拼接
│   ├── feishu_sync.py         # 飞书多维表格 Bitable 同步桥
│   └── adapter/
│       ├── __init__.py
│       └── jimeng.py          # 即梦 API 适配器 + 本地高保真 Mock
├── static/                    # 现代化 Web Studio 前端资产
│   ├── index.html             # 时序分镜看板页面
│   ├── app.js                 # 前端响应式交互与 API 调用
│   └── style.css              # 高质感 Dark Glassmorphism 样式
├── presets/                   # 预置商品图与分镜示例素材
└── outputs/                   # 生成的分镜视频与 15s 成片存储目录
```
