# AI-SVWF 试岗实战操作与技术交接手册（MVP V1.0）

> **状态校正（2026-09-14）**：演示结论必须来自实际接口响应、SQLite 记录、人工 QA 和测试断言。火山 Ark 的 GLM 识图、Seedream 首帧和 Seedance 单镜视频链路已真实跑通；`QA_PENDING` 或一次 API 成功不等于内容验收通过，也不能用预置分数宣称修复有效。

## 一、快速启动与演示方案

### 方式 A：Web Studio 交互演示

1. 双击项目根目录的 `start.bat`。脚本会等待服务就绪，再打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。
2. 需要手动启动时执行：

   ```powershell
   .\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
   ```

3. 按下列顺序演示。

#### Step 1：商品上传、识图与合规建档

- 在左侧上传 1～6 张同一商品的多角度图片。服务端会校验真实图片、重新编码以移除 EXIF/GPS 等元数据，并按内容哈希去重。
- Mock 模式不会为了“演示识图”暗中调用付费模型，应手工填写商品名称；切换真实模式并确认费用后，才调用 GLM 生成商品名称、可见卖点和证据分层档案。
- 展示 `observed_information`、`packaging_claims`、`model_inferences`、`vision_notes` 的分离，以及 `confirmed_information` 与 `possible_information` 不互相冒充。
- 可用“违规案例测试”展示 ComplianceGuard；低于 0.50 的商品仍可进入纯外观展示流程，但禁止功效、参数、成分、检测结论和使用效果文案。

#### Step 2：11 层 Prompt 编译

- 点击 S01/S02/S03 卡片的“查看 11 层装配提示词”，依次检查镜头目标、人物、商品、场景、动作、交互、镜头、光线、真实感、商品锁定和负向约束。
- 首帧会复用其中第 1、3、4、8、10 层作为静态依据，不是另行自由生成一套无关 Prompt。
- 未接入可信人像时，首帧 Prompt 会要求背影或脸外构图。这只是尽力降低隐私拒绝概率；当前没有生成后人脸检测，不能宣称一定无脸或一定通过供应商审核。

#### Step 3：区分三镜预览与 Round 1

- 主看板右上角“**一键生成三分镜预览（3 条）**”只生成 S01/S02/S03 各一条，用于快速检查完整叙事，不是 Round 1。
- “**优化对比矩阵（Section 19）**”中的“运行 Round 1 基准测试（9 条）”才会生成 S01×3、S02×3、S03×3。真实模式会产生明显费用，且要求三张首帧已经生成。
- 当前 Web 的 9 条默认是多轴受控变体；它适合覆盖探索，但不能证明某一个设计轴导致质量提升。单轴实验需通过变体 API 只选择一个 `axes` 项。
- 生成完成后状态是 `QA_PENDING`；先看片，再得出质量结论。
- 若 GLM、Seedream 或 Seedance 返回“不确定是否已提交/计费”，不要再次点击制造新键。系统会保留原幂等尝试并要求人工复核；先到火山控制台确认，再在明确的二次费用确认框中决定是否创建新尝试。

#### Step 4：人工 QA 与单镜修复

- 先点击目标分镜的“QA 评分”，由人工观看视频、填写 10 个评分维度并勾选实际 Failure Code；系统不会自动识别 `HAND001`、`PRO001` 等缺陷。
- 26 个普通 Failure Code 和 7 个 HARD FAIL 由人工标记并写入 SQLite。没有 Failure Code 时，修复请求会被拒绝。
- 只有完成 QA 后，才点击 S02 的“V1.1 修复重跑”。系统按已记录代码生成修复 Prompt、创建带 `parent_task_id` 的新任务，并只重跑该失败镜头；S01/S03 不被覆盖。
- V1.1 仍需再次人工 QA。只有 Round 1/2 都有真实评分，才能比较通过率。

#### Step 5：15 秒拼接与可选 TTS

- 正式路径只允许三条 `PASS` 视频进入拼接；Mock 预览可显式绕过，并在交付记录标记 `MOCK_QA_BYPASS`。
- 三条输入必须来自同一 execution mode，Mock 和真实任务不能混拼。FFmpeg 会先把每一段分别补帧/裁切为精确 5 秒，再按 S01/S02/S03 拼成 15 秒，避免供应商片段时长偏差导致口播错位。
- 未勾选 TTS 时，最终视频显式移除非批准音轨。勾选 TTS 时采用 fail-closed：Edge TTS、三段音频归一化、15 秒合并或最终混音任一步失败，接口直接报错，不会生成静音占位成片。成功后仍应实际试听。
- 最终文件名由系统生成，并对 720×1280、约 15 秒、24fps 和音轨策略做校验，不应预设固定文件名。

#### Step 6：条件式剪映草稿交付

