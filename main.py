"""AI-SVWF FastAPI service.

The REST surface mirrors section 23 of the handoff document while retaining a
small number of ``/api`` compatibility routes used by the bundled Web Studio.
"""

import asyncio
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.adapter.jimeng import JimengAdapter
from core.config import settings
from core.database import database
from core.feishu_sync import FeishuBitableSync
from core.jianying_exporter import JianyingExporter
from core.llm_enhancer import LLMEnhancer
from core.product_analyzer import ProductAnalyzer
from core.prompt_builder import PromptBuilder
from core.prompt_variant_planner import PromptVariantPlanner
from core.qa_engine import QAEngine
from core.repair_engine import RepairEngine
from core.schemas import (
    LLMEnhancementRequest,
    ProductAnalysis,
    ProductInput,
    PromptCompileRequest,
    PromptSchemaV1,
    PromptVariant,
    PromptVariantPlanRequest,
    QARecordInput,
    QAStatus,
    StitchRequest,
    StitchResult,
    TaskRetryRequest,
    TaskStatus,
    VideoGenerateRequest,
    VideoPlanRequest,
    VideoTaskRecord,
    utc_now_iso,
)
from core.stitcher import StitcherService
from core.tts_service import TTSService, VOICE_PRESETS


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


app = FastAPI(
    title="AI-SVWF 工作流引擎",
    description="3×5s AI 带货视频工作流（本地 SQLite + 飞书协作镜像）",
    version="1.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.mount("/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")
static_dir = settings.BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _product_or_404(product_id: str) -> ProductAnalysis:
    product = database.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product not found: {product_id}")
    return product


def _task_or_404(task_id: str) -> VideoTaskRecord:
    task = JimengAdapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


@app.get("/")
async def index():
    index_html = static_dir / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return JSONResponse({"status": "online", "doc": "/docs", "mock_mode": settings.MOCK_MODE})


@app.get("/api/system/status")
async def get_system_status():
    mirror = FeishuBitableSync.get_mirror_summary()
    mirror["is_feishu_configured"] = bool(
        settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET and settings.FEISHU_BITABLE_APP_TOKEN
    )
    return {
        "status": "healthy",
        "mock_mode": settings.MOCK_MODE,
        "billing_mode": settings.BILLING_MODE,
        "cost_per_second_cny": settings.COST_PER_SECOND_CNY,
        "credits_per_second": settings.CREDITS_PER_SECOND,
        "sample_5s_cost": settings.calculate_cost(5),
        "database_path": str(settings.DATABASE_PATH),
        "database": database.stats(),
        "feishu": mirror,
        "feishu_sync_mode": settings.FEISHU_SYNC_MODE,
        "llm_enhancement_configured": LLMEnhancer.configured(),
    }


@app.get("/api/system/settings")
async def get_system_settings():
    """Return non-secret settings and booleans only; credentials never leave the server."""
    return {
        "mock_mode": settings.MOCK_MODE,
        "model_provider": settings.MODEL_PROVIDER,
        "jimeng_api_key": "",
        "jimeng_api_secret": "",
        "jimeng_default_model": settings.JIMENG_DEFAULT_MODEL,
        "seedance_ark_api_key": "",
        "seedance_endpoint_id": settings.SEEDANCE_ENDPOINT_ID,
        "kling_api_key": "",
        "llm_api_base_url": settings.LLM_API_BASE_URL,
        "llm_api_key": "",
        "llm_model": settings.LLM_MODEL,
        "llm_api_style": settings.LLM_API_STYLE,
        "billing_mode": settings.BILLING_MODE,
        "cost_per_second_cny": settings.COST_PER_SECOND_CNY,
        "credits_per_second": settings.CREDITS_PER_SECOND,
        "feishu_sync_mode": settings.FEISHU_SYNC_MODE,
        "feishu_app_id": settings.FEISHU_APP_ID,
        "feishu_app_secret": "",
        "feishu_bitable_app_token": "",
        "has_jimeng_key": bool(settings.JIMENG_API_KEY or settings.SEEDANCE_ARK_API_KEY),
        "has_kling_key": bool(settings.KLING_API_KEY),
        "has_llm_key": bool(settings.LLM_API_KEY),
        "has_feishu_secret": bool(settings.FEISHU_APP_SECRET),
        "has_feishu_app_token": bool(settings.FEISHU_BITABLE_APP_TOKEN),
    }


@app.post("/api/system/settings")
async def update_settings(request: Request, payload: Dict[str, Any] = Body(...)):
    """Update process-local settings. This endpoint is deliberately loopback-only."""
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Settings can only be changed from this computer")

    allowed_text = {
        "model_provider": "MODEL_PROVIDER",
        "jimeng_default_model": "JIMENG_DEFAULT_MODEL",
        "seedance_endpoint_id": "SEEDANCE_ENDPOINT_ID",
        "llm_api_base_url": "LLM_API_BASE_URL",
        "llm_model": "LLM_MODEL",
        "feishu_sync_mode": "FEISHU_SYNC_MODE",
        "feishu_app_id": "FEISHU_APP_ID",
    }
    secret_text = {
        "jimeng_api_key": "JIMENG_API_KEY",
        "jimeng_api_secret": "JIMENG_API_SECRET",
        "seedance_ark_api_key": "SEEDANCE_ARK_API_KEY",
        "kling_api_key": "KLING_API_KEY",
        "llm_api_key": "LLM_API_KEY",
        "feishu_app_secret": "FEISHU_APP_SECRET",
        "feishu_bitable_app_token": "FEISHU_BITABLE_APP_TOKEN",
    }
    for public_name, setting_name in allowed_text.items():
        if public_name in payload:
            setattr(settings, setting_name, str(payload[public_name]).strip())
    for public_name, setting_name in secret_text.items():
        # Blank form fields mean "keep existing secret", preventing accidental deletion.
        if public_name in payload and str(payload[public_name]).strip():
            setattr(settings, setting_name, str(payload[public_name]).strip())
    if "mock_mode" in payload:
        settings.MOCK_MODE = payload["mock_mode"] is True or str(payload["mock_mode"]).lower() == "true"
    if "llm_api_style" in payload:
        style = str(payload["llm_api_style"]).strip()
        if style not in {"responses", "chat_completions"}:
            raise HTTPException(status_code=422, detail="llm_api_style must be responses or chat_completions")
        settings.LLM_API_STYLE = style
    if "feishu_sync_mode" in payload and settings.FEISHU_SYNC_MODE not in {"local", "dual", "cloud"}:
        raise HTTPException(status_code=422, detail="feishu_sync_mode must be local, dual or cloud")
    if "billing_mode" in payload:
        if payload["billing_mode"] not in {"CNY", "POINTS"}:
            raise HTTPException(status_code=422, detail="billing_mode must be CNY or POINTS")
        settings.BILLING_MODE = payload["billing_mode"]
    if "cost_per_second_cny" in payload:
        value = float(payload["cost_per_second_cny"])
        if value < 0:
            raise HTTPException(status_code=422, detail="cost_per_second_cny cannot be negative")
        settings.COST_PER_SECOND_CNY = value

    return {
        "message": "Settings updated in current process",
        "current_mock_mode": settings.MOCK_MODE,
        "model_provider": settings.MODEL_PROVIDER,
        "model": settings.JIMENG_DEFAULT_MODEL,
        "has_jimeng_key": bool(settings.JIMENG_API_KEY or settings.SEEDANCE_ARK_API_KEY),
        "has_llm_key": bool(settings.LLM_API_KEY),
    }


@app.post("/api/products/analyze", response_model=ProductAnalysis)
async def analyze_product(product_input: ProductInput):
    product = ProductAnalyzer.analyze(product_input)
    database.upsert_product(product)
    FeishuBitableSync.sync_product(product)
    return product


@app.get("/api/products/{product_id}", response_model=ProductAnalysis)
async def get_product(product_id: str):
    return _product_or_404(product_id)


@app.post("/api/video-plan/generate")
async def generate_video_plan(payload: VideoPlanRequest):
    product = _product_or_404(payload.product_id)
    if payload.template_id != PromptBuilder.DEFAULT_TEMPLATE.template_id:
        raise HTTPException(status_code=422, detail="MVP currently supports TPL_SCENE_PRODUCT_15S_V1 only")
    return {
        "product_id": product.product_id,
        "template": PromptBuilder.DEFAULT_TEMPLATE,
        "evidence_policy": {
            "information_confidence": product.information_confidence,
            "allow_unverified_claims": False,
            "real_generation_ready": bool(product.source_images),
        },
    }


@app.post("/api/prompts/compile", response_model=PromptSchemaV1)
async def compile_prompts(payload: PromptCompileRequest):
    product = _product_or_404(payload.product_id)
    schema = PromptBuilder.build_full_schema(
        product,
        version=payload.version,
        provider=payload.provider,
        model=payload.model,
    )
    database.save_prompt_schema(product.product_id, payload.version, schema)
    return schema


@app.post("/api/prompts/variants/plan", response_model=list[PromptVariant])
async def plan_prompt_variants(payload: PromptVariantPlanRequest):
    product = _product_or_404(payload.product_id)
    return PromptVariantPlanner.plan(product, payload)


@app.get("/api/prompts/variants", response_model=list[PromptVariant])
async def list_prompt_variants(product_id: str, shot_id: Optional[str] = None):
    return database.list_variants(product_id, shot_id)


@app.post("/api/video/generate", response_model=VideoTaskRecord)
async def generate_video_shot(payload: VideoGenerateRequest):
    product = _product_or_404(payload.product_id)
    if not settings.MOCK_MODE and payload.provider != "mock" and not payload.image_url:
        raise HTTPException(status_code=422, detail="Real image-to-video generation requires image_url")
    task = await JimengAdapter.submit_video_task(
        product_id=payload.product_id,
        shot_id=payload.shot_id,
        prompt=payload.prompt,
        negative_prompt=payload.negative_prompt,
        image_url=payload.image_url,
        provider=payload.provider,
        model=payload.model,
        prompt_version=payload.prompt_version,
        duration=payload.duration,
        aspect_ratio=payload.aspect_ratio,
        product_name=payload.product_name or product.product_name,
        variant_id=payload.variant_id,
    )
    FeishuBitableSync.sync_task(task)
    return task


@app.get("/api/video/tasks/{task_id}", response_model=VideoTaskRecord)
async def get_video_task(task_id: str):
    task = _task_or_404(task_id)
    FeishuBitableSync.sync_task(task)
    return task


@app.get("/api/video/tasks", response_model=list[VideoTaskRecord])
async def list_video_tasks(product_id: Optional[str] = None):
    return JimengAdapter.list_tasks(product_id)


@app.get("/api/video/tasks/{task_id}/events")
async def get_video_task_events(task_id: str):
    _task_or_404(task_id)
    return database.task_events(task_id)


@app.post("/api/video/tasks/{task_id}/qa")
async def submit_qa_evaluation(task_id: str, qa_input: QARecordInput):
    task = _task_or_404(task_id)
    if qa_input.internal_task_id != task_id or qa_input.shot_id != task.shot_id:
        raise HTTPException(status_code=422, detail="QA task_id/shot_id does not match target task")
    if task.status not in {TaskStatus.QA_PENDING, TaskStatus.REPAIR, TaskStatus.REJECTED}:
        raise HTTPException(status_code=409, detail=f"Task in {task.status.value} cannot be QA evaluated")
    unknown_codes = sorted(set(qa_input.failure_codes) - set(RepairEngine.FAILURE_CODE_MAP))
    if unknown_codes:
        raise HTTPException(status_code=422, detail={"message": "Unknown Failure Code", "codes": unknown_codes})

    total_score, qa_status, report = QAEngine.evaluate(qa_input)
    actions = RepairEngine.resolve_repair_actions(qa_input.failure_codes)
    task.qa_score = total_score
    task.qa_status = qa_status
    task.failure_codes = qa_input.failure_codes
    task.failure_notes = qa_input.failure_notes
    task.repair_actions = actions
    task.status = {
        QAStatus.PASS: TaskStatus.PASS,
        QAStatus.REPAIR: TaskStatus.REPAIR,
        QAStatus.FAIL: TaskStatus.REJECTED,
    }[qa_status]
    task.updated_at = utc_now_iso()
    database.upsert_task(task)
    database.save_qa(task, {"input": qa_input.model_dump(mode="json"), "report": report, "repair_actions": actions})
    FeishuBitableSync.sync_task(task)
    FeishuBitableSync.sync_qa_record(qa_input, total_score, qa_status.value, actions)
    return {
        "task_id": task_id,
        "task_status": task.status.value,
        "qa_score": total_score,
        "qa_status": qa_status.value,
        "failure_codes": qa_input.failure_codes,
        "repair_actions": actions,
        "next_prompt_version": RepairEngine.next_version(task.prompt_version) if qa_status != QAStatus.PASS else None,
        "report": report,
    }


async def _retry_task(task_id: str, failure_codes: list[str]) -> VideoTaskRecord:
    old_task = _task_or_404(task_id)
    codes = failure_codes or old_task.failure_codes
    if not codes:
        raise HTTPException(status_code=422, detail="Retry requires at least one Failure Code")
    if old_task.status == TaskStatus.PASS:
        raise HTTPException(status_code=409, detail="PASS task cannot be repaired")

    # A manual repair click is treated as explicit human defect classification.
    if old_task.status == TaskStatus.QA_PENDING:
        old_task.status = TaskStatus.REPAIR
        old_task.failure_codes = codes
        old_task.repair_actions = RepairEngine.resolve_repair_actions(codes)
        database.upsert_task(old_task, {"source": "manual_failure_classification"})
        FeishuBitableSync.sync_task(old_task)
    elif old_task.status not in {TaskStatus.REPAIR, TaskStatus.REJECTED, TaskStatus.FAILED}:
        raise HTTPException(status_code=409, detail=f"Task in {old_task.status.value} cannot be repaired")

    product = _product_or_404(old_task.product_id)
    repair = RepairEngine.generate_v1_1_prompt(
        product=product,
        shot_id=old_task.shot_id,
        current_version=old_task.prompt_version,
        failure_codes=codes,
    )
    new_task = await JimengAdapter.submit_video_task(
        product_id=old_task.product_id,
        shot_id=old_task.shot_id,
        prompt=repair["compiled_positive"],
        negative_prompt=repair["compiled_negative"],
        image_url=old_task.source_image,
        provider=old_task.provider,
        model=old_task.model,
        prompt_version=repair["next_version"],
        duration=old_task.duration,
        aspect_ratio=old_task.aspect_ratio,
        product_name=product.product_name,
        parent_task_id=old_task.internal_task_id,
    )
    new_task.failure_codes = codes
    new_task.repair_actions = repair["repair_actions"]
    database.upsert_task(new_task)
    FeishuBitableSync.sync_task(new_task)
    return new_task


@app.post("/api/video/tasks/{task_id}/retry", response_model=VideoTaskRecord)
async def retry_video_task(task_id: str, payload: TaskRetryRequest):
    return await _retry_task(task_id, payload.failure_codes)


@app.post("/api/video/repair", response_model=VideoTaskRecord)
async def legacy_repair_route(payload: Dict[str, Any] = Body(...)):
    task_id = payload.get("task_id")
    if not task_id:
        raise HTTPException(status_code=422, detail="task_id is required; repair always derives from an existing task")
    return await _retry_task(task_id, list(payload.get("failure_codes") or []))


@app.post("/api/video/stitch", response_model=StitchResult)
async def stitch_final_video(payload: StitchRequest):
    product = _product_or_404(payload.product_id)
    tasks = [_task_or_404(task_id) for task_id in payload.task_ids]
    if len({task.internal_task_id for task in tasks}) != 3:
        raise HTTPException(status_code=422, detail="task_ids must contain three distinct tasks")
    if {task.shot_id for task in tasks} != {"S01", "S02", "S03"}:
        raise HTTPException(status_code=422, detail="Exactly one S01, S02 and S03 task is required")
    if any(task.duration != 5 or task.aspect_ratio != "9:16" for task in tasks):
        raise HTTPException(status_code=422, detail="MVP stitching requires three 5-second 9:16 shots")
    if any(task.product_id != payload.product_id for task in tasks):
        raise HTTPException(status_code=422, detail="All tasks must belong to product_id")

    bypass = not payload.require_qa_pass
    if bypass and not settings.MOCK_MODE:
        raise HTTPException(status_code=403, detail="QA bypass is only available in Mock preview mode")
    if not bypass:
        blocked = [task.internal_task_id for task in tasks if task.status != TaskStatus.PASS]
        if blocked:
            raise HTTPException(status_code=409, detail={"message": "Only PASS tasks can be stitched", "blocked": blocked})
    missing = [task.internal_task_id for task in tasks if not task.local_video_path or not os.path.exists(task.local_video_path)]
    if missing:
        raise HTTPException(status_code=409, detail={"message": "Tasks have no archived local video", "missing": missing})

    ordered = sorted(tasks, key=lambda item: item.shot_id)
    audio_path = None
    if payload.enable_tts:
        safe_description = "，".join(product.confirmed_information)
        safe_name = product.product_name if not product.risk_information else "该商品"
        tts_result = await TTSService.generate_batch_tts(safe_name, safe_description, payload.voice)
        audio_path = tts_result.get("merged_audio_path")
    result = await asyncio.to_thread(
        StitcherService.stitch_3x5s_videos,
        [task.local_video_path for task in ordered],
        payload.product_id,
        [task.internal_task_id for task in ordered],
        [f"{task.shot_id}_V{task.prompt_version}" for task in ordered],
        audio_path,
    )
    result.qa_pass_summary = {
        "status": "MOCK_QA_BYPASS" if bypass else "ALL_SHOTS_PASSED",
        "shots_count": 3,
        "task_statuses": {task.shot_id: task.status.value for task in ordered},
    }
    database.save_delivery(result)
    FeishuBitableSync.sync_delivery(result)
    return result


@app.get("/api/metrics")
async def get_metrics(product_id: Optional[str] = None):
    return database.stats(product_id)


@app.post("/api/feishu/sync/retry")
async def retry_feishu_sync(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Feishu sync retry is local-only")
    return await asyncio.to_thread(FeishuBitableSync.retry_pending)


@app.post("/api/enhancements/product-suggestions")
async def enhance_product(payload: LLMEnhancementRequest):
    product = _product_or_404(payload.product_id)
    if not LLMEnhancer.configured():
        raise HTTPException(status_code=503, detail="Optional LLM enhancement is not configured")
    try:
        return await asyncio.to_thread(LLMEnhancer.suggest, product, payload.instruction)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM enhancement failed: {exc}") from exc


@app.get("/api/tts/voices")
async def get_tts_voices():
    return {"voices": VOICE_PRESETS}


@app.post("/api/tts/generate")
async def generate_tts(payload: Dict[str, Any] = Body(...)):
    return await TTSService.generate_batch_tts(
        payload.get("product_name", "优质好物"),
        payload.get("short_description", ""),
        payload.get("voice", "xiaoxiao"),
    )


@app.post("/api/export/jianying")
async def export_jianying_draft(payload: Dict[str, Any] = Body(...)):
    task_ids = list(payload.get("task_ids") or [])
    if len(task_ids) != 3:
        raise HTTPException(status_code=422, detail="Exactly three task_ids are required")
    tasks = [_task_or_404(task_id) for task_id in task_ids]
    if any(not task.local_video_path or not os.path.exists(task.local_video_path) for task in tasks):
        raise HTTPException(status_code=409, detail="All tasks must have archived local videos")
    product_id = str(payload.get("product_id") or tasks[0].product_id)
    product = _product_or_404(product_id)
    product_name = product.product_name if not product.risk_information else "该商品"
    tts_result = await TTSService.generate_batch_tts(
        product_name, "，".join(product.confirmed_information), payload.get("voice", "xiaoxiao")
    )
    ordered = sorted(tasks, key=lambda item: item.shot_id)
    return JianyingExporter.export_draft(
        product_name=product_name,
        video_paths=[task.local_video_path for task in ordered],
        audio_paths=[item["audio_path"] for item in tts_result["scripts"]],
        subtitles=[item["subtitle"] for item in tts_result["scripts"]],
        durations_sec=[5.0, 5.0, 5.0],
        project_title=f"AI-SVWF_{product_name}",
    )


@app.post("/api/test/run-visual-e2e")
async def run_visual_e2e_test(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Visual test runner is local-only")
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(settings.BASE_DIR / "run_ui_test.py")],
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        logs = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        success = proc.returncode == 0
    except Exception as exc:
        logs = f"Visual test failed: {exc}"
        success = False
    filenames = [
        "test_step1_home.png", "test_step2_preset.png", "test_step3_settings_modal.png",
        "test_step4_generated.png", "test_step5_repaired.png", "test_step6_stitched.png",
        "test_step7_matrix.png", "test_step8_prompt_studio.png", "test_step9_version_compare.png",
    ]
    steps = [
        {"step": index + 1, "filename": name, "exists": (settings.OUTPUT_DIR / name).exists(),
         "url": f"/outputs/{name}?t={int(time.time())}" if (settings.OUTPUT_DIR / name).exists() else None}
        for index, name in enumerate(filenames)
    ]
    return {"success": success, "logs": logs, "steps": steps}


if __name__ == "__main__":
    import uvicorn

    print(f"AI-SVWF: http://127.0.0.1:{settings.PORT}  docs: http://127.0.0.1:{settings.PORT}/docs")
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
