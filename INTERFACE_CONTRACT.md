# AI-SVWF MVP 接口与数据契约（V1.3）

本文把《AI带货视频工作流_MVP技术交接文档_V1.0》第 3～29 节转换为当前代码可执行、可测试的契约。接口统一增加 `/api` 前缀；职责仍与文档第 23 节一致。

## 1. 当前边界

- 已可离线验收：商品建档、20 个 Prompt 模块、11 层确定性编译、3×5 秒 Mock 任务、状态持久化、人工 QA、Failure Code、单镜头版本化重跑、三镜头拼接、SQLite、飞书待同步队列、可选 LLM 结构化建议。
- 已真实接通火山方舟：GLM-5.3 Flash 多模态识图、Seedream 5.0（4.5 回退）首帧、Seedance 2.0 异步视频。模型 ID 以账户 `/models` 实际返回为准，不使用营销简称猜测 ID。
- 2026-09-14 单镜烟测已完成：商品图上传 -> GLM 识图 -> 11 层编译 -> Seedream 首帧 -> Seedance 5 秒视频 -> 本地 MP4 -> 人工 QA。接口成功不代表内容通过；该样片因人物性别未遵循模板得到 81 分并标记 `REPAIR/PER002`。
- 非本轮失败条件：向量数据库、自动剪辑、自动字幕、复杂路由等，符合交接文档第 29 节。

## 2. 文档要求的主流程

```text
商品输入
  -> 图片上传、内容校验与 SHA-256 去重
  -> GLM 视觉证据提取（observed / packaging_claims / inference / notes）
  -> 商品档案（confirmed / possible / risk / confidence）
  -> 15 秒分镜计划（S01 / S02 / S03，各 5 秒，9:16）
  -> 20 模块选择
  -> PromptSchemaV1
  -> 11 层自然语言 Prompt 编译
  -> Seedream 首帧（使用 11 层中的静态子集）
  -> Seedance Provider 私有参数映射
  -> 视频任务状态机
  -> 人工 QA
  -> PASS：进入拼接
     REPAIR / REJECTED：Failure Code -> 新 Prompt 版本 -> 只重跑失败镜头 -> 重新 QA
  -> 交付物 + 指标统计 + 飞书镜像
```

不得让 LLM 每次自由生成完整 Prompt；不得把 `possible_information` 自动升级为事实；不得覆盖旧 Prompt 或旧视频记录。

## 3. 核心业务接口

