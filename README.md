# AI-SVWF

AI 带货视频工作流 MVP：商品合规建档、20 个 Prompt 积木、11 层确定性编译、3×5 秒分镜任务、人工 QA、Failure Code 定向修复、15 秒拼接，以及 SQLite + 飞书协作镜像。

当前版本可以在没有视频 API Key 的情况下用 Mock 视频完整验证工作流和网页交互。真实 Seedance/即梦/可灵接口尚未宣称接通：统一 Provider 契约已经准备好，拿到实际接口文档与 Key 后再完成供应商鉴权、字段映射和真实视频验收。

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

- 商品档案区分 `confirmed_information`、`possible_information`、合规风险和可信度；规则引擎不会把“图片 URL 存在”等同于“已经读懂图片”。
- Prompt Builder 使用固定 11 层顺序和 20 个模块，不依赖 LLM。
- `PromptVariantPlanner` 按场景、动作、镜头、光线、商品锁定生成有限变体，保存指纹并去重。
- 任务状态完整保存：`CREATED -> SUBMITTED -> PROCESSING -> COMPLETED -> QA_PENDING -> PASS/REPAIR/REJECTED`。
- SQLite 保存商品、Prompt 版本、变体、任务状态事件、QA、修复父子关系和交付物；服务重启后仍可查询。
- 飞书作为团队协作镜像。未配置或同步失败时，待办留在 SQLite outbox，不影响本地事实数据。
- 24 个 Failure Code 和 7 个 HARD FAIL；修复产生新版本，只重跑失败分镜。
- 拼接不再自动补虚假 Mock 片段。正式路径只接收 S01/S02/S03 三条 `PASS`；Mock UI 可显式预览并标记 `MOCK_QA_BYPASS`。
- 设置接口不回显 API Key/App Secret，默认只监听本机，Debug 默认关闭，CORS 仅允许本地来源。
- 可选 OpenAI 格式增强支持 Responses API（推荐）和 Chat Completions；没有 Key 时核心流程照常运行。

## 尚待真实 Key 完成

- 确认具体视频供应商、官方 endpoint、鉴权/签名、提交与轮询响应格式；
- 用真实商品图完成 image-to-video；
- 下载供应商结果并做格式/时长/画幅检查；
- 人工 QA 后做真实 Round 1、S02 V1.1 与第二模型 A/B；
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

## 数据与配置

- SQLite：`data/ai_svwf.sqlite3`（被 Git 忽略）
- 媒体/截图：`outputs/`（被 Git 忽略）
- 本地配置：`.env`（被 Git 忽略）
- 飞书兼容 JSON 镜像：`outputs/bitable_local_mirror.json`

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

LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=gpt-5-mini
LLM_API_STYLE=responses
```

## 核心目录

```text
core/
  adapter/jimeng.py          统一视频任务编排与 Provider 骨架
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
static/                      Web Studio
main.py                      FastAPI 服务
start.bat                    Windows 双击启动
```

## 开源协议

[MIT License](LICENSE)
