# AI-SVWF

AI 带货视频工作流 MVP：商品合规建档、20 个 Prompt 积木、11 层确定性编译、3×5 秒分镜任务、人工 QA、Failure Code 定向修复、15 秒拼接，以及 SQLite + 飞书协作镜像。

当前版本既可在没有 Key 时用 Mock 视频离线验收，也已接通火山方舟的 GLM-5.3 Flash 多模态识图、Doubao Seedream 5.0/4.5 首帧生成和 Doubao Seedance 2.0 视频任务。2026-09-14 已用一张真实商品图完成 5 秒、720×1280、24fps 的端到端烟测并归档本地 MP4；人工 QA 给出 81 分、`REPAIR/PER002`（人物设定不匹配），说明“API 成功”不等于“内容通过”。

详细接口、状态和必存字段见 [INTERFACE_CONTRACT.md](INTERFACE_CONTRACT.md)。原始依据是 [AI带货视频工作流_MVP技术交接文档_V1.0.md](AI带货视频工作流_MVP技术交接文档_V1.0.md)。

## 快速启动（Windows）

直接双击 `start.bat`。脚本会：

1. 固定从项目目录启动；
2. 检查 Python 3.10+；
3. 自动创建/复用 `venv` 并安装依赖；
4. 等服务真正就绪后打开 `http://127.0.0.1:8000`；
5. 重复双击时复用现有服务，不制造端口冲突。

也可手动启动：

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

- Web Studio：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`

## 已实现且可验证

- Web Studio 支持一次上传 1～6 张 JPEG/PNG/WebP 商品图；服务端会完整解码后重新编码，移除 EXIF/GPS 与尾随数据，再按规范化内容做尺寸/体积校验、SHA-256 去重并保存到被 Git 忽略的本地素材库。
- GLM 多模态识别只将像素可见内容写入 `observed_information`；包装营销文字、模型推测和证据局限分别保存，不会被自动升级为已确认卖点。
- 商品档案区分 `confirmed_information`、`possible_information`、合规风险和证据充分度；兼容字段 `information_confidence` 已弃用，规则引擎不会把“图片 URL 存在”等同于“已经读懂图片”。
- Prompt Builder 使用固定 11 层顺序和 20 个模块，将识图所得的商品名称、品牌、规格、外观锚点和可见事实注入商品锁定层；核心组装不依赖自由生成式 LLM，Seedream 首帧复用第 1、3、4、8、10 层静态信息。
- `PromptVariantPlanner` 按场景、动作、镜头、光线、商品锁定生成有限变体，保存指纹并去重。
- 主看板按钮生成 S01/S02/S03 各一条的三镜预览；Section 19 矩阵中的 Round 1 才是 3×3 共 9 条。Web 默认使用多轴受控变体，单轴因果实验需通过 API 只选择一个变化轴。
- 任务状态完整保存：`CREATED -> SUBMITTED -> PROCESSING -> COMPLETED -> QA_PENDING -> PASS/REPAIR/REJECTED`。
- SQLite 保存商品、Prompt 修订、变体、生成尝试、任务状态事件、逐项 QA、修复父子关系、最终镜头选择和交付物；镜头历史与选择在服务重启后仍可完整恢复。
- 飞书作为团队协作镜像。未配置或同步失败时，待办留在 SQLite outbox，不影响本地事实数据。
- 人工 QA 可标记 26 个 Failure Code 和 7 个 HARD FAIL；系统据此生成修复版本并只重跑失败分镜，不宣称自动看视频识别缺陷。
- 拼接不再自动补虚假 Mock 片段，也禁止混用 Mock 与真实任务。正式路径只接收 S01/S02/S03 三条同模式的 `PASS`；Mock UI 可显式预览并标记 `MOCK_QA_BYPASS`。FFmpeg 会把每个分镜分别补帧/裁切为精确 5 秒，再拼成 15 秒成片。
- TTS 采用 fail-closed：Edge TTS、三段 5 秒音频归一化或最终混音任一步失败，接口直接报错，不会用静音音轨伪装成有声交付。剪映导出同样禁止 Mock/real 混用；真实草稿要求三镜全部 `PASS`，Mock 只作为预览，草稿目录和 ZIP 文件名会先清洗。
- 设置接口不回显 API Key/App Secret，默认只监听本机，Debug 默认关闭，CORS 仅允许本地来源。
- 可选 OpenAI 格式增强支持 Responses API（推荐）和 Chat Completions；没有 Key 时核心流程照常运行。
- 真实付费任务有内容指纹幂等、每日本地硬上限和 SQLite 状态恢复；Seedance 服务重启或瞬时网络/轮询失败后继续查询已有 Ark task ID。GLM、Seedream 或 Seedance 提交结果不确定时，系统保留原幂等键且不自动重提；必须先到供应商控制台人工复核，再明确确认一次新的可能计费尝试。
- 默认真实日限为识图 20 次、首帧 6 张、视频 12 条；付费模型接口在完成云端鉴权前只允许本机调用。`/api/metrics?execution_mode=mock|real` 可隔离两种模式，费用字段是本地估算，不是供应商账单。
- 公共虚拟人目录已内置 5 个用户提供的火山方舟公共素材；页面默认选择“日本·女·37 岁·新媒体运营”。客户端只提交 `group_id`，服务端从仓库白名单解析 `asset://...` 并作为 Seedance 2.0 的独立 `reference_image` 发送；人物快照进入商品、任务、幂等指纹、重抽与修复历史。
- 未选公共虚拟人时，首帧 Prompt 仍要求背影或脸外构图以降低人像隐私拒绝概率；选中公共虚拟人时可自然露脸，并由视频阶段的白名单人物参考锁定身份。两种方式均属于提示词/供应商约束，当前没有生成后人脸检测。
- Web Studio 会保存当前商品、首帧与视频任务标识；刷新后恢复工作区并续查原有任务，不重新提交同一次付费请求。

