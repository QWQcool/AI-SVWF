# AI-SVWF MVP 接口与数据契约（V1.1）

本文把《AI带货视频工作流_MVP技术交接文档_V1.0》第 3～29 节转换为当前代码可执行、可测试的契约。接口统一增加 `/api` 前缀；职责仍与文档第 23 节一致。

## 1. 当前边界

- 已可离线验收：商品建档、20 个 Prompt 模块、11 层确定性编译、3×5 秒 Mock 任务、状态持久化、人工 QA、Failure Code、单镜头版本化重跑、三镜头拼接、SQLite、飞书待同步队列、可选 LLM 结构化建议。
- 真实视频 Provider：统一请求/响应和状态契约已经实现；Seedance/即梦仅有通用 HTTP 骨架，可灵仅保留适配入口。未拿到实际供应商接口文档、鉴权方式和 Key 前，不声明真实视频已接通。
- 非本轮失败条件：向量数据库、自动剪辑、自动字幕、复杂路由等，符合交接文档第 29 节。

## 2. 文档要求的主流程

```text
商品输入
  -> 商品档案（confirmed / possible / risk / confidence）
  -> 15 秒分镜计划（S01 / S02 / S03，各 5 秒，9:16）
  -> 20 模块选择
  -> PromptSchemaV1
  -> 11 层自然语言 Prompt 编译
  -> Provider 私有参数映射
  -> 视频任务状态机
  -> 人工 QA
  -> PASS：进入拼接
     REPAIR / REJECTED：Failure Code -> 新 Prompt 版本 -> 只重跑失败镜头 -> 重新 QA
  -> 交付物 + 指标统计 + 飞书镜像
```

不得让 LLM 每次自由生成完整 Prompt；不得把 `possible_information` 自动升级为事实；不得覆盖旧 Prompt 或旧视频记录。

## 3. REST 接口

| 接口 | 职责 | 关键约束 |
|---|---|---|
| `POST /api/products/analyze` | 商品建档与合规分析 | `product_name` 必填；`product_images` 真实生成前必填。规则引擎只登记图片，不假装读懂图片。 |
| `GET /api/products/{product_id}` | 读取持久化商品档案 | 服务重启后仍可从 SQLite 恢复。 |
| `POST /api/video-plan/generate` | 生成 S01/S02/S03 分镜计划 | MVP 仅支持 `TPL_SCENE_PRODUCT_15S_V1`、3×5 秒、9:16。 |
| `POST /api/prompts/compile` | 编译完整 `PromptSchemaV1` | 严格按 11 层顺序；保存 Prompt 版本；不存在的商品返回 404。 |
| `POST /api/prompts/variants/plan` | 生成受控 Prompt 变体 | 每镜头 1～6 个；只改变 scene/action/camera/lighting/product_lock 轴；去重并持久化。 |
| `POST /api/video/generate` | 提交一个分镜生成任务 | 完整接收 provider/model/prompt/image/duration/ratio，不静默忽略参数。 |
| `GET /api/video/tasks/{task_id}` | 查询单任务 | 返回内部/供应商 ID、状态、视频、耗时、成本、QA、失败与修复字段。 |
| `GET /api/video/tasks/{task_id}/events` | 查询状态历史 | 按发生顺序返回每次状态迁移，供审计与故障恢复。 |
| `GET /api/video/tasks?product_id=...` | 查询测试矩阵原始记录 | 前端矩阵只能展示这里的实际记录，不预置虚构分数。 |
| `POST /api/video/tasks/{task_id}/qa` | 写入 100 分制人工 QA | PASS ≥85；REPAIR 70～84；FAIL <70；HARD_FAIL 直接失败。 |
| `POST /api/video/tasks/{task_id}/retry` | Failure Code 驱动的单镜头重跑 | 创建新任务并记录 `parent_task_id`；不覆盖、不重跑其他镜头。 |
| `POST /api/video/stitch` | 拼接 S01/S02/S03 | 必须同一商品、三个不同任务、每个本地视频存在；默认只接受 PASS。Mock 可显式预览绕过，并标记 `MOCK_QA_BYPASS`。 |
| `GET /api/metrics?product_id=...` | 版本/模型统计 | 返回生成数、通过数、失败数、通过率、平均 QA、成本和耗时。 |
| `POST /api/feishu/sync/retry` | 重试飞书 outbox | 仅本机可调用；凭据或表 ID 不完整时保留待办并说明原因。 |
| `POST /api/enhancements/product-suggestions` | 可选 LLM 结构化建议 | 无 Key 返回 503，核心流程不受影响；结果永久标记为未验证。 |

兼容旧 Web Studio 的 `POST /api/video/repair` 仍保留，但必须提供已有 `task_id`，内部调用相同的重跑逻辑。

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

可信度按文档第 5 节使用：`0.90～1.00` 高可信；`0.70～0.89` 可生成但禁用强事实宣传；`0.50～0.69` 仅保守生成；低于 `0.50` 只展示、不自动进入生成。当前纯规则分析器不会读取图像像素，因此不会因“URL 非空”直接给 0.95。

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
  "variant_id": null
}
```

响应至少包含：`internal_task_id`、`provider_task_id`、`product_id`、`shot_id`、`provider`、`model`、`execution_mode`、`prompt_version`、`prompt_text`、`duration`、`aspect_ratio`、`status`、`video_url`、`generation_time_seconds`、`estimated_cost`、QA 与失败字段。`execution_mode=mock` 时，记录不得被用于证明对应真实 Provider 已接通。

### 4.3 QA 写回

路径中的任务 ID、请求体的 `internal_task_id`、`shot_id` 必须与目标任务一致。10 个维度总分上限为 100；Failure Code 只能使用第 15 节定义的 26 个代码：

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

`COMPLETED` 表示供应商生成完成且视频已归档；随后立即进入 `QA_PENDING`。`PASS` 是人工 QA 结论，二者不可混用。

## 6. SQLite + 飞书规范

SQLite 是本地事实源，默认位置 `data/ai_svwf.sqlite3`，包含：

- `products`：商品档案、证据分层、图片来源、可信度；
- `prompt_versions`：按内容指纹追加、不可覆盖的 PromptSchema 版本（旧库中的 `prompt_schemas` 仅为兼容表）；
- `prompt_variants`：变体轴、正文、负向词、指纹；
- `video_tasks`：文档第 13/26 节要求的完整生成记录；
- `task_events`：每次状态迁移；
- `qa_records`：评分、失败说明、修复动作；
- `deliveries`：15 秒成片；
- `sync_outbox`：飞书同步状态、重试次数和错误。

飞书是协作与审批镜像，不代替事务数据库。未配置或临时失败时，记录保留在 SQLite/outbox；本地 JSON 仅作为兼容导出镜像，不再承担数据库职责。

## 7. PromptVariantPlanner 与 Round 1/2

- Round 1：S01×3、S02×3、S03×3，共 9 条实际任务。三次相同基线可测模型随机稳定性；受控变体可测单一设计轴影响。
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
- 设置更新和 UI 自动化执行端点只允许本机调用。
- 真实 Provider 完成后必须下载结果到本地/对象存储再进入 QA 和拼接，不能只保存临时远程 URL。

## 10. 验证命令

```powershell
.\venv\Scripts\python.exe verify_mvp.py
.\venv\Scripts\python.exe run_ui_test.py
```

第一条运行接口与持久化断言；第二条要求本地服务已启动，只有在每个页面状态满足断言后才保存截图。
