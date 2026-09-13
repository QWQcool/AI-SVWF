# AI-SVWF 落地实施规划与架构蓝图 (MVP V1.0)

> 目标：严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》与《AIGC内容资产中心实现目标.txt》，在今晚完成核心闭环落地，使项目随时可在本地及云服务器一键部署，明天下班前达成 8 项硬性验收标准。

---

## 一、 系统架构与分层设计

```text
┌────────────────────────────────────────────────────────────────────────┐
│               前端展示层 (Modern Storyboard Pipeline Studio)           │
│  - 商品信息输入与分析面板       - S01/S02/S03 三镜头时序看板            │
│  - 11层Prompt实时装配预览       - 单镜头即梦生成进度与播放器           │
│  - QA质量评分与Failure Code标定 - 针对失败镜头一键Repair与V1.1重跑     │
│  - 15秒成品视频缝合预览与下载   - 飞书Bitable同步状态指示灯            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / WebSocket / REST
┌───────────────────────────────────▼────────────────────────────────────┐
│                  核心引擎层 (FastAPI / Python 3.10+)                   │
│                                                                        │
│  1. 商品识别与合规守卫 (ProductAnalyzer & ComplianceGuard)            │
│     - 提取商品基础特征，计算 information_confidence 可信度分数        │
│     - 严格区分 confirmed_information 与 possible_information          │
│     - 强制五大禁止项拦截（未提供检测、参数、医疗、收益、绝对化用语）    │
│                                                                        │
│  2. 11层Prompt自动化装配器 (PromptBuilder)                             │
│     - 预置 20 个标准积木模块（人物肤质、自然动作、商品锁定、负向约束） │
│     - 严格遵循 11 步流水线组装逻辑，输出结构化 Schema V1.0            │
│                                                                        │
│  3. 视频生成模型适配中枢 (VideoModelAdapter)                           │
│     - 统一接口：provider, model, prompt, image_url, duration, ratio  │
│     - 字节即梦 (Jimeng / Seaweed) 异步任务提交 + 轮询状态机           │
│     - 高保真 Mock 发生器（无 Key 或网络异常时秒级切换兜底）          │
│                                                                        │
│  4. 质量验收与修复引擎 (QAEngine & RepairEngine)                       │
│     - 10 项加权 QA 评分标准 (100分) + 7 项 HARD FAIL 强制拦截          │
│     - Failure Code 映射自动触发 repair_action                         │
│     - 生成 Prompt V1.1 并支持【单独重跑失败镜头】机制                  │
│                                                                        │
│  5. 视频合成后处理服务 (StitcherService)                               │
│     - 基于 FFmpeg 将 S01 + S02 + S03 (3×5s) 拼接为 15s 带货成品 MP4   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                    数据持久化与协作层 (双模双写)                       │
│  1. 本地轻量存储 (Local JSON/SQLite)：保证无网络波动时系统高可用       │
│  2. 飞书多维表格 (Feishu Bitable API)：连接《00_管理表》全资产回流     │
│     - 商品资料表 / 视频任务表 / 提示词版本表 / QA验收记录表            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、 模块落地明细与规范要求

### 模块 1：商品分析与合规守卫 (`core/compliance.py` & `core/product.py`)
- **严格遵循文档 Section 4 & 5**：
  - 输入：`product_name`, `product_images`, 可选描述。
  - 输出结构：
    - `confirmed_information`：只包含用户明确提供或图文识别的高确定性信息。
    - `possible_information`：AI逻辑推测，严禁直接作为宣传事实。
    - `information_confidence`：
      - `>=0.90`：高可信，全流程生成；
      - `0.70~0.89`：避免强事实性宣传；
      - `0.50~0.69`：保守生成，仅展示与场景使用；
      - `<0.50`：仅商品展示，禁生成功效文案。
- **五大禁止行为硬编码拦截器**：
  - 严禁未提供检测数据（如“通过SGS检测”、“有效率99%”等无源数据）。
  - 严禁未提供成分参数（如“添加50%玻色因”等无依据数值）。
  - 严禁医疗功效表达（“治疗”、“消炎”、“抗敏”、“根除”等）。
  - 严禁收益承诺（“月入过万”、“稳赚”等）。
  - 严禁未经证明的绝对化极限词（“第一”、“全网最好”、“顶级”、“100%”等）。
  - 触发违规直接标记 `risk_information` 并触发降级或 `CMP001~003` 警报。

### 模块 2：11层 Prompt 编译系统 (`core/prompt_builder.py`)
- **严格遵循文档 Section 7、8、9**：
  - 20 个标准积木模块库（`REAL_PERSON_001~003`, `SCENE_REAL_001~003`, `ACTION_001~005`, `CAMERA_001~003`, `LIGHT_001~002`, `PRODUCT_LOCK_001~002`, `NEGATIVE_001~002`）。
  - 11 层确定性组装顺序：
    `1.镜头目标 -> 2.人物 -> 3.商品 -> 4.场景 -> 5.人物动作 -> 6.商品交互 -> 7.镜头 -> 8.光线 -> 9.人物真实感 -> 10.商品锁定 -> 11.负向约束`。
  - 支持 S01（场景建立）、S02（单手拿起）、S03（记忆放回）三大分镜的模块编译。
  - 输出完整且类型完备的 `PromptSchemaV1` JSON。

### 模块 3：即梦 API 适配器与 Mock 引擎 (`core/adapter/jimeng.py`)
- **严格遵循文档 Section 11 & 24**：
  - 统一调用签名：
    ```python
    async def generate_video(
        product_id: str,
        shot_id: str,
        prompt: str,
        image_url: str,
        provider: str = "jimeng",
        model: str = "jimeng-v2",
        prompt_version: str = "1.0",
        duration: int = 5,
        aspect_ratio: str = "9:16",
    ) -> VideoTaskResult
    ```
  - 状态流转状态机：
    `CREATED -> SUBMITTED -> PROCESSING -> COMPLETED (或 FAILED)`。
  - 状态查询接口与指数退避轮询调度（Poll Poller）。
  - **Mock 引擎高保真兜底**：若未配置密钥或开启 MOCK 模式，自动生成/调用分镜示例视频，返回模拟 provider_task_id，保证联调与演示 100% 畅通。

### 模块 4：QA 评分与 Failure Code 驱动的修复引擎 (`core/repair.py`)
- **严格遵循文档 Section 14、15、16**：
  - QA 评分规则：100 分制（商品一致性20, 人物真实性15, 动作自然度15, 手部肢体10, 遵循度10, 场景10, 运镜5, 稳定性5, 准确度5, 合规5）。
    - `>=85`：PASS
    - `70~84`：REPAIR
    - `<70`：FAIL
  - 7 项 HARD FAIL 强制阻断（`HARD_FAIL_01~07`）。
  - Failure Code 映射库：
    - `HAND001` (手指畸形) -> `reduce_action_complexity`
    - `PRO001` (商品变形) -> `strengthen_product_lock`
    - `MOT002` (动作过快) -> `slow_action`
    - `CAM001` (运镜过大) -> `switch_fixed_camera`
    - `CMP001` (无依据卖点) -> `remove_unverified_claim`
  - 触发 Repair：根据 Failure Code 调整 Prompt 模块装配规则，版本自增为 `V1.1`，**并仅重跑指定 shot_id**。

### 模块 5：飞书多维表格（Bitable）数据同步中枢 (`core/feishu_sync.py`)
- **严格遵循《AIGC内容资产中心实现目标.txt》与图片结构**：
  - 对应《00_管理表》：
    - `01_商品资料库`：保存商品档案、识别结论、合规标签；
    - `视频创作任务库`：保存 task_id、shot_id、提示词版本、生成视频 URL、耗时、成本；
    - `检查层 (QA记录表)`：保存 QA 总分、分项打分、Failure Code 列表、修复建议。
  - 双模设计：本地 JSON/SQLite 自动备份持久化，配置了 Feishu AppToken 后自动异步双写。

### 模块 6：FFmpeg 3 镜头拼接器 (`core/stitcher.py`)
- 输入：S01.mp4, S02.mp4, S03.mp4。
- 检查分辨率与帧率一致性，使用 FFmpeg concat demuxer 无损直拼为 15 秒 9:16 成片。

### 模块 7：现代化分镜时序工作台 Web UI (`static/index.html`, `static/app.js`, `static/style.css`)
- 单页现代化控制台（Dark Glassmorphism 极客科技风）：
  - 顶部：全局状态监控栏（飞书连通性指示灯、即梦 API/Mock 模式切换器、生成统计数据）；
  - 左侧：商品输入与结构化档案区（图片上传、卖点与合规词提取、可信度评分仪表盘）；
  - 中部核心：**S01/S02/S03 三分镜时序看板**：
    - 实时 Prompt 预览与 11 层积木高亮；
    - 生成倒计时与状态流转；
    - 5 秒分镜播放器；
    - **【单镜重跑 V1.1】操作入口**；
  - 底部：QA 打分与 Failure Code 标定弹窗，以及最终 15 秒无缝拼接成品视频播放与导出。

---

## 三、 明日验收标准 8 项刚性核对表

| # | 验收标准项 | 对应实现模块 | 预期输出 |
|---|:---|:---|:---|
| 1 | 能输入商品 | `POST /api/products/input` | 接收图片与名称，回显预览 |
| 2 | 能自动生成/读取结构化商品档案 | `core/product.py` + `compliance.py` | 产出包含 confirmed/possible 与可信度的 JSON |
| 3 | 能自动组装 Prompt | `core/prompt_builder.py` | 11 层顺序拼接，输出符合 Schema V1.0 的 Prompt |
| 4 | 能调用视频 API 并返回结果 | `core/adapter/jimeng.py` | 提交任务、返回 task_id、轮询获取 MP4 |
| 5 | 能记录 QA | `core/qa.py` | 100 分制加权计算并回写 |
| 6 | 能明确记录 Failure Code | `core/repair.py` | 记录具体故障码（如 HAND001, PRO001） |
| 7 | 能针对失败镜头生成 V1.1 并单独重跑 | `POST /api/video/repair` | 仅重新生成 S02_V1.1，保留 S01/S03 |
| 8 | 第二轮质量或首次 PASS 率提升 | 统计比对模块 | 输出 V1.0 vs V1.1 通过率与平均 QA 改善对比 |

---

## 四、 实施计划与里程碑 (今晚落地节奏)

- [x] **Milestone 0: 仓库初始化与云端同步** (已完成 GitHub `QWQcool/AI-SVWF` 创建与首推)
- [ ] **Milestone 1: 核心领域模型与 Prompt 编译器构建** (`core/schemas.py`, `core/modules.py`, `core/prompt_builder.py`)
- [ ] **Milestone 2: 合规风控与商品档案分析器** (`core/compliance.py`, `core/product_analyzer.py`)
- [ ] **Milestone 3: 即梦 API 适配器与 Mock 发生器** (`core/adapter/jimeng.py`)
- [ ] **Milestone 4: Failure Code 归因与单镜修复引擎** (`core/repair_engine.py`)
- [ ] **Milestone 5: 飞书多维表格 Bitable 同步桥与本地数据层** (`core/feishu_sync.py`, `core/database.py`)
- [ ] **Milestone 6: FFmpeg 视频拼接合成服务** (`core/stitcher.py`)
- [ ] **Milestone 7: FastAPI 路由收口与端到端接口暴露** (`main.py`, `api/`)
- [ ] **Milestone 8: 现代化分镜时序工作台 (Web Studio) 搭建** (`static/`)
- [ ] **Milestone 9: 端到端闭环自测与试岗演示交接文档编制** (`TEST_WALKTHROUGH.md`)