## 仍待项目方配置/验收

- 飞书 App ID、App Secret、Bitable App Token 及四张表 ID；未提供前 SQLite 是事实库，outbox 安全保留同步待办；
- 5 个公共虚拟人白名单及 Provider 参数映射已实现并通过离线 payload 测试；尚需用当前火山账号做一次真实人物任务烟测，确认素材未下架、账号可见且供应商仍接受首帧与人物双参考组合。仓库白名单不替代火山引擎商业条款或素材授权证明；
- 用真实业务素材完成 S01/S02/S03 全套人工 QA，之后再做 Round 1、失败镜头 V1.1 与第二模型 A/B；
- 云服务器反向代理、TLS、登录鉴权、对象存储与飞书真实表字段联调。

## 验证

接口与持久化测试（会实际生成 Mock MP4 并调用 FFmpeg）：

```powershell
.\venv\Scripts\python.exe verify_mvp.py
```

网页视觉测试需要先启动服务：

```powershell
.\venv\Scripts\python.exe run_ui_test.py
```

测试只有在页面状态满足断言时才截图；不再使用固定等待、错误 XPath 或预置 QA 分数制造“通过”。截图输出到 `outputs/`。

已配置本机 `.env` 后，可运行一次幂等的真实 Ark 烟测。默认路径已经会调用 GLM 识图和 Seedream 首帧，可能产生费用；加 `--include-video` 后还会提交付费 Seedance 视频任务：

```powershell
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png"
.\venv\Scripts\python.exe verify_real_api.py "C:\path\to\product.png" --include-video
```

## 数据与配置

- SQLite：`data/ai_svwf.sqlite3`（被 Git 忽略）
- 媒体/截图：`outputs/`（被 Git 忽略）
- 本地配置：`.env`（被 Git 忽略）
- 飞书兼容 JSON 镜像：`outputs/bitable_local_mirror.json`

服务每次启动都会自动读取项目根目录的 `.env`。写在 `.env` 中的模型与密钥会跨重启保留，因此无需每次打开网页重新填写；“接口配置”页面留空密钥字段会继续使用现有值，且后端不会把密钥回传给浏览器。页面中的“保存并立即生效”只更新当前服务进程，若要让新值在下次启动后仍然有效，需要同步写入本机 `.env`。默认 `MOCK_MODE=true`，所以即使已经加载真实 API Key，启动和普通预览也不会自动产生付费调用；需要人工切换到真实模式并再次确认。

常用环境变量：

```dotenv
HOST=127.0.0.1
PORT=8000
DEBUG=false
MOCK_MODE=true
ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

FEISHU_SYNC_MODE=dual
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BITABLE_APP_TOKEN=
FEISHU_TABLE_PRODUCTS=
FEISHU_TABLE_TASKS=
FEISHU_TABLE_QA=
FEISHU_TABLE_DELIVERY=

LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=gpt-5-mini
LLM_API_STYLE=responses

ARK_API_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_API_KEY=
VISION_MODEL=glm-5-3-flash-260828
IMAGE_MODEL_PRIMARY=doubao-seedream-5-0-260128
IMAGE_MODEL_FALLBACK=doubao-seedream-4-5-251128
VIDEO_MODEL=doubao-seedance-2-0-260128
VIDEO_GENERATE_AUDIO=false
MAX_UPLOAD_REQUEST_MB=65
MAX_REAL_VISION_TASKS_PER_DAY=20
MAX_REAL_IMAGE_TASKS_PER_DAY=6
MAX_REAL_VIDEO_TASKS_PER_DAY=12
```

## 核心目录

```text
core/
  adapter/jimeng.py          Ark Seedance 异步任务、幂等与恢复
  ark_client.py              方舟识图/Seedream/Seedance HTTP 客户端
  asset_manager.py           商品图片校验、去重与本地归档
  vision_analyzer.py         GLM 多模态证据分层建档
  first_frame_service.py     Seedream 首帧生成与持久化
  database.py                SQLite 事实库与飞书 outbox
  product_analyzer.py        商品建档与证据分层
  prompt_builder.py          11 层确定性 Prompt 编译
  prompt_variant_planner.py  受控批量变体
  qa_engine.py               100 分制 QA
  repair_engine.py           Failure Code -> 定向修复
  feishu_sync.py             飞书/本地镜像同步
  stitcher.py                FFmpeg 拼接
  llm_enhancer.py            可选 OpenAI 格式结构化建议
tests/
  test_api_contract.py       实际接口与文件/数据库断言
  test_asset_vision_pipeline.py  上传/识图/首帧离线契约断言
static/                      Web Studio
main.py                      FastAPI 服务
start.bat                    Windows 双击启动
verify_real_api.py           幂等真实 Ark 烟测
```

## 开源协议

[MIT License](LICENSE)