- “导出剪映电脑版草稿”要求同一商品、同一模式以及 S01/S02/S03 各一条已归档本地视频。真实任务必须三镜全部 `PASS`；Mock 草稿只可标为预览，不能当作真实交付。
- 商品名会先清洗、截断后用于草稿目录与 ZIP 文件名，防止非法字符或目录越界。只有系统检测到本机剪映草稿目录且实际写入成功，接口返回 `synced_to_local_jianying=true` 时，才可声明“已直写本机剪映”；否则只交付 ZIP 手动导入。
- 剪映导出也依赖真实 TTS，口播失败会终止导出，不会用静音轨完成伪交付。

### 方式 B：终端自动化验收

```powershell
.\venv\Scripts\python.exe verify_mvp.py
```

该命令运行测试套件，验证接口契约、SQLite 恢复、状态机、QA 门禁、修复版本和 FFmpeg 文件输出；任何断言失败都会返回非零退出码。它验证的是 Mock/契约流程，不自动证明真实 9 条视频质量或 Round 2 提升。

真实烟测命令默认就会调用 GLM 和 Seedream，可能产生费用；`--include-video` 还会提交 Seedance：

```powershell
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png"
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png" --include-video
```

## 二、团队协作与配置交接

### 对接 AIGC 内容开发者

- 请内容人员提供一款真实商品的多角度素材，并确认哪些信息可以视为已核实事实。
- 共同检查 11 层 Prompt，特别是商品身份、可见文字、动作复杂度和功效表述。
- 由内容人员完成 QA 评分与 Failure Code 标记；只有飞书所有配置齐全且写表响应成功时，才能说记录已同步到飞书。

### 对接技术负责人

- Ark Key 已通过被 Git 忽略的本机 `.env` 支持；无需再等待“真实视频 Key”，下一步是用业务素材完成三镜、Round 1/2 和人工质量验收。
- 服务启动会自动加载 `.env`，因此已有模型和密钥无需每次在网页重填。网页“接口配置”保存的新值只对当前服务进程生效；需要跨重启保留时必须同步更新本机 `.env`。默认 `MOCK_MODE=true`，加载 Key 不会自动触发付费调用。
- 飞书远端镜像需要 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BITABLE_APP_TOKEN` 以及商品、任务、QA、交付四张表 ID。配置不全或写入失败时，SQLite 是事实源，待办留在 `sync_outbox`，不能声称已经写入飞书。
- 已实现 5 个火山方舟公共虚拟人的仓库白名单、商品选择持久化和 Seedance 2.0 `reference_image` 参数映射。客户端不能提交任意 `asset://`；重抽和修复沿用原人物。上线前仍应在当前账号做一次真实烟测，并以火山素材状态和平台商业条款为准。
- 付费识图、首帧、真实视频与修复接口当前仅允许本机访问。云部署前仍需反向代理、TLS、登录鉴权、权限控制、对象存储和密钥托管。
- 付费调用状态不确定时，先查火山控制台，不自动换幂等键。只有操作员明确确认新的可能计费尝试后，系统才推进下一 attempt。
- 真实指标请查询 `/api/metrics?product_id=...&execution_mode=real`；其中费用是项目按配置单价计算的本地估算，不是供应商账单。

## 三、8 项验收指标对照

| # | 交接文档验收标准 | 当前落地 | 验证边界 |
|---|---|---|---|
| 1 | 能输入商品 | `POST /api/assets/images` + Web 上传区 | 1～6 张图片；内容校验、元数据清理和哈希去重 |
| 2 | 能生成/读取结构化商品档案 | `core/vision_analyzer.py` + `core/product_analyzer.py` | GLM 结果按观察、包装宣称、推测、局限分层；Mock 不冒充识图 |
| 3 | 能自动组装 Prompt | `core/prompt_builder.py` | 20 个模块、固定 11 层与证据充分度策略 |
| 4 | 能调用视频 API 并返回结果 | `core/adapter/jimeng.py` | Ark 单镜已烟测；完整三镜质量仍待人工验收 |
| 5 | 能记录 QA | `core/qa_engine.py` | 人工 100 分制评分写入 SQLite；飞书写入取决于完整配置和实际响应 |
| 6 | 能明确记录 Failure Code | `core/repair_engine.py` | 人工标记 26 个普通代码或 7 个 HARD FAIL，不宣称自动视觉识别缺陷 |
| 7 | 能针对失败镜头生成 V1.1 并单独重跑 | `POST /api/video/tasks/{task_id}/retry` | 必须已有人工 Failure Code；保留父子关系和其他分镜 |
| 8 | 第二轮质量或首次 PASS 率提高 | Round 1/2 + 人工 QA + `/api/metrics?...&execution_mode=real` | 尚待真实 9 条 Round 1、失败镜头重跑及两轮人工 QA；不预置结论 |

最终演示原则很简单：工作流能力用自动化测试证明，供应商链路用真实任务记录证明，内容质量用人工 QA 证明。