| 接口 | 职责 | 关键约束 |
|---|---|---|
| `POST /api/assets/images` | 上传本地商品图片 | 本机限定；每次 1～6 张；JPEG/PNG/WebP；完整解码并重新编码以移除 EXIF/GPS/尾随数据，再校验像素、体积并按规范化哈希去重。 |
| `GET /api/assets/{asset_id}` | 读取上传素材元数据 | 本机限定；只读取 SQLite 已登记素材，不接受任意文件路径。 |
| `POST /api/products/analyze` | 商品建档与合规分析 | `product_name` 必填；`product_images` 真实生成前必填。规则引擎只登记图片，不假装读懂图片。 |
| `POST /api/products/analyze-vision` | GLM 多图识别与证据分层建档 | 付费且仅本机；只接收已上传 `asset_ids`；相同图片/输入/模型与幂等键命中已有记录，不重复调用。 |
| `GET /api/products/{product_id}` | 读取持久化商品档案 | 服务重启后仍可从 SQLite 恢复。 |
| `POST /api/products/{product_id}/claims/{claim_id}/confirm` | 人工确认或撤销单条商品 Claim | 只允许确认无合规代码的待确认 Claim；重复操作幂等，并写入不可变审计事件。 |
| `GET /api/products/{product_id}/claims/audit` | 查询 Claim 人工确认审计 | 返回确认人、动作、备注和时间，不改写原始视觉分析。 |
| `GET /api/virtual-actors/public` | 读取仓库公共虚拟人白名单 | 返回 5 个非密钥目录项与默认 `group_id`；不能通过该接口新增任意 Asset URI。 |
| `PUT /api/products/{product_id}/virtual-actor` | 选择/清除商品固定演员 | 只接收白名单 `group_id` 或 `null`；选择快照持久化到 SQLite。 |
| `POST /api/video-plan/generate` | 生成 S01/S02/S03 分镜计划 | MVP 仅支持 `TPL_SCENE_PRODUCT_15S_V1`、3×5 秒、9:16。 |
| `POST /api/prompts/compile` | 编译完整 `PromptSchemaV1` | 严格按 11 层顺序；保存 Prompt 版本；不存在的商品返回 404。 |
| `POST /api/prompts/variants/plan` | 生成受控 Prompt 变体 | 每镜头 1～6 个；只改变 scene/action/camera/lighting/product_lock 轴；去重并持久化。 |
| `GET /api/prompts/variants?product_id=...` | 查询已保存变体 | 可按 `shot_id` 进一步筛选。 |
| `POST /api/images/first-frame` | 生成并归档 Seedream 首帧 | 付费且仅本机；要求商品图；请求指纹幂等；5.0 的 400/404 可回退 4.5；返回本地永久 URL。 |
| `GET /api/images/tasks/{image_task_id}` | 查询首帧任务 | 返回模型、Prompt、来源素材、尺寸、状态、错误与本地归档。 |
| `GET /api/images/tasks?product_id=...` | 查询商品首帧任务 | 用于页面刷新后恢复 S01/S02/S03 首帧，不重新提交付费请求。 |
| `POST /api/video/generate` | 提交一个分镜生成任务 | 完整接收 provider/model/prompt/image/duration/ratio；可选 `virtual_actor_group_id` 只能由服务端白名单映射为 Seedance 2.0 `reference_image`；真实 Provider 付费且仅本机。 |
| `GET /api/video/tasks/{task_id}` | 查询单任务 | 返回内部/供应商 ID、状态、视频、耗时、成本、QA、失败与修复字段。 |
| `GET /api/video/tasks/{task_id}/events` | 查询状态历史 | 按发生顺序返回每次状态迁移，供审计与故障恢复。 |
| `GET /api/video/tasks?product_id=...` | 查询测试矩阵原始记录 | 前端矩阵只能展示这里的实际记录，不预置虚构分数。 |
| `GET /api/qa/failure-codes` | 查询完整 Failure Code 目录 | 动态返回 26 个普通代码与 7 个 HARD FAIL；网页不得只维护局部硬编码列表。 |
| `POST /api/video/tasks/{task_id}/qa` | 写入 100 分制人工 QA | PASS ≥85；REPAIR 70～84；FAIL <70；HARD_FAIL 直接失败。 |
| `POST /api/video/tasks/{task_id}/retry` | Failure Code 驱动的单镜头重跑 | 仅本机；必须先由人工 QA 提供 Failure Code；创建新任务并记录 `parent_task_id`，不覆盖、不重跑其他镜头。 |
| `POST /api/video/tasks/{task_id}/reroll` | 相同 Prompt 再抽一次 | Prompt 修订不变，原子分配下一 `attempt_no`；与人工改词和 Failure Code 修复明确区分。 |
| `POST /api/products/{product_id}/shots/{shot_id}/prompt-revisions` | 保存人工修改后的 Prompt 修订并生成 | 校验父修订/父任务同属该商品与镜头；保留不可变父子关系，不覆盖旧 Prompt。 |
| `GET /api/products/{product_id}/shots/{shot_id}/history` | 查询镜头完整历史 | 返回全部 Prompt 修订、生成尝试、QA 记录、父子关系与最终选用，供刷新恢复和动态对比。 |
| `POST /api/video/stitch` | 拼接 S01/S02/S03 | 必须同一商品、同一 execution mode、三个不同任务且每个本地视频存在；禁止 Mock/real 混拼。真实路径只接受 PASS，Mock 可显式预览绕过并标记 `MOCK_QA_BYPASS`。 |
| `PUT /api/products/{product_id}/shots/{shot_id}/selection` | 持久化最终选用版本 | 任务必须属于同商品/镜头且已有本地归档视频；真实任务必须先通过 QA。网页恢复和拼接均优先使用该选择。 |
| `GET /api/metrics?product_id=...&execution_mode=real` | 版本/模型统计 | `execution_mode` 只接受 `mock` 或 `real`；返回生成数、通过率、平均 QA、耗时及本地估算费用，不能当作供应商账单。 |
| `POST /api/feishu/sync/retry` | 重试飞书 outbox | 仅本机可调用；凭据或表 ID 不完整时保留待办并说明原因。 |
| `POST /api/enhancements/product-suggestions` | 可选 LLM 结构化建议 | 可能计费且仅本机；无 Key 返回 503，核心流程不受影响；结果永久标记为未验证。 |

