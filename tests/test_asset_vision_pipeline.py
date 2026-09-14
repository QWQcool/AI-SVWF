"""Offline contract tests for upload, grounded vision and first-frame persistence."""

import base64
import asyncio
import io
import time
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from core.adapter.jimeng import JimengAdapter
from core.ark_client import ArkAPIError, ArkClient
from core.config import settings
from core.database import database
from core.schemas import TaskStatus, VideoTaskRecord
from core.storage import StorageManager
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 480), (20, 90, 160)).save(buffer, "PNG")
    return buffer.getvalue()


def test_seedance_output_url_variants_are_supported():
    assert JimengAdapter._provider_video_url({"output_url": "https://example.test/a.mp4"}).endswith("a.mp4")
    assert JimengAdapter._provider_video_url({"content": {"video_url": "https://example.test/b.mp4"}}).endswith("b.mp4")


def test_seedance_request_uses_composition_reference_and_explicit_media_settings(monkeypatch):
    captured = {}

    def fake_request(method, path, *, payload=None, timeout=0):
        captured.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"id": "provider-task"}

    monkeypatch.setattr(ArkClient, "_request", staticmethod(fake_request))
    result = ArkClient.create_video_task(
        model="doubao-seedance-2-0-260128",
        prompt="保持商品一致",
        image_reference="data:image/png;base64,AA==",
        duration=5,
        aspect_ratio="9:16",
    )

    assert result["id"] == "provider-task"
    assert captured["path"] == "/contents/generations/tasks"
    assert captured["payload"]["duration"] == 5
    assert captured["payload"]["ratio"] == "9:16"
    assert captured["payload"]["resolution"] == "720p"
    assert captured["payload"]["generate_audio"] is False
    assert captured["payload"]["content"][1]["role"] == "reference_image"


def test_seedream_pro_omits_unsupported_sequential_setting(monkeypatch):
    captured = {}

    def fake_request(method, path, *, payload=None, timeout=0):  # noqa: ARG001
        captured.update(payload=payload)
        return {"data": []}

    monkeypatch.setattr(ArkClient, "_request", staticmethod(fake_request))
    ArkClient.generate_image(
        model="doubao-seedream-5-0-pro-260628",
        prompt="test",
        image_references=[],
        size="1440x2560",
    )
    assert "sequential_image_generation" not in captured["payload"]


@pytest.mark.anyio
async def test_oversized_upload_is_rejected_before_multipart_parsing():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/assets/images",
            content=b"",
            headers={"content-length": str(66 * 1024 * 1024)},
        )
    assert response.status_code == 413


@pytest.mark.anyio
async def test_upload_reencoding_removes_trailing_metadata_bytes():
    raw = _png_bytes() + b"SENSITIVE-TRAILING-METADATA"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/assets/images",
            files=[("files", ("metadata.png", raw, "image/png"))],
        )
    assert response.status_code == 200, response.text
    stored = response.json()[0]
    with open(stored["local_path"], "rb") as handle:
        assert b"SENSITIVE-TRAILING-METADATA" not in handle.read()


