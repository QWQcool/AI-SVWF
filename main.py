"""
AI-SVWF 主服务程序 (FastAPI)
包含所有工作流 REST 接口、静态资源托管与自动化状态机调度
"""

import os
import sys
import time
from typing import List, Optional, Dict, Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.schemas import (
    ProductInput,
    ProductAnalysis,
    PromptSchemaV1,
    VideoTaskRecord,
    QARecordInput,
    StitchResult,
    TaskStatus,
)
from core.product_analyzer import ProductAnalyzer
from core.prompt_builder import PromptBuilder
from core.adapter.jimeng import JimengAdapter
from core.qa_engine import QAEngine
from core.repair_engine import RepairEngine
from core.stitcher import StitcherService
from core.feishu_sync import FeishuBitableSync
from core.storage import StorageManager
from core.tts_service import TTSService, VOICE_PRESETS
from core.jianying_exporter import JianyingExporter


app = FastAPI(
    title="AI-SVWF 工作流引擎",
    description="专为 AIGC 内容创作者与电商带货设计的 3×5s 带货视频自动化工作流 (MVP V1.0)",
    version="1.0.0",
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 outputs 目录用于静态视频访问
app.mount("/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")

# 挂载 static 目录用于前端静态文件
static_dir = settings.BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ------------------------------------------------------------------------------
# 1. 首页路由 (返回单页 Storyboard Studio)
# ------------------------------------------------------------------------------
@app.get("/")
async def index():
    index_html = static_dir / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return JSONResponse({
        "status": "online",
        "service": "AI-SVWF Workflow Engine",
        "doc": "/docs",
        "mock_mode": settings.MOCK_MODE,
    })


# ------------------------------------------------------------------------------
# 2. 系统全局状态与设置
# ------------------------------------------------------------------------------
@app.get("/api/system/status")
async def get_system_status():
    """获取系统运行模式、飞书连接状态与成本核算参数"""
    mirror_summary = FeishuBitableSync.get_mirror_summary()
    mirror_summary["is_feishu_configured"] = bool(settings.FEISHU_APP_ID)
    return {
        "status": "healthy",
        "mock_mode": settings.MOCK_MODE,
        "billing_mode": settings.BILLING_MODE,
        "cost_per_second_cny": settings.COST_PER_SECOND_CNY,
        "credits_per_second": settings.CREDITS_PER_SECOND,
        "sample_5s_cost": settings.calculate_cost(5),
        "feishu": mirror_summary,
    }


@app.get("/api/system/settings")
async def get_system_settings():
    """获取完整的系统配置 (Seedance/即梦/可灵Key、模型、LLM配置、飞书Token等)"""
    return {
        "mock_mode": settings.MOCK_MODE,
        "model_provider": settings.MODEL_PROVIDER,
        "jimeng_api_key": settings.JIMENG_API_KEY,
        "jimeng_api_secret": settings.JIMENG_API_SECRET,
        "jimeng_default_model": settings.JIMENG_DEFAULT_MODEL,
        "seedance_ark_api_key": settings.SEEDANCE_ARK_API_KEY or settings.JIMENG_API_KEY,
        "seedance_endpoint_id": settings.SEEDANCE_ENDPOINT_ID,
        "kling_api_key": settings.KLING_API_KEY,
        "llm_api_base_url": settings.LLM_API_BASE_URL,
        "llm_api_key": settings.LLM_API_KEY,
        "llm_model": settings.LLM_MODEL,
        "billing_mode": settings.BILLING_MODE,
        "cost_per_second_cny": settings.COST_PER_SECOND_CNY,
        "credits_per_second": settings.CREDITS_PER_SECOND,
        "feishu_app_id": settings.FEISHU_APP_ID,
        "feishu_app_secret": settings.FEISHU_APP_SECRET,
        "feishu_bitable_app_token": settings.FEISHU_BITABLE_APP_TOKEN,
    }


@app.post("/api/system/settings")
async def update_settings(payload: Dict[str, Any] = Body(...)):
    """动态更新运行时配置 (配置Seedance/即梦Key、切换模型、更新成本单价、LLM Key)"""
    if "mock_mode" in payload:
        settings.MOCK_MODE = bool(payload["mock_mode"])
    if "model_provider" in payload:
        settings.MODEL_PROVIDER = str(payload["model_provider"]).strip()
    if "jimeng_api_key" in payload:
        settings.JIMENG_API_KEY = str(payload["jimeng_api_key"]).strip()
    if "jimeng_api_secret" in payload:
        settings.JIMENG_API_SECRET = str(payload["jimeng_api_secret"]).strip()
    if "jimeng_default_model" in payload:
        settings.JIMENG_DEFAULT_MODEL = str(payload["jimeng_default_model"]).strip()
    if "seedance_ark_api_key" in payload:
        settings.SEEDANCE_ARK_API_KEY = str(payload["seedance_ark_api_key"]).strip()
        if not settings.JIMENG_API_KEY:
            settings.JIMENG_API_KEY = settings.SEEDANCE_ARK_API_KEY
    if "seedance_endpoint_id" in payload:
        settings.SEEDANCE_ENDPOINT_ID = str(payload["seedance_endpoint_id"]).strip()
    if "kling_api_key" in payload:
        settings.KLING_API_KEY = str(payload["kling_api_key"]).strip()
    if "llm_api_base_url" in payload:
        settings.LLM_API_BASE_URL = str(payload["llm_api_base_url"]).strip()
    if "llm_api_key" in payload:
        settings.LLM_API_KEY = str(payload["llm_api_key"]).strip()
    if "llm_model" in payload:
        settings.LLM_MODEL = str(payload["llm_model"]).strip()
    if "feishu_app_id" in payload:
        settings.FEISHU_APP_ID = str(payload["feishu_app_id"]).strip()
    if "feishu_app_secret" in payload:
        settings.FEISHU_APP_SECRET = str(payload["feishu_app_secret"]).strip()
    if "feishu_bitable_app_token" in payload:
        settings.FEISHU_BITABLE_APP_TOKEN = str(payload["feishu_bitable_app_token"]).strip()
    if "cost_per_second_cny" in payload:
        settings.COST_PER_SECOND_CNY = float(payload["cost_per_second_cny"])
    if "billing_mode" in payload and payload["billing_mode"] in ["CNY", "POINTS"]:
        settings.BILLING_MODE = payload["billing_mode"]

    return {
        "message": "Settings updated successfully",
        "current_mock_mode": settings.MOCK_MODE,
        "model_provider": settings.MODEL_PROVIDER,
        "model": settings.JIMENG_DEFAULT_MODEL,
        "has_jimeng_key": bool(settings.JIMENG_API_KEY or settings.SEEDANCE_ARK_API_KEY),
        "has_llm_key": bool(settings.LLM_API_KEY),
    }


# ------------------------------------------------------------------------------
# 3. 商品建档与合规分析 (Section 4, 5)
# ------------------------------------------------------------------------------
# 内存缓存当前活跃商品档案
_current_products: Dict[str, ProductAnalysis] = {}


@app.post("/api/products/analyze", response_model=ProductAnalysis)
async def analyze_product(product_input: ProductInput):
    """
    接收用户最小商品输入，执行合规风控审查与可信度核算，自动构建结构化商品档案
    """
    analysis = ProductAnalyzer.analyze(product_input)
    _current_products[analysis.product_id] = analysis

    # 异步同步至飞书《01_商品资料库》
    FeishuBitableSync.sync_product(analysis)

    return analysis


@app.get("/api/products/{product_id}", response_model=ProductAnalysis)
async def get_product(product_id: str):
    if product_id not in _current_products:
        raise HTTPException(status_code=404, detail="Product not found")
    return _current_products[product_id]


# ------------------------------------------------------------------------------
# 4. 11层 Prompt 编译 (Section 8, 9)
# ------------------------------------------------------------------------------
@app.post("/api/prompts/compile", response_model=PromptSchemaV1)
async def compile_prompts(payload: Dict[str, Any] = Body(...)):
    """
    根据商品档案，按照 11 层标准化流水线编译 S01、S02、S03 的全套分镜 Prompt
    """
    product_id = payload.get("product_id")
    version = payload.get("version", "1.0")

    product = _current_products.get(product_id)
    if not product:
        # 提供默认回退商品档案
        product = ProductAnalyzer.analyze(
            ProductInput(product_name=payload.get("product_name", "测试咖啡保温杯"))
        )
        _current_products[product.product_id] = product

    schema = PromptBuilder.build_full_schema(product, version=version)
    return schema


# ------------------------------------------------------------------------------
# 5. 视频生成与单镜头任务管理 (Section 11, 12, 24)
# ------------------------------------------------------------------------------
@app.post("/api/video/generate", response_model=VideoTaskRecord)
async def generate_video_shot(payload: Dict[str, Any] = Body(...)):
    """
    提交单镜头视频生成任务 (遵循交接文档 Section 24 统一生成接口)
    """
    product_id = payload.get("product_id", "PROD_DEFAULT")
    shot_id = payload.get("shot_id", "S01")
    prompt = payload.get("prompt", "")
    negative_prompt = payload.get("negative_prompt", "")
    image_url = payload.get("image_url", "")
    version = payload.get("prompt_version", "1.0")
    product_name = payload.get("product_name", "测试商品")

    if not prompt:
        product = _current_products.get(product_id)
        if product:
            shot_data = PromptBuilder.compile_shot_prompt(product, shot_id=shot_id, version=version)
            prompt = shot_data["compiled_positive"]
            negative_prompt = shot_data["compiled_negative"]
        else:
            prompt = f"真实场景体验型带货视频 {shot_id} 普通生活化场景"

    task = await JimengAdapter.submit_video_task(
        product_id=product_id,
        shot_id=shot_id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        image_url=image_url,
        prompt_version=version,
        product_name=product_name,
    )

    # 同步至飞书《视频创作任务库》
    FeishuBitableSync.sync_task(task)

    return task


@app.get("/api/video/tasks/{task_id}", response_model=VideoTaskRecord)
async def get_video_task(task_id: str):
    task = JimengAdapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # 状态变化时触发飞书同步
    FeishuBitableSync.sync_task(task)
    return task


@app.get("/api/video/tasks")
async def list_video_tasks(product_id: Optional[str] = None):
    return JimengAdapter.list_tasks(product_id)


# ------------------------------------------------------------------------------
# 6. QA 质检评分与回写 (Section 14, 25)
# ------------------------------------------------------------------------------
@app.post("/api/video/tasks/{task_id}/qa")
async def submit_qa_evaluation(task_id: str, qa_input: QARecordInput):
    """
    提交 QA 验收评分，自动计算加权总分并识别 Failure Code
    """
    task = JimengAdapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    total_score, status, report = QAEngine.evaluate(qa_input)
    repair_actions = RepairEngine.resolve_repair_actions(qa_input.failure_codes)

    task.qa_score = total_score
    task.qa_status = status
    task.failure_codes = qa_input.failure_codes
    task.failure_notes = qa_input.failure_notes
    task.repair_actions = repair_actions

    # 同步写入飞书《检查层 (QA质检表)》
    FeishuBitableSync.sync_qa_record(qa_input, total_score, status.value, repair_actions)

    return {
        "task_id": task_id,
        "qa_score": total_score,
        "qa_status": status.value,
        "repair_actions": repair_actions,
        "report": report,
    }


# ------------------------------------------------------------------------------
# 7. 单镜头精准修复重跑 (Section 16, 17, 19 核心杀手锏)
# ------------------------------------------------------------------------------
@app.post("/api/video/repair", response_model=VideoTaskRecord)
async def trigger_shot_repair(payload: Dict[str, Any] = Body(...)):
    """
    针对失败镜头（如 S02）生成 V1.1 Prompt 并【仅单独重跑该镜头】，绝不浪费算力重新生成已 PASS 镜头！
    """
    task_id = payload.get("task_id")
    old_task = JimengAdapter.get_task(task_id) if task_id else None

    product_id = payload.get("product_id") or (old_task.product_id if old_task else "PROD_DEFAULT")
    shot_id = payload.get("shot_id") or (old_task.shot_id if old_task else "S02")
    failure_codes = payload.get("failure_codes") or (old_task.failure_codes if old_task else ["HAND001"])
    current_version = payload.get("current_version") or (old_task.prompt_version if old_task else "1.0")

    product = _current_products.get(product_id)
    if not product:
        product = ProductAnalyzer.analyze(ProductInput(product_name="测试商品"))
        _current_products[product.product_id] = product

    # 1. 针对性构建 V1.1 修复提示词
    repair_pack = RepairEngine.generate_v1_1_prompt(
        product=product,
        shot_id=shot_id,
        current_version=current_version,
        failure_codes=failure_codes,
    )

    # 2. 仅重新提交该单镜头的生成任务
    new_task = await JimengAdapter.submit_video_task(
        product_id=product_id,
        shot_id=shot_id,
        prompt=repair_pack["compiled_positive"],
        negative_prompt=repair_pack["compiled_negative"],
        image_url=old_task.source_image if old_task else "",
        prompt_version=repair_pack["next_version"],
        product_name=product.product_name,
    )

    new_task.failure_codes = failure_codes
    new_task.repair_actions = repair_pack["repair_actions"]

    FeishuBitableSync.sync_task(new_task)

    return new_task


# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# 8. 3 镜头拼接缝合成 15 秒带货成片 (支持 TTS 自动混音配音)
# ------------------------------------------------------------------------------
@app.post("/api/video/stitch", response_model=StitchResult)
async def stitch_final_video(payload: Dict[str, Any] = Body(...)):
    """
    将 S01, S02, S03 三段 5 秒视频无缝拼接为 15 秒 9:16 成片，支持同时混入 TTS 口播配音
    """
    task_ids = payload.get("task_ids", [])
    product_id = payload.get("product_id", "PROD_DEFAULT")
    product_name = payload.get("product_name", "带货商品")
    product_desc = payload.get("product_desc", "")
    enable_tts = payload.get("enable_tts", True)
    voice_key = payload.get("voice", "xiaoxiao")

    video_paths = []
    shots = []

    for tid in task_ids:
        t = JimengAdapter.get_task(tid)
        if t and t.local_video_path and os.path.exists(t.local_video_path):
            video_paths.append(t.local_video_path)
            shots.append(t.shot_id)

    # 如果传入的有效本地路径不足 3 个，自动补全/生成示例分镜视频
    while len(video_paths) < 3:
        idx = len(video_paths) + 1
        s_id = f"S0{idx}"
        p, _ = StorageManager.create_mock_video(s_id, "1.0", product_name, 5)
        video_paths.append(p)
        shots.append(s_id)

    # 若开启 TTS 口播配音，生成 15s 配音音频
    audio_merged_path = None
    if enable_tts:
        try:
            tts_res = await TTSService.generate_batch_tts(product_name, product_desc, voice_key)
            audio_merged_path = tts_res.get("merged_audio_path")
        except Exception as e:
            print(f"[main] TTS 合成异常，跳过配音: {e}")

    stitch_res = StitcherService.stitch_3x5s_videos(
        video_paths=video_paths,
        product_id=product_id,
        task_ids=task_ids,
        shots=shots,
        audio_path=audio_merged_path,
    )

    # 同步回写飞书《交接层》
    FeishuBitableSync.sync_delivery(stitch_res)

    return stitch_res


# ------------------------------------------------------------------------------
# 8.2 TTS 口播语音生成与发音人接口
# ------------------------------------------------------------------------------
@app.get("/api/tts/voices")
async def get_tts_voices():
    """获取所有可用 TTS 发音人预设"""
    return {"voices": VOICE_PRESETS}


@app.post("/api/tts/generate")
async def generate_tts(payload: Dict[str, Any] = Body(...)):
    """为指定商品生成 3×5s 时序口播台词与音频"""
    product_name = payload.get("product_name", "优质好物")
    desc = payload.get("short_description", "")
    voice = payload.get("voice", "xiaoxiao")

    result = await TTSService.generate_batch_tts(product_name, desc, voice)
    return result


# ------------------------------------------------------------------------------
# 8.3 剪映电脑版 (Jianying Pro) 草稿一键导出接口
# ------------------------------------------------------------------------------
@app.post("/api/export/jianying")
async def export_jianying_draft(payload: Dict[str, Any] = Body(...)):
    """
    一键导出标准剪映工程草稿包 (.zip)，并自动同步直写本机剪映草稿目录
    """
    product_id = payload.get("product_id", "PROD_DEFAULT")
    product_name = payload.get("product_name", "带货商品")
    product_desc = payload.get("product_desc", "")
    task_ids = payload.get("task_ids", [])
    voice = payload.get("voice", "xiaoxiao")

    # 1. 收集 3 分镜视频
    video_paths = []
    for tid in task_ids:
        t = JimengAdapter.get_task(tid)
        if t and t.local_video_path and os.path.exists(t.local_video_path):
            video_paths.append(t.local_video_path)

    while len(video_paths) < 3:
        idx = len(video_paths) + 1
        s_id = f"S0{idx}"
        p, _ = StorageManager.create_mock_video(s_id, "1.0", product_name, 5)
        video_paths.append(p)

    # 2. 生成/提取 TTS 音频与带货花字字幕
    tts_res = await TTSService.generate_batch_tts(product_name, product_desc, voice)
    audio_paths = [s["audio_path"] for s in tts_res["scripts"]]
    subtitles = [s["subtitle"] for s in tts_res["scripts"]]

    # 3. 编译并打包剪映工程
    draft_res = JianyingExporter.export_draft(
        product_name=product_name,
        video_paths=video_paths[:3],
        audio_paths=audio_paths[:3],
        subtitles=subtitles[:3],
        durations_sec=[5.0, 5.0, 5.0],
        project_title=f"AI-SVWF_{product_name}",
    )

    return draft_res


# ------------------------------------------------------------------------------
# 8.5 自动化 UI 模拟点击与视觉截屏测试 (Visual E2E Runner)
# ------------------------------------------------------------------------------
@app.post("/api/test/run-visual-e2e")
async def run_visual_e2e_test():
    """
    触发 Headless 模拟点击与视觉回归测试链，
    自动执行 6 个关键交互步骤，并生成高清截屏证据图
    """
    import subprocess
    output_dir = settings.OUTPUT_DIR
    output_dir.mkdir(exist_ok=True)

    try:
        # 执行 run_ui_test.py
        proc = subprocess.run(
            [sys.executable, "run_ui_test.py"],
            capture_output=True,
            text=True,
            timeout=45,
            encoding="utf-8",
            errors="ignore",
        )
        logs = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        success = proc.returncode == 0
    except Exception as e:
        logs = f"运行自动化 UI 截屏测试异常: {str(e)}"
        success = False

    # 收集 6 个步骤的截屏文件
    steps = [
        {"step": 1, "title": "Web 首页就绪", "filename": "test_step1_home.png", "desc": "初始布局、Agent 拓扑栏与预置案例"},
        {"step": 2, "title": "切换预置案例", "filename": "test_step2_preset.png", "desc": "切换修护精华液并自动完成 11 层编译"},
        {"step": 3, "title": "接口与模型配置", "filename": "test_step3_settings_modal.png", "desc": "Seedance / 即梦 Key 与成本单价配置弹窗"},
        {"step": 4, "title": "并发生成全部分镜", "filename": "test_step4_generated.png", "desc": "S01, S02, S03 三分镜并发渲染与视口回显"},
        {"step": 5, "title": "S02 修复重跑 (V1.1)", "filename": "test_step5_repaired.png", "desc": "手部畸形归因介入，S01/S03锁定，S02单镜重跑"},
        {"step": 6, "title": "15s 带货成片交付", "filename": "test_step6_stitched.png", "desc": "FFmpeg 3×5s 无损无黑帧缝合成片与弹窗播放"},
    ]

    for s in steps:
        fpath = output_dir / s["filename"]
        s["exists"] = fpath.exists()
        s["url"] = f"/outputs/{s['filename']}?t={int(time.time())}" if fpath.exists() else None

    return {
        "success": success,
        "logs": logs,
        "steps": steps,
    }


# ------------------------------------------------------------------------------
# 9. 服务启动入口
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print(f"==================================================")
    print(f"  AI-SVWF AI带货视频工作流引擎 (MVP V1.0)")
    print(f"  Web Studio: http://localhost:{settings.PORT}")
    print(f"  Swagger API: http://localhost:{settings.PORT}/docs")
    print(f"  运行模式: {'[🟡 离线高保真 Mock 模式]' if settings.MOCK_MODE else '[🟢 真实即梦 API 模式]'}")
    print(f"==================================================")
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
