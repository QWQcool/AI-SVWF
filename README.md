# AI-SVWF (AI Short Video Workflow)

> **AI 带货视频工业化工作流与内容资产中心 (MVP V1.0)**  
> 专为 AIGC 内容创作者与电商带货场景设计的 AI 视频自动化生成与质检修复工作流引擎。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Modern%20Async-green.svg)](https://fastapi.tiangolo.com/)

---

## 🎯 核心目标与特性

AI-SVWF 致力于实现“用户仅上传基础商品素材，即可自动生成合规、可用的带货短视频”的端到端最小闭环：

1. **结构化商品建档与合规风控 (Compliance Guard)**
   - 自动提取商品图文核心特征，明确区分 `confirmed_information`（已确认事实）与 `possible_information`（可能推测）。
   - 计算信息可信度 `information_confidence`。
   - **严格合规守卫**：严禁自动生成未提供检测数据、成分参数、医疗功效、收益承诺及“第一/最好/100%”等绝对化宣传词。

2. **11层原子化 Prompt 编译系统 (Prompt Builder)**
   - 预置 20 个标准化 Prompt 积木模块（人物肤质、微动作、自然场景、单手抓握、物理稳定性、负向约束等）。
   - 杜绝大模型随机幻觉，按标准序列确定性编译 S01/S02/S03 分镜。

3. **分镜化时序生产 (3 × 5s → 15s 成片)**
   - **S01 (0~5s)**：真实场景建立（自然生活/办公环境，商品首帧锁定）。
   - **S02 (5~10s)**：单手抓握与简单使用（严格商品物理稳定性）。
   - **S03 (10~15s)**：自然放回与品牌记忆点。
   - 自动调用底层渲染管线将通过验收的 3 个分镜无损缝合为 15 秒带货成片。

4. **Failure Code 驱动的精准单镜修复 (Repair Engine)**
   - 针对失败分镜精准归因（如 `HAND001` 手指粘连、`PRO001` 商品形变）。
   - 自动触发针对性修复动作生成 `V1.1 Prompt`，**仅重跑失败镜头**，极大节约 GPU 算力与生成时间。

5. **双模存储与模型适配 (Dual-Mode)**
   - **存储中枢**：飞书多维表格（Bitable API）远程同步 + 本地轻量持久化双保险。
   - **视频生成适配层**：字节跳动即梦（Jimeng）API 异步轮询调度 + 本地高保真 Mock 引擎无缝切换。

---

## 🛠️ 技术架构

```text
[用户商品输入] ──> [商品结构化分析 & 合规拦截 (ComplianceGuard)]
                           │
                           ▼
               [11层 Prompt 编译器 (PromptBuilder)]
                           │
                           ▼
          [即梦 API 调度 / Mock 引擎 (JimengAdapter)]
                           │
                           ▼
            [分镜 QA 质检 & Failure Code 归因]
             ├── PASS ──> [FFmpeg 3×5s 缝合 15s 成片]
             └── FAIL ──> [自动生成 V1.1 Prompt ──> 单镜重跑]
                           │
                           ▼
           [飞书多维表格 (Bitable) 数据资产回流]
```

---

## 📄 开源协议

本项目采用 [MIT 协议](LICENSE) 开源。
