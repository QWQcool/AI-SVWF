# AI-SVWF (AI Short Video Workflow)

> **AI 带货视频工业化工作流与内容资产中心 (MVP V1.0)**  
> 专为 AIGC 电商带货短视频设计的工业级自动化生成、11层原子装配、因果质检与单镜修复工作流引擎。  
> 严格遵循并 100% 对齐《AI带货视频工作流_MVP技术交接文档_V1.0》规范标准。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Modern%20Async-green.svg)](https://fastapi.tiangolo.com/)
[![E2E Test](https://img.shields.io/badge/UI%20Test-Passing%20(9%2F9)-success.svg)](run_ui_test.py)

---

## 🎯 核心定位与业务价值

在传统 AI 带货短视频生产中，直接使用生硬的大模型提示词往往面临：**商品主体形变漂移、多手指肢体粘连、运镜晃动不可控、违规绝对化用词、返工成本高昂**等工业化痛点。

**AI-SVWF** 建立了一套标准化的“**输入基础素材 ➔ 结构化合规建档 ➔ 11层原子编译 ➔ 3×5s 时序生成 ➔ Failure Code 归因 ➔ V1.1 靶向单镜修复 ➔ 15s 成片混音交付**”端到端工业级闭环流水线。

---

## 🚀 核心架构：5 阶 Agent 时序拓扑

```text
[用户商品图文输入]
       │
       ▼
【01. 商品档案 Agent】──────> ComplianceGuard 合规拦截器 (剔除绝对化宣传词/计算可信度)
       │
       ▼
【02. Prompt 编译 Agent】────> 11 层原子化 Prompt 编译系统 (20个工业积木/高斯形态锁定)
       │
       ▼
【03. 即梦调度 Agent】──────> 3×5s 异步轮询调度 / 双模架构 (即梦 API / 本地高保真 Mock)
       │
       ▼
【04. QA 质检归因 Agent】────> 10 维工业质检 / Failure Code 归因 (HAND001 / PRO001 / MOT002)
       │                   ├── PASS ──> 进入成片拼接
       │                   └── FAIL ──> 🛠️ V1.1 因果动作降级与双重锁定 ──> 单镜头独立重跑
       ▼
【05. FFmpeg 成片 Agent】────> 15s 无缝混流 / Edge-TTS 自动化带货配音 / 剪映草稿工程导出
       │
       ▼
【内容资产回流中枢】────────> 飞书多维表格 (Bitable API) 双写同步 / 本地快照持久化
```

---

## 🌟 预处理版本核心功能亮点

### 1. ⚡ 结构化编译与合规风控 (ComplianceGuard)
- **事实与推测严格分离**：自动区分 `confirmed_information`（已确认事实）与 `possible_information`（合理推测），计算信息可信度 `information_confidence`；
- **极限词拦截**：严禁“第一/最好/100%/国家级/稳赚”等违规词，智能阻断虚假宣称，保障电商带货合规安全。

### 2. ⚙️ 分镜提示词工程独立工坊 (Prompt Studio)
- **宽屏双栏桌面级工作台**（`95vw × 92vh` 大屏空间）：
  - **左侧边栏 (360px)**：三维规避预设控制台 + 智能诊断建议 + Baseline 一键重置；
  - **右侧主区 (1fr 通顶)**：全高度 11 层工业提示词编辑器，采用 JetBrains Mono 等宽字体与舒适行高；
- **多镜头 Tab 无缝切换**：顶栏支持在 `[S01 场景建立]`、`[S02 拿起使用 ★]`、`[S03 平稳放回]` 之间自由切换连贯精调，底层状态物理隔离，各自分镜动作语义专属特化；
- **极简 Mini Chip 胶囊**：短小精炼按钮（`[复合动作]` / `[降级动作 ★]` / `[极简动作]` / `[双重锁定 ★]` / `[固定机位 ★]`），配备实时动态解析说明；
- **11 层工业规范 100% 绿色自检**：实时验证层级完整度，杜绝误报。

### 3. 🛠️ S01 / S02 / S03 全镜头因果修复重跑 (Section 19)
- **拒绝全片废弃**：仅针对失败镜头进行靶向归因修复，大幅节约 66.7% 的 GPU 算力成本；
- **因果降级逻辑**：
  - **S01**：消除镜头晃动（`CAM001`），切绝对固定中景，专注生活化工作状态；
  - **S02**：修复手部粘连与商品形变（`HAND001`/`PRO001`），动作降级为 `伸手 ➔ 拿起悬停5cm`，加固 `PRODUCT_LOCK_001+002` 双重几何物理锁；
  - **S03**：减缓放回抽搐速度（`MOT002`），强化平稳定格与品牌记忆点。

### 4. 📊 Section 18 & 19 优化矩阵与多版本 A/B 质检看板
- **并排双视口播放器**：同屏播放 `V1.0 (原版)` 与 `V1.1 (降级修复版)` 视频差异；
- **Prompt Diff 差异对比**：精准高亮动作层降级与锁定层强化的关键参数；
- **数据化跃升**：直观呈现缺陷率降为 0%，通过率从 33.3% 跃升至 100% 的工业化改善实证。

### 5. 🎙️ Edge-TTS 自动化带货配音与 15s 成片无缝混音
- 自动提取商品卖点生成节奏轻快、热情专业的带货旁白；
- 采用微软 Edge-TTS 神经网络男声/女声音色，无缝合成为 `3×5s = 15s` 高清带货成片；
- 支持剪映专业版（Jianying Pro）草稿工程目录导出。

### 6. ☁️ 飞书多维表格（本地 + 云端双写可配置）与算力成本测算
- 支持离线单机模式与云端 API 模式自由切换；
- 接口配置弹窗内置即梦 API、Seedance、飞书 Bitable 凭证配置；
- 支持根据调用模型动态测算每次生成的预估成本（填入行业标准默认值并持久化）。

---

## 💻 快速开始与启动指南

### 1. 克隆代码库
```bash
git clone https://github.com/QWQcool/AI-SVWF.git
cd AI-SVWF
```

### 2. 安装 Python 依赖
建议使用 Python 3.10 或更高版本虚拟环境：
```bash
pip install -r requirements.txt
```

### 3. 启动 Web 服务
```bash
python main.py
```
启动成功后，浏览器访问：**`http://localhost:8000`** 即可进入 AI-SVWF Web Studio。

---

## 🧪 自动化 UI 与视觉回归测试

项目内置了基于 Headless Edge + Selenium 的全自动端到端 UI 测试套件：

```bash
python run_ui_test.py
```

该脚本将自动模拟真实用户的完整生产操作链，并在 `outputs/` 目录下生成 9 张全流程视觉存证截图：
1. `test_step1_home.png`：首页加载与结构化编译按钮；
2. `test_step2_preset.png`：商品案例切换与 11 层提示词装配；
3. `test_step3_settings_modal.png`：接口配置与算力成本弹窗；
4. `test_step4_generated.png`：S01/S02/S03 三分镜并发异步生成视口；
5. `test_step5_repaired.png`：单镜头因果归因修复重跑完成；
6. `test_step6_stitched.png`：15 秒成片合成与播放弹窗；
7. `test_step7_matrix.png`：Section 19 轮次优化矩阵；
8. `test_step8_prompt_studio.png`：分镜提示词工程大屏独立工作台；
9. `test_step9_version_compare.png`：多版本 A/B 质检并排对比看板。

---

## 📁 项目核心目录结构

```text
AI-SVWF/
├── core/                                # 核心后端工业算法与适配引擎
│   ├── adapter/                         # 视频生成适配器 (JimengAdapter / MockAdapter)
│   ├── compliance.py                    # ComplianceGuard 合规风控与信息可信度评估
│   ├── prompt_builder.py                # 11 层工业标准提示词原子化编译器
│   ├── repair_engine.py                 # Failure Code 驱动的因果修复与动作降级引擎
│   ├── qa_engine.py                     # 10 维工业质检与打分引擎
│   ├── stitcher.py                      # FFmpeg 视频多轨道对齐与无缝拼接
│   ├── tts_service.py                   # Edge-TTS 神经网络旁白合成引擎
│   ├── jianying_exporter.py             # 剪映专业版 Draft 结构导出器
│   ├── feishu_sync.py                   # 飞书多维表格 Bitable 双写同步
│   ├── product_analyzer.py              # 商品图文多模态特征结构化建档
│   └── schemas.py                       # 工业数据规范与 Pydantic 模型
├── static/                              # Web Studio 前端设计系统
│   ├── index.html                       # Studio 骨架与工作台弹窗
│   ├── style.css                        # 宽屏双栏玻璃拟物化样式系统
│   └── app.js                          # 前端状态机、异步轮询与 Tab 物理隔离控制器
├── outputs/                             # 自动化生成成片、分镜缓存与测试截图
├── main.py                              # FastAPI 异步服务主入口
├── run_ui_test.py                       # E2E 自动化测试与视觉存证工具
├── TRIAL_HANDOVER_GUIDE.md              # 试岗与交接实操演示标准化指南 (SOP)
├── AI带货视频工作流_MVP技术交接文档_V1.0.md # 工业规范技术交接原始文档
└── README.md                            # 项目说明文档
```

---

## 🤝 明日交接实操指引 (Company Handover Quickstart)

明天在公司电脑打开项目后，可通过以下三步快速恢复工作上下文：

1. **拉取最新代码**：
   ```bash
   git pull origin main
   ```
2. **启动本地开发服务**：
   ```bash
   python main.py
   ```
3. **查阅交接指南**：
   - 阅读项目根目录下的 **`TRIAL_HANDOVER_GUIDE.md`**，内含面向领导/同事演示的完整 6 步 SOP 话术；
   - 让公司电脑上的 AI 编程工具直接读取 `TRIAL_HANDOVER_GUIDE.md` 和 `AI带货视频工作流_MVP技术交接文档_V1.0.md` 即可无缝接力后续工作。

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 协议开源。