兼容旧 Web Studio 的 `POST /api/video/repair` 仍保留，但必须提供已有 `task_id`，内部调用相同的重跑逻辑。

### 3.1 系统与可选交付接口

| 接口 | 职责 | 当前边界 |
|---|---|---|
| `GET /api/system/status` | 健康状态、SQLite/飞书摘要、模型配置与当日用量 | 不返回密钥。 |
| `GET /api/system/settings` | 读取非敏感运行配置及密钥是否已配置 | Secret/Token 字段恒为空；不回显原值。 |
| `POST /api/system/settings` | 修改当前进程设置 | 仅本机；空 Secret 表示保留原值。 |
| `GET /api/system/models` | 探测当前 Ark 账户模型 | 仅本机；只返回模型 ID，不返回密钥。 |
| `GET /api/tts/voices`、`POST /api/tts/generate` | 查询发音人、生成三段口播 | 生产路径 fail-closed；Edge TTS 或音频归一化失败即返回错误，不生成静音占位交付。成功响应仍给出 `tts_available`/`degraded` 供审计。 |
| `POST /api/export/jianying` | 生成剪映草稿及 ZIP | 仅本机；要求同一商品、同一模式、S01/S02/S03 各一条本地视频。真实任务必须全部 PASS，Mock 仅作 Mock 预览；目录和 ZIP 文件名清洗后再写入。仅当本机草稿目录实际写入成功时才可声明“已直写”。 |
| `POST /api/test/run-visual-e2e` | 从本机启动 UI 自动化测试 | 仅本机；属于验证辅助接口，不是生产业务接口。 |

## 4. 关键请求与响应

### 4.1 商品输入

```json
{
  "product_name": "示例商品",
  "product_images": ["https://cdn.example.com/product.jpg"],
  "short_description": "用户明确提供的描述",
  "reference_video": "",
  "target_audience": "",
  "preferred_scene": "普通办公室"
}
```

证据充分度按文档第 5 节使用：`0.90～1.00` 资料充分；`0.70～0.89` 可生成但禁用强事实宣传；`0.50～0.69` 仅保守生成；低于 `0.50` 仍可生成“纯商品展示”，但只允许外观、摆放、简单拿起/放回，禁止功效、参数、成分、检测结论和使用效果文案。它不是统计概率；兼容字段 `information_confidence` 已弃用。当前纯规则分析器不会读取图像像素，因此不会因“URL 非空”直接给 0.95。

首帧不是另起一套自由 Prompt：服务从已编译 11 层中提取第 1、3、4、8、10 层（镜头目标、商品、场景、光线、商品锁定）作为静态依据，再叠加 S01/S02/S03 构图和证据策略。

### 4.2 单镜头生成

```json
{
  "product_id": "PROD_...",
  "shot_id": "S02",
  "provider": "mock",
  "model": "mock-video-v1",
  "prompt_version": "1.0",
  "prompt": "11 层编译后的正向 Prompt",
  "negative_prompt": "负向约束",
  "image_url": "",
  "duration": 5,
  "aspect_ratio": "9:16",
  "variant_id": null,
  "idempotency_key": "product-shot-version-frame"
}
```

响应至少包含：`internal_task_id`、`provider_task_id`、`product_id`、`shot_id`、`provider`、`model`、`execution_mode`、`prompt_version`、`prompt_text`、`duration`、`aspect_ratio`、`status`、`video_url`、`generation_time_seconds`、`estimated_cost`、QA 与失败字段。`execution_mode=mock` 时，记录不得被用于证明对应真实 Provider 已接通。

### 4.3 QA 写回

路径中的任务 ID、请求体的 `internal_task_id`、`shot_id` 必须与目标任务一致。10 个维度总分上限为 100；Failure Code 只能使用第 15 节定义的 26 个普通代码与 7 个 HARD FAIL：

