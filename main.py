"""AI-SVWF FastAPI service.

The REST surface mirrors section 23 of the handoff document while retaining a
small number of ``/api`` compatibility routes used by the bundled Web Studio.
"""

import asyncio
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.adapter.jimeng import JimengAdapter
from core.ark_client import ArkAPIError, ArkClient
from core.asset_manager import AssetManager, AssetValidationError
from core.config import settings
from core.database import database
from core.errors import QuotaExceededError
from core.feishu_sync import FeishuBitableSync
from core.first_frame_service import FirstFrameService
from core.jianying_exporter import JianyingExporter
from core.llm_enhancer import LLMEnhancer
from core.product_analyzer import ProductAnalyzer
from core.prompt_builder import PromptBuilder
from core.prompt_variant_planner import PromptVariantPlanner
from core.qa_engine import QAEngine
from core.repair_engine import RepairEngine
from core.schemas import (
    LLMEnhancementRequest,
    AssetRecord,
    FirstFrameRequest,
    ImageGenerationRecord,
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
    VisionAnalyzeRequest,
    utc_now_iso,
)
from core.stitcher import StitcherService
from core.tts_service import TTSService, VOICE_PRESETS
from core.vision_analyzer import VisionProductAnalyzer


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.mark_pending_vision_for_review()
    database.mark_pending_images_for_review()
    await JimengAdapter.resume_pending_tasks()
    yield


class UploadBodyTooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    """Enforce the upload cap for both Content-Length and chunked bodies."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/api/assets/images":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    response = JSONResponse(status_code=413, content={"detail": "上传请求体过大"})
                    return await response(scope, receive, send)
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
                return await response(scope, receive, send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    raise UploadBodyTooLarge
            return message

        try:
            return await self.app(scope, limited_receive, send)
        except UploadBodyTooLarge:
            response = JSONResponse(status_code=413, content={"detail": "上传请求体过大"})
            return await response(scope, receive, send)


app = FastAPI(
    title="AI-SVWF 工作流引擎",
    description="3×5s AI 带货视频工作流（本地 SQLite + 飞书协作镜像）",
    version="1.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(
    UploadSizeLimitMiddleware,
    max_bytes=settings.MAX_UPLOAD_REQUEST_MB * 1024 * 1024,
)

app.mount("/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")
app.mount("/assets", StaticFiles(directory=str(settings.ASSET_DIR)), name="assets")
static_dir = settings.BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _is_local_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    request_host = (request.url.hostname or "").lower()
    local_names = {"127.0.0.1", "::1", "localhost", "testclient", "testserver"}
    return client_host in local_names and request_host in local_names


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


def _require_local_paid_access(request: Request) -> None:
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Paid model endpoints are local-only until deployment authentication is configured",
        )


@app.get("/")
async def index():
    index_html = static_dir / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return JSONResponse({"status": "online", "doc": "/docs", "mock_mode": settings.MOCK_MODE})


@app.get("/api/system/status")
async def get_system_status():
    mirror = await asyncio.to_thread(FeishuBitableSync.get_mirror_summary)
    mirror["is_feishu_configured"] = FeishuBitableSync.configured()
    today = datetime.now(timezone.utc).date().isoformat()
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
        "ark": {
            "configured": ArkClient.configured(),
            "vision_model": settings.VISION_MODEL,
            "image_model_primary": settings.IMAGE_MODEL_PRIMARY,
            "image_model_fallback": settings.IMAGE_MODEL_FALLBACK,
            "video_model": settings.VIDEO_MODEL,
            "daily_usage": database.daily_real_task_counts(today),
            "daily_limits": {
                "vision": settings.MAX_REAL_VISION_TASKS_PER_DAY,
                "image": settings.MAX_REAL_IMAGE_TASKS_PER_DAY,
                "video": settings.MAX_REAL_VIDEO_TASKS_PER_DAY,
            },
        },
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
        "feishu_table_products": settings.FEISHU_TABLE_PRODUCTS,
        "feishu_table_tasks": settings.FEISHU_TABLE_TASKS,
        "feishu_table_qa": settings.FEISHU_TABLE_QA,
        "feishu_table_delivery": settings.FEISHU_TABLE_DELIVERY,
        "has_jimeng_key": ArkClient.configured(),
        "has_kling_key": bool(settings.KLING_API_KEY),
        "has_llm_key": bool(settings.LLM_API_KEY),
        "has_feishu_secret": bool(settings.FEISHU_APP_SECRET),
        "has_feishu_app_token": bool(settings.FEISHU_BITABLE_APP_TOKEN),
        "has_ark_key": ArkClient.configured(),
        "ark_api_base_url": settings.ARK_API_BASE_URL,
        "vision_model": settings.VISION_MODEL,
        "image_model_primary": settings.IMAGE_MODEL_PRIMARY,
        "image_model_fallback": settings.IMAGE_MODEL_FALLBACK,
        "video_model": settings.VIDEO_MODEL,
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
        "vision_model": "VISION_MODEL",
        "image_model_primary": "IMAGE_MODEL_PRIMARY",
        "image_model_fallback": "IMAGE_MODEL_FALLBACK",
        "video_model": "VIDEO_MODEL",
        "feishu_table_products": "FEISHU_TABLE_PRODUCTS",
        "feishu_table_tasks": "FEISHU_TABLE_TASKS",
        "feishu_table_qa": "FEISHU_TABLE_QA",
        "feishu_table_delivery": "FEISHU_TABLE_DELIVERY",
    }
    secret_text = {
        "jimeng_api_key": "JIMENG_API_KEY",
        "jimeng_api_secret": "JIMENG_API_SECRET",
        "seedance_ark_api_key": "SEEDANCE_ARK_API_KEY",
        "kling_api_key": "KLING_API_KEY",
        "llm_api_key": "LLM_API_KEY",
        "feishu_app_secret": "FEISHU_APP_SECRET",
        "feishu_bitable_app_token": "FEISHU_BITABLE_APP_TOKEN",
        "ark_api_key": "ARK_API_KEY",
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
        "has_jimeng_key": ArkClient.configured(),
        "has_llm_key": bool(settings.LLM_API_KEY),
    }


@app.post("/api/products/analyze", response_model=ProductAnalysis)
async def analyze_product(product_input: ProductInput):
    product = ProductAnalyzer.analyze(product_input)
    database.upsert_product(product)
    await asyncio.to_thread(FeishuBitableSync.sync_product, product)
    return product


@app.post("/api/assets/images", response_model=List[AssetRecord])
async def upload_product_images(request: Request, files: List[UploadFile] = File(...)):
    """Validate, de-duplicate and persist local product reference images."""
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Product uploads are local-only until authentication is configured")
    if not files or len(files) > settings.MAX_ASSETS_PER_PRODUCT:
        raise HTTPException(
            status_code=422,
            detail=f"每次需上传 1 到 {settings.MAX_ASSETS_PER_PRODUCT} 张商品图片",
        )
    pending: list[tuple[bytes, str]] = []
    for upload in files:
        try:
            content = await upload.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
            AssetManager.validate_image(content)
            pending.append((content, upload.filename or "upload"))
        except AssetValidationError as exc:
            raise HTTPException(status_code=422, detail=f"{upload.filename}: {exc}") from exc
        finally:
            await upload.close()
    return [AssetManager.save_image(content, name) for content, name in pending]


@app.get("/api/assets/{asset_id}", response_model=AssetRecord)
async def get_product_asset(request: Request, asset_id: str):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Product assets are local-only until authentication is configured")
    asset = database.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    return asset


@app.post("/api/products/analyze-vision", response_model=ProductAnalysis)
async def analyze_product_images(request: Request, payload: VisionAnalyzeRequest):
    _require_local_paid_access(request)
    assets = database.get_assets(payload.asset_ids)
    found = {asset.asset_id for asset in assets}
    missing = [asset_id for asset_id in payload.asset_ids if asset_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail={"message": "Asset not found", "asset_ids": missing})
    try:
        product = await asyncio.to_thread(VisionProductAnalyzer.analyze, payload, assets)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ArkAPIError as exc:
        review_codes = {
            "ARK_VISION_SUBMISSION_UNCERTAIN",
            "ARK_VISION_RESULT_REVIEW_REQUIRED",
            "RESUME_REQUIRES_REVIEW",
        }
        error_code = exc.error_code or (
            "ARK_VISION_SUBMISSION_UNCERTAIN" if exc.status_code == 0 else "ARK_VISION_ANALYSIS_FAILED"
        )
        submission_uncertain = exc.status_code == 0 or error_code in review_codes
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "error_code": error_code,
                "submission_uncertain": submission_uncertain,
                "retry_allowed": not submission_uncertain,
            },
        ) from exc
    await asyncio.to_thread(FeishuBitableSync.sync_product, product)
    return product


@app.get("/api/system/models")
async def get_available_ark_models(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Model probing is local-only")
    try:
        model_ids = await asyncio.to_thread(ArkClient.list_models)
    except ArkAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    enabled = [
        item for item in model_ids
        if any(token in item.lower() for token in ("glm-5-3", "seedream", "seedance-2-0"))
    ]
    return {"models": enabled, "configured_models": {
        "vision": settings.VISION_MODEL,
        "image_primary": settings.IMAGE_MODEL_PRIMARY,
        "image_fallback": settings.IMAGE_MODEL_FALLBACK,
        "video": settings.VIDEO_MODEL,
    }}


@app.post("/api/images/first-frame", response_model=ImageGenerationRecord)
async def generate_first_frame(request: Request, payload: FirstFrameRequest):
    _require_local_paid_access(request)
    product = _product_or_404(payload.product_id)
    if not (payload.asset_ids or product.source_asset_ids or product.source_images):
        raise HTTPException(status_code=422, detail="真实首帧生成需要至少一张商品参考图")
    if payload.asset_ids:
        found = {asset.asset_id for asset in database.get_assets(payload.asset_ids)}
        missing = [asset_id for asset_id in payload.asset_ids if asset_id not in found]
        if missing:
            raise HTTPException(status_code=404, detail={"message": "Asset not found", "asset_ids": missing})
    try:
        return await asyncio.to_thread(FirstFrameService.generate, payload, product)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.get("/api/images/tasks/{image_task_id}", response_model=ImageGenerationRecord)
async def get_image_task(image_task_id: str):
    record = database.get_image_generation(image_task_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Image task not found: {image_task_id}")
    return record


@app.get("/api/images/tasks", response_model=list[ImageGenerationRecord])
async def list_image_tasks(product_id: Optional[str] = None):
    return database.list_image_generations(product_id)


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
            **PromptBuilder.confidence_policy(product),
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
async def generate_video_shot(request: Request, payload: VideoGenerateRequest):
    product = _product_or_404(payload.product_id)
    if payload.provider.strip().lower() != "mock":
        _require_local_paid_access(request)
    if payload.provider.strip().lower() != "mock" and not payload.image_url:
        raise HTTPException(status_code=422, detail="Real image-to-video generation requires image_url")
    try:
        task = await JimengAdapter.submit_video_task(
            product_id=payload.product_id,
            shot_id=payload.shot_id,
            prompt=PromptBuilder.apply_confidence_policy(payload.prompt, product),
            negative_prompt=payload.negative_prompt,
            image_url=payload.image_url,
            provider=payload.provider,
            model=payload.model,
            prompt_version=payload.prompt_version,
            duration=payload.duration,
            aspect_ratio=payload.aspect_ratio,
            product_name=payload.product_name or product.product_name,
            variant_id=payload.variant_id,
            idempotency_key=payload.idempotency_key,
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (ArkAPIError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await asyncio.to_thread(FeishuBitableSync.sync_task, task)
    return task


@app.get("/api/video/tasks/{task_id}", response_model=VideoTaskRecord)
async def get_video_task(task_id: str):
    task = _task_or_404(task_id)
    JimengAdapter.resume_task_if_needed(task_id)
    await asyncio.to_thread(FeishuBitableSync.sync_task, task)
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
    await asyncio.to_thread(FeishuBitableSync.sync_task, task)
    await asyncio.to_thread(
        FeishuBitableSync.sync_qa_record,
        qa_input,
        total_score,
        qa_status.value,
        actions,
    )
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
    unknown_codes = sorted(set(failure_codes) - set(RepairEngine.FAILURE_CODE_MAP))
    if unknown_codes:
        raise HTTPException(status_code=422, detail={"message": "Unknown Failure Code", "codes": unknown_codes})
    if failure_codes and set(failure_codes) != set(old_task.failure_codes):
        raise HTTPException(
            status_code=422,
            detail="Repair must use the Failure Codes saved by the task's human QA record",
        )
    codes = old_task.failure_codes
    if not codes:
        raise HTTPException(status_code=422, detail="Repair requires Failure Codes saved by human QA")
    stored_unknown_codes = sorted(set(codes) - set(RepairEngine.FAILURE_CODE_MAP))
    if stored_unknown_codes:
        raise HTTPException(
            status_code=422,
            detail={"message": "Stored QA contains unknown Failure Code", "codes": stored_unknown_codes},
        )
    if old_task.qa_score is None or old_task.qa_status is None:
        raise HTTPException(status_code=409, detail="Complete and save human QA before repair")
    if old_task.status not in {TaskStatus.REPAIR, TaskStatus.REJECTED}:
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
    await asyncio.to_thread(FeishuBitableSync.sync_task, new_task)
    return new_task


@app.post("/api/video/tasks/{task_id}/retry", response_model=VideoTaskRecord)
async def retry_video_task(request: Request, task_id: str, payload: TaskRetryRequest):
    _require_local_paid_access(request)
    return await _retry_task(task_id, payload.failure_codes)


@app.post("/api/video/repair", response_model=VideoTaskRecord)
async def legacy_repair_route(request: Request, payload: Dict[str, Any] = Body(...)):
    _require_local_paid_access(request)
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
    if len({task.execution_mode for task in tasks}) != 1:
        raise HTTPException(status_code=422, detail="Mock and real tasks cannot be mixed in one delivery")

    bypass = not payload.require_qa_pass
    if bypass and (not settings.MOCK_MODE or any(task.execution_mode != "mock" for task in tasks)):
        raise HTTPException(status_code=403, detail="QA bypass is only available for Mock tasks in Mock preview mode")
    if not bypass:
        blocked = [task.internal_task_id for task in tasks if task.status != TaskStatus.PASS]
        if blocked:
            raise HTTPException(status_code=409, detail={"message": "Only PASS tasks can be stitched", "blocked": blocked})
    missing = [task.internal_task_id for task in tasks if not task.local_video_path or not os.path.exists(task.local_video_path)]
    if missing:
        raise HTTPException(status_code=409, detail={"message": "Tasks have no archived local video", "missing": missing})

    ordered = sorted(tasks, key=lambda item: item.shot_id)
    audio_path = None
    tts_result: Optional[Dict[str, Any]] = None
    if payload.enable_tts:
        safe_description = "，".join(product.confirmed_information)
        safe_name = product.product_name if not product.risk_information else "该商品"
        try:
            tts_result = await TTSService.generate_batch_tts(safe_name, safe_description, payload.voice)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        audio_path = tts_result.get("merged_audio_path")
    try:
        result = await asyncio.to_thread(
            StitcherService.stitch_3x5s_videos,
            [task.local_video_path for task in ordered],
            payload.product_id,
            [task.internal_task_id for task in ordered],
            [f"{task.shot_id}_V{task.prompt_version}" for task in ordered],
            audio_path,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result.qa_pass_summary = {
        **result.qa_pass_summary,
        "status": "MOCK_QA_BYPASS" if bypass else "ALL_SHOTS_PASSED",
        "shots_count": 3,
        "task_statuses": {task.shot_id: task.status.value for task in ordered},
        "tts": {
            "enabled": payload.enable_tts,
            "available": bool(tts_result and tts_result.get("tts_available")),
            "degraded": bool(tts_result and tts_result.get("degraded")),
            "voice": tts_result.get("voice") if tts_result else None,
        },
    }
    database.save_delivery(result)
    await asyncio.to_thread(FeishuBitableSync.sync_delivery, result)
    return result


@app.get("/api/metrics")
async def get_metrics(product_id: Optional[str] = None, execution_mode: Optional[str] = None):
    if execution_mode not in {None, "mock", "real"}:
        raise HTTPException(status_code=422, detail="execution_mode must be mock or real")
    return database.stats(product_id, execution_mode)


@app.post("/api/feishu/sync/retry")
async def retry_feishu_sync(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Feishu sync retry is local-only")
    return await asyncio.to_thread(FeishuBitableSync.retry_pending)


@app.post("/api/enhancements/product-suggestions")
async def enhance_product(request: Request, payload: LLMEnhancementRequest):
    _require_local_paid_access(request)
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
async def generate_tts(request: Request, payload: Dict[str, Any] = Body(...)):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="TTS generation is local-only until authentication is configured")
    try:
        return await TTSService.generate_batch_tts(
            str(payload.get("product_name", "优质好物"))[:200],
            str(payload.get("short_description", ""))[:4000],
            str(payload.get("voice", "xiaoxiao"))[:64],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/export/jianying")
async def export_jianying_draft(request: Request, payload: Dict[str, Any] = Body(...)):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Jianying export is local-only until authentication is configured")
    task_ids = list(payload.get("task_ids") or [])
    if len(task_ids) != 3 or len(set(task_ids)) != 3:
        raise HTTPException(status_code=422, detail="Exactly three distinct task_ids are required")
    tasks = [_task_or_404(task_id) for task_id in task_ids]
    product_id = str(payload.get("product_id") or tasks[0].product_id)
    product = _product_or_404(product_id)
    if any(task.product_id != product_id for task in tasks):
        raise HTTPException(status_code=422, detail="All tasks must belong to product_id")
    if {task.shot_id for task in tasks} != {"S01", "S02", "S03"}:
        raise HTTPException(status_code=422, detail="Exactly one S01, S02 and S03 task is required")
    if len({task.execution_mode for task in tasks}) != 1:
        raise HTTPException(status_code=422, detail="Mock and real tasks cannot be mixed in one draft")
    if any(task.duration != 5 or task.aspect_ratio != "9:16" for task in tasks):
        raise HTTPException(status_code=422, detail="Jianying draft requires three 5-second 9:16 shots")
    if tasks[0].execution_mode == "real":
        blocked = [task.internal_task_id for task in tasks if task.status != TaskStatus.PASS]
        if blocked:
            raise HTTPException(
                status_code=409,
                detail={"message": "Real Jianying delivery requires PASS tasks", "blocked": blocked},
            )
    elif not settings.MOCK_MODE:
        raise HTTPException(status_code=403, detail="Mock drafts are only available in Mock preview mode")
    if any(not task.local_video_path or not os.path.exists(task.local_video_path) for task in tasks):
        raise HTTPException(status_code=409, detail="All tasks must have archived local videos")
    product_name = product.product_name if not product.risk_information else "该商品"
    try:
        tts_result = await TTSService.generate_batch_tts(
            product_name,
            "，".join(product.confirmed_information),
            str(payload.get("voice", "xiaoxiao"))[:64],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    ordered = sorted(tasks, key=lambda item: item.shot_id)
    result = JianyingExporter.export_draft(
        product_name=product_name,
        video_paths=[task.local_video_path for task in ordered],
        audio_paths=[item["audio_path"] for item in tts_result["scripts"]],
        subtitles=[item["subtitle"] for item in tts_result["scripts"]],
        durations_sec=[5.0, 5.0, 5.0],
        project_title=f"AI-SVWF_{product_name}",
    )
    result["delivery_basis"] = "ALL_SHOTS_PASSED" if tasks[0].execution_mode == "real" else "MOCK_PREVIEW"
    return result


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
        "test_step7_matrix.png", "test_step8_prompt_studio.png", "test_step8_prompt_studio_s01.png",
        "test_step9_version_compare.png",
        "test_step10_restored.png",
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