@pytest.mark.anyio
async def test_empty_normalized_asset_ids_do_not_call_vision(monkeypatch):
    calls = 0

    def fake_vision(*args, **kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(ArkClient, "vision_json", staticmethod(fake_vision))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/products/analyze-vision", json={"asset_ids": ["   "]})
    assert response.status_code == 422
    assert calls == 0


@pytest.mark.anyio
async def test_concurrent_vision_idempotency_calls_provider_once(monkeypatch):
    calls = 0

    def fake_vision(*args, **kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return {
            "product_name": "并发测试商品",
            "category": "日常消费品",
            "appearance_description": "蓝色方形包装",
            "observed_information": ["蓝色包装"],
            "confidence": 0.75,
        }

    monkeypatch.setattr(ArkClient, "vision_json", staticmethod(fake_vision))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload = await client.post(
            "/api/assets/images",
            files=[("files", ("vision.png", _png_bytes(), "image/png"))],
        )
        asset_id = upload.json()[0]["asset_id"]
        payload = {
            "asset_ids": [asset_id],
            "idempotency_key": f"vision-concurrent-{uuid4().hex}",
        }
        first, second = await asyncio.gather(
            client.post("/api/products/analyze-vision", json=payload),
            client.post("/api/products/analyze-vision", json=payload),
        )
    assert 200 in {first.status_code, second.status_code}
    assert {first.status_code, second.status_code} <= {200, 502}
    assert calls == 1


@pytest.mark.anyio
async def test_first_frame_quota_reservation_is_atomic(monkeypatch):
    png = _png_bytes()
    calls = 0

    def fake_image(**kwargs):  # noqa: ARG001
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return {"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]}

    monkeypatch.setattr(ArkClient, "generate_image", staticmethod(fake_image))
    current = database.daily_real_task_counts(time.strftime("%Y-%m-%d"))["image"]
    monkeypatch.setattr(settings, "MAX_REAL_IMAGE_TASKS_PER_DAY", current + 1)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload = await client.post(
            "/api/assets/images", files=[("files", ("quota.png", png, "image/png"))]
        )
        asset = upload.json()[0]
        product = (await client.post("/api/products/analyze", json={
            "product_name": "配额测试商品",
            "product_images": [asset["url"]],
        })).json()
        base = {
            "product_id": product["product_id"],
            "prompt": "【第 1 层】商品展示",
            "asset_ids": [asset["asset_id"]],
        }
        first, second = await asyncio.gather(
            client.post("/api/images/first-frame", json={
                **base, "shot_id": "S01", "idempotency_key": f"quota-a-{uuid4().hex}",
            }),
            client.post("/api/images/first-frame", json={
                **base, "shot_id": "S02", "idempotency_key": f"quota-b-{uuid4().hex}",
            }),
        )
    assert sorted([first.status_code, second.status_code]) == [200, 429]
    assert calls == 1


@pytest.mark.anyio
async def test_transient_provider_poll_error_resumes_same_task(monkeypatch):
    task = VideoTaskRecord(
        internal_task_id=f"TASK_{uuid4().hex[:10].upper()}",
        provider_task_id=f"provider-{uuid4().hex}",
        product_id=f"PROD_{uuid4().hex[:8].upper()}",
        shot_id="S01",
        provider="volcengine",
        model="doubao-seedance-2-0-260128",
        execution_mode="real",
        request_fingerprint=uuid4().hex,
        prompt_text="test",
        source_image="/outputs/frame.png",
        status=TaskStatus.PROCESSING,
    )
    database.upsert_task(task)
    calls = 0

    def fake_poll(provider_task_id):  # noqa: ARG001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArkAPIError("temporary", status_code=500)
        return {"status": "succeeded", "content": {"video_url": "https://example.test/video.mp4"}}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(ArkClient, "get_video_task", staticmethod(fake_poll))
    monkeypatch.setattr(StorageManager, "download_remote_video", staticmethod(lambda *args: ("fake.mp4", "/outputs/fake.mp4")))
    monkeypatch.setattr(StorageManager, "inspect_video", staticmethod(lambda *args: {
        "width": 720, "height": 1280, "fps": 24.0, "duration": 5.0,
        "video_codec": "h264", "audio_codec": "",
    }))
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await JimengAdapter._process_real_api_task(task.internal_task_id)
    saved = database.get_task(task.internal_task_id)
    assert calls == 2
    assert saved.status == TaskStatus.QA_PENDING


@pytest.mark.anyio
async def test_upload_vision_compile_first_frame_and_idempotency(monkeypatch):
    png = _png_bytes()

    def fake_vision(assets, instruction, model):  # noqa: ARG001
        return {
            "product_name": "测试蓝色移动电源",
            "brand": "示例品牌",
            "category": "移动电源",
            "specification": "",
            "appearance_description": "蓝色圆角长方体，正面品牌位于中央",
            "observed_information": ["蓝色圆角外壳"],
            "visible_text": ["示例品牌"],
            "packaging_claims": ["轻巧便携"],
            "model_inferences": ["可能适合通勤"],
            "usage_scenes": ["办公室桌面"],
            "risk_information": [],
            "vision_notes": [],
            "confidence": 0.82,
        }

    def fake_image(**kwargs):  # noqa: ARG001
        return {"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]}

    monkeypatch.setattr(ArkClient, "vision_json", staticmethod(fake_vision))
    monkeypatch.setattr(ArkClient, "generate_image", staticmethod(fake_image))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload = await client.post(
            "/api/assets/images",
            files=[("files", ("powerbank.png", png, "image/png"))],
        )
        assert upload.status_code == 200, upload.text
        asset = upload.json()[0]
        duplicate = await client.post(
            "/api/assets/images",
            files=[("files", ("same-content.png", png, "image/png"))],
        )
        assert duplicate.json()[0]["asset_id"] == asset["asset_id"]

        analyzed = await client.post("/api/products/analyze-vision", json={
            "asset_ids": [asset["asset_id"]],
            "product_name": "",
            "preferred_scene": "普通办公桌",
        })
        assert analyzed.status_code == 200, analyzed.text
        product = analyzed.json()
        assert product["analysis_source"] == "vision"
        assert product["source_asset_ids"] == [asset["asset_id"]]
        assert product["packaging_claims"] == ["轻巧便携"]
        assert any("包装宣称" in item for item in product["possible_information"])

        compiled = await client.post("/api/prompts/compile", json={
            "product_id": product["product_id"],
            "provider": "volcengine",
            "model": "doubao-seedance-2-0-260128",
        })
        prompt = compiled.json()["shots"][0]["prompt"]
        assert "蓝色圆角长方体" in prompt
        assert "轻巧便携" not in prompt

        key = f"pytest-{uuid4().hex}"
        request = {
            "product_id": product["product_id"],
            "shot_id": "S01",
            "prompt": prompt,
            "asset_ids": [asset["asset_id"]],
            "model": "doubao-seedream-5-0-260128",
            "idempotency_key": key,
        }
        first = await client.post("/api/images/first-frame", json=request)
        second = await client.post("/api/images/first-frame", json=request)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "COMPLETED"
        assert "商品与场景构图参考图" in first.json()["prompt_text"]
        assert "不得出现人物、人脸、人体、手臂或手" in first.json()["prompt_text"]
        assert first.json()["image_task_id"] == second.json()["image_task_id"]


@pytest.mark.anyio
async def test_mock_video_duplicate_click_returns_same_task():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product = (await client.post("/api/products/analyze", json={
            "product_name": "幂等测试商品",
            "product_images": [],
        })).json()
        request = {
            "product_id": product["product_id"],
            "shot_id": "S01",
            "provider": "mock",
            "model": "mock-video-v1",
            "prompt": "固定幂等测试提示词",
            "idempotency_key": f"video-{uuid4().hex}",
        }
        first = await client.post("/api/video/generate", json=request)
        second = await client.post("/api/video/generate", json=request)
        assert first.status_code == 200
        assert first.json()["internal_task_id"] == second.json()["internal_task_id"]