- 人物：`PER001`～`PER003`
- 手部：`HAND001`～`HAND003`
- 商品：`PRO001`～`PRO005`
- 动作：`MOT001`～`MOT004`
- 镜头：`CAM001`～`CAM003`
- 场景：`SCN001`～`SCN003`
- 文字：`TXT001`～`TXT002`
- 合规：`CMP001`～`CMP003`

硬失败使用 `HARD_FAIL_01`～`HARD_FAIL_07`。QA 响应返回 `repair_actions` 和 `next_prompt_version`。

## 5. 状态机

```text
CREATED -> SUBMITTED -> PROCESSING -> COMPLETED -> QA_PENDING
                                                    |-> PASS
                                                    |-> REPAIR -> 新任务 CREATED...
                                                    `-> REJECTED -> 新任务 CREATED...
任一生成阶段异常 -> FAILED -> 修复/人工处理
```

`COMPLETED` 表示供应商生成完成且视频已归档；随后立即进入 `QA_PENDING`。`PASS` 是人工 QA 结论，二者不可混用。相同请求指纹直接返回已有任务；服务重启仅续查已保存 `provider_task_id` 的任务。轮询遇到瞬时网络错误、429 或供应商 5xx 时，仍续接同一供应商任务 ID，不重新提交。

GLM、Seedream、Seedance 的付费调用若出现“请求是否已被供应商受理无法确认”，会记录 `ARK_*_SUBMISSION_UNCERTAIN` 或 `RESUME_REQUIRES_REVIEW`。前端不会因此自动增加 attempt 或更换幂等键，也不会自动重放。操作员必须先在火山控制台复核；只有明确确认“创建新的可能计费请求”后，才生成新的尝试键。普通、确定的失败与不确定提交必须分开处理。

## 6. SQLite + 飞书规范

SQLite 是本地事实源，默认位置 `data/ai_svwf.sqlite3`，包含：

- `products`：商品档案、证据分层、图片来源、证据充分度；
- `assets`：本地商品素材元数据、SHA-256、尺寸和永久路径；
- `vision_analyses`：视觉模型、输入指纹、资产集合和原始结构化结果；
- `prompt_versions`：按内容指纹追加、不可覆盖的 PromptSchema 版本（旧库中的 `prompt_schemas` 仅为兼容表）；
- `prompt_variants`：变体轴、正文、负向词、指纹；
- `shot_prompt_revisions`：不可变 Prompt 修订、显示版本、父修订和变更类型；
- `video_tasks`：文档第 13/26 节要求的完整生成记录，包括修订、尝试序号、生成类型和父任务；
- `task_events`：每次状态迁移；
- `qa_records`：追加式评分、Failure Code、逐项备注、发生秒点、帧范围和修复动作；
- `shot_selections`：每个商品/镜头最终选用的任务及操作备注；
- `claim_confirmation_events`：商品 Claim 的人工确认/撤销审计事件；
- `deliveries`：15 秒成片；
- `image_generations`：Seedream 首帧请求、模型回退、指纹、状态和归档；
- `sync_outbox`：飞书同步状态、重试次数和错误。

飞书是协作与审批镜像，不代替事务数据库。远端同步只有在 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BITABLE_APP_TOKEN`、`FEISHU_TABLE_PRODUCTS`、`FEISHU_TABLE_TASKS`、`FEISHU_TABLE_QA`、`FEISHU_TABLE_DELIVERY` 全部配置，且鉴权和写表实际成功时才成立。未配置或临时失败时，SQLite 仍是事实源，记录保留在 `sync_outbox`；本地 JSON 仅作为兼容导出镜像，不承担事务数据库职责。

## 7. PromptVariantPlanner 与 Round 1/2

