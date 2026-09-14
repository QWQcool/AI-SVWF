import asyncio
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import httpx
import pytest

from core.adapter.jimeng import JimengAdapter
from core.ark_client import ArkAPIError, ArkClient
from core.asset_manager import AssetManager
from core.config import settings
from core.database import WorkflowDatabase, database
from core.jianying_exporter import JianyingExporter
from core.first_frame_service import FirstFrameService
from core.schemas import (
    AssetRecord,
    FirstFrameRequest,
    ImageGenerationRecord,
    ProductAnalysis,
    TaskStatus,
    VideoTaskRecord,
    VisionAnalyzeRequest,
)
from core.vision_analyzer import VisionProductAnalyzer
from main import UploadSizeLimitMiddleware, app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_submitted_vision_is_quarantined_after_restart(tmp_path):
    local_database = WorkflowDatabase(tmp_path / "vision-recovery.sqlite3")
    fingerprint = f"vision-{uuid4().hex}"
    reservation = local_database.reserve_vision_analysis(
        product_id="PROD_CRASHED",
        model="glm-test",
        fingerprint=fingerprint,
        asset_ids=["ASSET_001"],
        daily_limit=10,
    )
    assert reservation["result"] == "reserved"

    assert local_database.mark_pending_vision_for_review() == 1
    assert local_database.mark_pending_vision_for_review() == 0

    existing = local_database.reserve_vision_analysis(
        product_id="PROD_RETRY",
        model="glm-test",
        fingerprint=fingerprint,
        asset_ids=["ASSET_001"],
        daily_limit=10,
    )
    assert existing["result"] == "existing"
    assert existing["status"] == "RESUME_REQUIRES_REVIEW"
    payload = json.loads(existing["payload_json"])
    assert payload["error_code"] == "RESUME_REQUIRES_REVIEW"
    assert "未自动重放" in payload["error_message"]


def test_submitted_image_recovery_remains_schema_readable(tmp_path):
    local_database = WorkflowDatabase(tmp_path / "image-recovery.sqlite3")
    record = ImageGenerationRecord(
        image_task_id=f"IMG_{uuid4().hex[:10]}",
        product_id="PROD_CRASHED",
        shot_id="S01",
        model="seedream-test",
        prompt_version="1.0",
        prompt_text="首帧测试",
        request_fingerprint=uuid4().hex,
        status="SUBMITTED",
    )
    local_database.upsert_image_generation(record)

    assert local_database.mark_pending_images_for_review() == 1
    restored = local_database.get_image_generation(record.image_task_id)
    assert restored is not None
    assert restored.status == "FAILED"
    assert restored.error_code == "RESUME_REQUIRES_REVIEW"
    assert "未自动重放" in restored.error_message


def test_visual_model_low_confidence_is_not_artificially_raised(monkeypatch, tmp_path):
    asset_id = f"ASSET_{uuid4().hex[:10]}"
    asset = AssetRecord(
        asset_id=asset_id,
        sha256=uuid4().hex * 2,
        original_name="low-confidence.png",
        stored_name="low-confidence.png",
        mime_type="image/png",
        width=320,
        height=480,
        size_bytes=100,
        local_path=str(tmp_path / "low-confidence.png"),
        url="/assets/low-confidence.png",
    )

    def fake_vision(*args, **kwargs):  # noqa: ARG001
        return {
            "product_name": "无法完全辨认的商品",
            "category": "日常消费品",
            "appearance_description": "主体被遮挡",
            "observed_information": ["只能看到包装轮廓"],
            "visible_text": [],
            "packaging_claims": [],
            "model_inferences": [],
            "usage_scenes": [],
            "risk_information": [],
            "vision_notes": ["图片大面积遮挡，内容待确认"],
            "confidence": 0.31,
        }

    monkeypatch.setattr(ArkClient, "vision_json", staticmethod(fake_vision))
    product = VisionProductAnalyzer.analyze(
        VisionAnalyzeRequest(
            asset_ids=[asset_id],
            idempotency_key=f"low-confidence-{uuid4().hex}",
        ),
        [asset],
    )

    assert product.information_confidence == pytest.approx(0.31)