- 主看板的“一键生成三分镜预览（3 条）”是 S01/S02/S03 各一条，便于预览完整叙事，不等于 Round 1。
- 矩阵窗口的 Round 1 是 S01×3、S02×3、S03×3，共 9 条实际任务。当前 Web 默认同时改变 scene/action/camera/lighting/product_lock 多个受控轴，因此适合覆盖探索，不能据此宣称某一单轴的因果提升；如需单轴实验，应调用变体 API 时只传一个 `axes` 项。
- 每个变体保存 `variant_id`、轴选择、Prompt 版本、指纹；不会做无限笛卡尔积。
- 生成完成后状态只能是 `QA_PENDING`。没有人工看片评分，就不能展示虚构通过率。
- Round 2：只选择已被 QA 标记为 `REPAIR/REJECTED` 的 S02，按真实 Failure Code 创建 V1.1，再重新 QA，之后才能比较通过率。

## 8. 可选 OpenAI 格式增强层

核心工作流不需要 OpenAI 格式 API。可选层用于受约束的受众、场景和模块建议，不能生成整条最终 Prompt，也不能写入 `confirmed_information`。

- 首选 `POST /v1/responses`，使用 JSON Schema Structured Outputs；
- 兼容 `POST /v1/chat/completions`；
- 设置项：`LLM_API_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_API_STYLE=responses|chat_completions`；
- 未配置时接口明确返回 503，不做静默伪结果。

参考 OpenAI 官方文档：[Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)、[Chat Completions](https://developers.openai.com/api/reference/cli/resources/chat)。

## 9. 安全与部署前置

- 默认监听 `127.0.0.1`，默认关闭 Debug，CORS 仅允许本地来源；云部署需显式配置反向代理、TLS、鉴权和 `ALLOWED_ORIGINS`。
- `GET /api/system/settings` 永不返回 API Key、App Secret 或 Bitable Token，只返回是否已配置。
- 设置更新、素材读写、模型探测、飞书重试和 UI 自动化执行端点只允许本机调用。GLM 识图、Seedream 首帧、真实 Seedance 生成/修复及可选 LLM 等可能计费的端点也只允许本机调用，直到云部署补齐认证授权。
- 真实 Provider 完成后必须下载结果到本地/对象存储再进入 QA 和拼接，不能只保存临时远程 URL。
- `.env`、`data/assets/`、SQLite 和 `outputs/` 均被 Git 忽略；API 错误在返回前会遮蔽当前密钥。
- 本地默认每日最多提交 20 次真实识图、6 张真实首帧和 12 条真实视频，可用 `MAX_REAL_VISION_TASKS_PER_DAY`、`MAX_REAL_IMAGE_TASKS_PER_DAY`、`MAX_REAL_VIDEO_TASKS_PER_DAY` 下调；这是防误点硬上限，不是供应商余额统计。
- 真实烟测曾因匿名首帧含可识别人脸收到 Seedance 隐私拒绝。未选公共虚拟人时 Seedream 首帧仍要求脸外/背影；选中白名单公共虚拟人时允许自然露脸，并在 Seedance 2.0 请求中同时提交商品首帧与独立 `reference_image`。
- 公共虚拟人链路只允许仓库目录中的 5 个 `group_id`，通用图片解析器仍拒绝 `asset://`，重抽、修复和拼接保留/校验演员一致性。该映射已离线测试，但尚未产生新的付费人物烟测；素材可见性、下架状态及商业范围以火山引擎账号和平台条款为准。
- 模型原生音频默认关闭；最终成片无 TTS 时显式 `-an`，有 TTS 时显式映射后期音轨。TTS 生产路径 fail-closed，生成或混音失败直接终止有声交付，不回退为静音成片。
- 拼接会对 S01/S02/S03 分别执行 scale/crop/fps、`tpad` 和 `trim`，把每段独立规范为 5 秒后再 concat 为 15 秒，避免供应商返回 4.x/5.x 秒素材造成分镜与口播边界错位；输出再次校验 720×1280、24fps、约 15 秒及音轨策略。

## 10. 验证命令

```powershell
.\venv\Scripts\python.exe verify_mvp.py
.\venv\Scripts\python.exe run_ui_test.py
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png"
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png" --include-video
```

第一条运行接口与持久化断言；第二条要求本地服务已启动，只有在每个页面状态满足断言后才保存截图。第三条默认就会调用 GLM 和 Seedream，可能产生费用；第四条还会提交 Seedance 视频。幂等指纹用于防重复提交，但不能替代运行前的费用确认；若状态不确定，应先人工复核，不能靠自动换键重试。