def test_uncertain_first_frame_submission_is_cached_for_manual_review(monkeypatch):
    calls = 0

    def uncertain_submission(*args, **kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise ArkAPIError("synthetic connection reset", status_code=0)

    monkeypatch.setattr(ArkClient, "generate_image", staticmethod(uncertain_submission))
    monkeypatch.setattr(settings, "MAX_REAL_IMAGE_TASKS_PER_DAY", 1000)
    product = ProductAnalysis(
        product_id=f"PROD_FRAME_{uuid4().hex[:8]}",
        product_name="首帧不确定态测试",
        source_images=["https://example.invalid/product.png"],
    )
    request = FirstFrameRequest(
        product_id=product.product_id,
        shot_id="S01",
        prompt_version="1.0",
        idempotency_key=f"frame-uncertain-{uuid4().hex}",
    )

    first = FirstFrameService.generate(request, product)
    second = FirstFrameService.generate(request, product)

    assert calls == 1
    assert second.image_task_id == first.image_task_id
    assert first.status == "FAILED"
    assert first.error_code == "ARK_IMAGE_SUBMISSION_UNCERTAIN"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for frontend policy checks")
def test_frontend_paid_video_retry_policy_cannot_be_bypassed_by_batch_mode():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    node_script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const context = { document: { addEventListener: () => {} }, console };
vm.createContext(context);
vm.runInContext(source, context);
const result = vm.runInContext(`JSON.stringify({
  batchUncertain: videoRetryConfirmationKind({
    mockMode: false,
    skipCostConfirmation: true,
    priorTask: {
      product_id: "PROD_1",
      prompt_version: "1.0",
      error_code: "ARK_SUBMISSION_UNCERTAIN"
    },
    productId: "PROD_1",
    version: "1.0"
  }),
  batchNormal: videoRetryConfirmationKind({
    mockMode: false,
    skipCostConfirmation: true,
    priorTask: null,
    productId: "PROD_1",
    version: "1.0"
  }),
  lostResponse: isAmbiguousVideoSubmissionFailure({
    mockMode: false,
    postStarted: true,
    submissionAcknowledged: false,
    responseStatus: undefined
  }),
  serverError: isAmbiguousVideoSubmissionFailure({
    mockMode: false,
    postStarted: true,
    submissionAcknowledged: false,
    responseStatus: 503
  }),
  validationError: isAmbiguousVideoSubmissionFailure({
    mockMode: false,
    postStarted: true,
    submissionAcknowledged: false,
    responseStatus: 422
  }),
  pollFailureAfterAck: isAmbiguousVideoSubmissionFailure({
    mockMode: false,
    postStarted: true,
    submissionAcknowledged: true,
    responseStatus: undefined
  })
})`, context);
process.stdout.write(result);
"""
    completed = subprocess.run(
        [shutil.which("node"), "-", str(app_js)],
        input=node_script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    policy = json.loads(completed.stdout)

    assert policy == {
        "batchUncertain": "manual-review",
        "batchNormal": None,
        "lostResponse": True,
        "serverError": True,
        "validationError": False,
        "pollFailureAfterAck": False,
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for frontend policy checks")
def test_frontend_lost_video_post_preserves_the_submitted_attempt_key():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    node_script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const values = {
  promptS01: { value: "稳定提示词" },
  productName: { value: "测试商品" },
  productImageUrl: { value: "/assets/product.png" }
};
const elements = new Map();
const element = id => {
  if (!elements.has(id)) {
    elements.set(id, Object.assign({
      className: "", innerText: "", textContent: "", value: "", style: {},
      load: () => {}, removeAttribute: () => {}
    }, values[id] || {}));
  }
  return elements.get(id);
};
const storage = new Map();
let fetchCalls = 0;
const context = {
  document: {
    addEventListener: () => {},
    getElementById: element
  },
  window: { confirm: () => true },
  console: { error: () => {}, warn: () => {}, log: () => {} },
  sessionStorage: {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key)
  },
  localStorage: {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {}
  },
  fetch: async () => {
    fetchCalls += 1;
    throw new TypeError("synthetic lost POST response");
  },
  setTimeout,
  clearTimeout
};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`
  showToast = () => {};
  isMockMode = false;
  currentVideoProvider = "volcengine";
  currentVideoModel = "seedance-test";
  currentProductId = "PROD_1";
  workspaceEpoch = 7;
  currentFirstFrames.S01 = {
    image_task_id: "IMG_1",
    product_id: "PROD_1",
    prompt_version: "1.0",
    image_url: "/outputs/frame.png"
  };
  currentTasks.S01 = {
    product_id: "PROD_1",
    prompt_version: "1.0",
    source_image: "/outputs/frame.png",
    status: "FAILED",
    error_code: "ARK_PROVIDER_FAILED"
  };
`, context);
(async () => {
  await vm.runInContext(`generateSingleShot("S01", "1.0", true)`, context);
  const base = "PROD_1:S01:1.0:IMG_1";
  process.stdout.write(JSON.stringify({
    fetchCalls,
    attempt: storage.get(`video-attempt:${base}`),
    requiresReview: storage.get(`video-review:${base}`),
    status: element("statusS01").innerText
  }));
})().catch(error => {
  process.stderr.write(error.stack || String(error));
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        [shutil.which("node"), "-", str(app_js)],
        input=node_script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 1,
        # The prior definitive failure advances a0 -> a1 before this POST.
        # Losing this POST must not advance again to a2.
        "attempt": "1",
        "requiresReview": "1",
        "status": "需人工复核",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for frontend policy checks")
def test_frontend_discards_stale_analysis_and_compile_responses():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    node_script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const elements = new Map();
const element = id => {
  if (!elements.has(id)) {
    elements.set(id, {
      value: "", disabled: false, innerHTML: "", innerText: "", textContent: "",
      className: "", style: {}, load: () => {}, removeAttribute: () => {}
    });
  }
  return elements.get(id);
};
const sessionValues = new Map();
const context = {
  document: {
    addEventListener: () => {},
    getElementById: element,
    querySelector: () => null,
    querySelectorAll: () => []
  },
  window: { confirm: () => true },
  console: { error: () => {}, warn: () => {}, log: () => {} },
  sessionStorage: {
    getItem: key => sessionValues.has(key) ? sessionValues.get(key) : null,
    setItem: (key, value) => sessionValues.set(key, String(value)),
    removeItem: key => sessionValues.delete(key)
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  setTimeout,
  clearTimeout,
  renderCount: 0,
  fillCount: 0
};
const response = payload => ({ ok: true, status: 200, json: async () => payload });
const oldProduct = {
  product_id: "PROD_OLD",
  product_name: "old-product",
  confirmed_information: [],
  source_images: [],
  information_confidence: 0.8
};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`
  renderAnalysisResult = () => { renderCount += 1; };
  fillPromptCards = () => { fillCount += 1; };
  updateAgentStep = () => {};
  showToast = () => {};
`, context);

(async () => {
  // A GLM response resolving after a product/asset switch must not be applied.
  element("productName").value = "old-input";
  element("productDesc").value = "old-description";
  element("preferredScene").value = "old-scene";
  element("productImageUrl").value = "/assets/old.png";
  vm.runInContext(`
    workspaceEpoch = 10;
    activeAnalysisRequestId = 0;
    isMockMode = false;
    currentProductId = null;
    uploadedAssets = [{ asset_id: "ASSET_OLD", url: "/assets/old.png" }];
  `, context);
  let resolveVision;
  const firstUrls = [];
  context.fetch = url => {
    firstUrls.push(String(url));
    return new Promise(resolve => { resolveVision = resolve; });
  };
  const visionPromise = vm.runInContext(`analyzeAndCompile(true)`, context);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext(`
    workspaceEpoch += 1;
    currentProductId = "PROD_NEW";
    uploadedAssets = [{ asset_id: "ASSET_NEW", url: "/assets/new.png" }];
  `, context);
  element("productName").value = "new-input";
  element("productImageUrl").value = "/assets/new.png";
  resolveVision(response(oldProduct));
  await visionPromise;
  const productAfterStaleVision = vm.runInContext(`currentProductId`, context);
  const renderAfterStaleVision = context.renderCount;

  // A compile response resolving after another switch must not replace prompts.
  context.renderCount = 0;
  context.fillCount = 0;
  element("productName").value = "compile-input";
  element("productDesc").value = "compile-description";
  element("preferredScene").value = "compile-scene";
  element("productImageUrl").value = "";
  vm.runInContext(`
    workspaceEpoch = 20;
    activeAnalysisRequestId = 0;
    isMockMode = true;
    currentProductId = null;
    uploadedAssets = [];
  `, context);
  let resolveCompile;
  const secondUrls = [];
  context.fetch = url => {
    secondUrls.push(String(url));
    if (secondUrls.length === 1) return Promise.resolve(response(oldProduct));
    return new Promise(resolve => { resolveCompile = resolve; });
  };
  const compilePromise = vm.runInContext(`analyzeAndCompile(true)`, context);
  for (let index = 0; index < 10 && !resolveCompile; index += 1) {
    await new Promise(resolve => setImmediate(resolve));
  }
  if (!resolveCompile) throw new Error("compile request was not reached");
  vm.runInContext(`
    workspaceEpoch += 1;
    currentProductId = "PROD_NEW";
    uploadedAssets = [{ asset_id: "ASSET_NEW", url: "/assets/new.png" }];
  `, context);
  element("productName").value = "newer-input";
  resolveCompile(response({ shots: [{}, {}, {}] }));
  await compilePromise;

  process.stdout.write(JSON.stringify({
    firstRequestCount: firstUrls.length,
    productAfterStaleVision,
    renderAfterStaleVision,
    secondRequestCount: secondUrls.length,
    productAfterStaleCompile: vm.runInContext(`currentProductId`, context),
    renderBeforeStaleCompile: context.renderCount,
    fillAfterStaleCompile: context.fillCount
  }));
})().catch(error => {
  process.stderr.write(error.stack || String(error));
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        [shutil.which("node"), "-", str(app_js)],
        input=node_script,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {
        "firstRequestCount": 1,
        "productAfterStaleVision": "PROD_NEW",
        "renderAfterStaleVision": 0,
        "secondRequestCount": 2,
        "productAfterStaleCompile": "PROD_NEW",
        "renderBeforeStaleCompile": 1,
        "fillAfterStaleCompile": 0,
    }


def test_uncertain_vision_submission_is_not_automatically_replayed(monkeypatch, tmp_path):
    calls = 0

    def uncertain_submission(*args, **kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise ArkAPIError("synthetic timeout", status_code=0)

    monkeypatch.setattr(ArkClient, "vision_json", staticmethod(uncertain_submission))
    monkeypatch.setattr(settings, "MAX_REAL_VISION_TASKS_PER_DAY", 1000)
    asset_id = f"ASSET_UNCERTAIN_{uuid4().hex[:8]}"
    asset = AssetRecord(
        asset_id=asset_id,
        sha256=uuid4().hex * 2,
        original_name="uncertain.png",
        stored_name="uncertain.png",
        mime_type="image/png",
        width=256,
        height=256,
        size_bytes=100,
        local_path=str(tmp_path / "uncertain.png"),
        url="/assets/uncertain.png",
    )
    request = VisionAnalyzeRequest(
        asset_ids=[asset_id],
        idempotency_key=f"vision-uncertain-{uuid4().hex}",
    )

    with pytest.raises(ArkAPIError) as first_error:
        VisionProductAnalyzer.analyze(request, [asset])
    with pytest.raises(ArkAPIError) as second_error:
        VisionProductAnalyzer.analyze(request, [asset])

    assert calls == 1
    assert first_error.value.error_code == "ARK_VISION_SUBMISSION_UNCERTAIN"
    assert second_error.value.error_code == "ARK_VISION_SUBMISSION_UNCERTAIN"


@pytest.mark.anyio
async def test_uncertain_video_submission_reuses_same_reserved_task(monkeypatch):
    calls = 0

    def uncertain_submission(*args, **kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise ArkAPIError("synthetic connection reset", status_code=0)

    monkeypatch.setattr(ArkClient, "configured", staticmethod(lambda: True))
    monkeypatch.setattr(ArkClient, "create_video_task", staticmethod(uncertain_submission))
    monkeypatch.setattr(settings, "MAX_REAL_VIDEO_TASKS_PER_DAY", 1000)
    idempotency_key = f"video-uncertain-{uuid4().hex}"
    kwargs = {
        "product_id": f"PROD_VIDEO_{uuid4().hex[:8]}",
        "shot_id": "S01",
        "prompt": "视频不确定态测试",
        "image_url": "https://example.invalid/frame.png",
        "provider": "volcengine",
        "model": "doubao-seedance-test",
        "idempotency_key": idempotency_key,
    }

    first = await JimengAdapter.submit_video_task(**kwargs)
    for _ in range(50):
        await asyncio.sleep(0.02)
        failed = JimengAdapter.get_task(first.internal_task_id)
        if failed and failed.status == TaskStatus.FAILED:
            break
    second = await JimengAdapter.submit_video_task(**kwargs)

    assert calls == 1
    assert second.internal_task_id == first.internal_task_id
    assert second.status == TaskStatus.FAILED
    assert second.error_code == "ARK_SUBMISSION_UNCERTAIN"


def test_real_video_quota_reservation_is_atomic_across_connections(tmp_path):
    database_path = tmp_path / "video-quota.sqlite3"
    first_database = WorkflowDatabase(database_path)
    second_database = WorkflowDatabase(database_path)
    fingerprint_prefix = uuid4().hex
    start_together = Barrier(2)

    def reserve(local_database: WorkflowDatabase, index: int):
        task = VideoTaskRecord(
            internal_task_id=f"TASK_ATOMIC_{index}_{uuid4().hex[:8]}",
            product_id="PROD_ATOMIC",
            shot_id=("S01", "S02")[index],
            provider="volcengine",
            model="seedance-test",
            execution_mode="real",
            request_fingerprint=f"{fingerprint_prefix}-{index}",
            prompt_text="并发预约测试",
            source_image="/assets/frame.png",
            status=TaskStatus.CREATED,
        )
        start_together.wait(timeout=5)
        outcome, stored = local_database.reserve_real_video_task(task, daily_limit=1)
        return outcome, stored

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(reserve, first_database, 0),
            executor.submit(reserve, second_database, 1),
        ]
        results = [future.result() for future in futures]

    assert sorted(outcome for outcome, _ in results) == ["quota", "reserved"]
    reserved = next(stored for outcome, stored in results if outcome == "reserved")
    assert reserved is not None
    assert reserved.status == TaskStatus.SUBMITTED
    assert first_database.daily_real_task_counts(reserved.created_at[:10])["video"] == 1
    assert [event["status"] for event in first_database.task_events(reserved.internal_task_id)] == [
        "CREATED",
        "SUBMITTED",
    ]


@pytest.mark.anyio
async def test_mock_global_mode_cannot_bypass_qa_for_real_tasks(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_MODE", True)
    product_response_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=product_response_transport, base_url="http://testserver"
    ) as client:
        product_response = await client.post(
            "/api/products/analyze",
            json={"product_name": f"Real任务门禁-{uuid4().hex[:8]}", "product_images": []},
        )
        assert product_response.status_code == 200, product_response.text
        product_id = product_response.json()["product_id"]

        task_ids = []
        for shot_id in ("S01", "S02", "S03"):
            task_id = f"TASK_REAL_GUARD_{uuid4().hex[:12]}"
            task_ids.append(task_id)
            database.upsert_task(
                VideoTaskRecord(
                    internal_task_id=task_id,
                    product_id=product_id,
                    shot_id=shot_id,
                    provider="volcengine",
                    model="doubao-seedance-test",
                    execution_mode="real",
                    prompt_text="真实任务测试提示词",
                    duration=5,
                    aspect_ratio="9:16",
                    status=TaskStatus.QA_PENDING,
                )
            )

        response = await client.post(
            "/api/video/stitch",
            json={
                "product_id": product_id,
                "task_ids": task_ids,
                "enable_tts": False,
                "require_qa_pass": False,
            },
        )
        assert response.status_code == 403, response.text
        assert "Mock tasks" in response.json()["detail"]


@pytest.mark.anyio
async def test_video_duration_below_provider_minimum_is_rejected():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/video/generate",
            json={
                "product_id": "PROD_NOT_REACHED",
                "shot_id": "S01",
                "provider": "mock",
                "model": "mock-video-v1",
                "prompt": "有效提示词",
                "duration": 3,
                "aspect_ratio": "9:16",
            },
        )
    assert response.status_code == 422
    assert any(error["loc"][-1] == "duration" for error in response.json()["detail"])


@pytest.mark.anyio
async def test_sub_half_confidence_compiles_display_only_without_efficacy_copy():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        analyzed = await client.post(
            "/api/products/analyze",
            json={
                "product_name": f"治疗效果待核实商品-{uuid4().hex[:6]}",
                "product_images": [],
            },
        )
        assert analyzed.status_code == 200, analyzed.text
        product = analyzed.json()
        assert product["information_confidence"] < 0.5

        compiled = await client.post(
            "/api/prompts/compile",
            json={"product_id": product["product_id"], "version": "1.0"},
        )
        assert compiled.status_code == 200, compiled.text
        schema = compiled.json()

    assert schema["evidence"]["confidence_policy"]["level"] == "display_only"
    assert schema["video_strategy"]["primary_selling_point"] == "商品外观展示"
    prompts = "\n".join(shot["prompt"] for shot in schema["shots"])
    assert "仅展示商品外观" in prompts
    assert "不生成任何功效型文案" in prompts
    assert "简单拿起展示" in prompts


@pytest.mark.anyio
async def test_chunked_upload_is_limited_without_content_length():
    async def consuming_app(scope, receive, send):  # noqa: ARG001
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    chunks = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = UploadSizeLimitMiddleware(consuming_app, max_bytes=5)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/assets/images",
            "headers": [(b"transfer-encoding", b"chunked")],
        },
        receive,
        send,
    )

    response_start = next(message for message in sent if message["type"] == "http.response.start")
    assert response_start["status"] == 413


@pytest.mark.anyio
async def test_mock_and_real_tasks_cannot_be_mixed_in_delivery():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product_response = await client.post(
            "/api/products/analyze",
            json={"product_name": f"混合模式门禁-{uuid4().hex[:8]}", "product_images": []},
        )
        product_id = product_response.json()["product_id"]
        task_ids = []
        for index, shot_id in enumerate(("S01", "S02", "S03")):
            task_id = f"TASK_MIXED_{uuid4().hex[:12]}"
            task_ids.append(task_id)
            execution_mode = "real" if index == 2 else "mock"
            database.upsert_task(
                VideoTaskRecord(
                    internal_task_id=task_id,
                    product_id=product_id,
                    shot_id=shot_id,
                    provider="volcengine" if execution_mode == "real" else "mock",
                    model="test-model",
                    execution_mode=execution_mode,
                    prompt_text="混合模式门禁",
                    status=TaskStatus.QA_PENDING,
                )
            )

        response = await client.post(
            "/api/video/stitch",
            json={
                "product_id": product_id,
                "task_ids": task_ids,
                "enable_tts": False,
                "require_qa_pass": False,
            },
        )
    assert response.status_code == 422
    assert "cannot be mixed" in response.json()["detail"]


@pytest.mark.anyio
async def test_repair_requires_saved_human_qa_and_known_codes():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product_response = await client.post(
            "/api/products/analyze",
            json={"product_name": f"修复门禁-{uuid4().hex[:8]}", "product_images": []},
        )
        product_id = product_response.json()["product_id"]
        task_id = f"TASK_REPAIR_GUARD_{uuid4().hex[:10]}"
        database.upsert_task(
            VideoTaskRecord(
                internal_task_id=task_id,
                product_id=product_id,
                shot_id="S02",
                prompt_text="修复门禁",
                status=TaskStatus.QA_PENDING,
            )
        )

        unknown = await client.post(
            f"/api/video/tasks/{task_id}/retry",
            json={"failure_codes": ["NOT_A_REAL_CODE"]},
        )
        no_qa = await client.post(
            f"/api/video/tasks/{task_id}/retry",
            json={"failure_codes": ["HAND001"]},
        )

    assert unknown.status_code == 422
    assert no_qa.status_code in {409, 422}
    assert "human QA" in str(no_qa.json()["detail"])


def test_jianying_filename_component_cannot_escape_output_tree():
    slug = JianyingExporter._safe_filename_component("../../\\危险商品:*?<>|")
    assert slug
    assert "/" not in slug and "\\" not in slug and ".." not in slug


def test_rfc2047_upload_filename_is_decoded_and_sanitized():
    encoded = "=?utf-8?B?54SV6KiA5YWF55S15a6dLnBuZw==?="
    assert AssetManager._safe_original_name(encoded) == "焕言充电宝.png"
