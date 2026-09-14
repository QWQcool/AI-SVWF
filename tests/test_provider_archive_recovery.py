"""Offline regression tests for paid-provider archive recovery semantics."""

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from PIL import Image

from core.adapter.jimeng import JimengAdapter
from core.ark_client import ArkClient
from core.config import settings
from core.database import database
from core.feishu_sync import FeishuBitableSync
from core.first_frame_service import FirstFrameService
from core.schemas import FirstFrameRequest, ProductAnalysis, TaskStatus, VideoTaskRecord
from core.storage import StorageManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _real_video_task() -> VideoTaskRecord:
    return VideoTaskRecord(
        internal_task_id=f"TASK_ARCHIVE_{uuid4().hex[:10].upper()}",
        provider_task_id=f"provider-{uuid4().hex}",
        product_id=f"PROD_ARCHIVE_{uuid4().hex[:8].upper()}",
        shot_id="S01",
        provider="volcengine",
        model="seedance-test",
        execution_mode="real",
        request_fingerprint=uuid4().hex,
        prompt_text="离线归档恢复测试",
        source_image="/outputs/frame.png",
        duration=5,
        aspect_ratio="9:16",
        status=TaskStatus.PROCESSING,
    )


def test_seedance_archive_name_is_stable_and_reuses_completed_download(monkeypatch):
    calls = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):  # noqa: ARG004
            yield b"provider-video-bytes"

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr("core.storage.requests.get", fake_get)
    archive_key = f"provider-{uuid4().hex}"
    first = StorageManager.download_remote_video(
        "https://example.test/result.mp4", "seedance-test", archive_key
    )
    second = StorageManager.download_remote_video(
        "https://example.test/result.mp4", "seedance-test", archive_key
    )

    assert first == second
    assert calls == 1
    assert Path(first[0]).read_bytes() == b"provider-video-bytes"
    StorageManager.discard_output_file(first[0])


def test_failed_seedance_download_removes_partial_file(monkeypatch):
    class InterruptedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):  # noqa: ARG004
            yield b"partial"
            raise requests.ConnectionError("synthetic interrupted stream")

    monkeypatch.setattr("core.storage.requests.get", lambda *_args, **_kwargs: InterruptedResponse())
    prefix = f"partial-{uuid4().hex[:8]}"

    with pytest.raises(requests.ConnectionError):
        StorageManager.download_remote_video(
            "https://example.test/result.mp4", prefix, f"provider-{uuid4().hex}"
        )

    assert not list(settings.OUTPUT_DIR.glob(f"{prefix}_*.mp4"))
    assert not list(settings.OUTPUT_DIR.glob(f"{prefix}_*.part"))


@pytest.mark.anyio
@pytest.mark.parametrize("failure_kind", ["decode", "dimensions"])
async def test_downloaded_invalid_seedance_media_is_deleted_and_terminal(
    monkeypatch, failure_kind
):
    task = _real_video_task()
    database.upsert_task(task)
    JimengAdapter._tasks.pop(task.internal_task_id, None)
    downloaded = settings.OUTPUT_DIR / f"{task.internal_task_id.lower()}.mp4"
    downloaded.parent.mkdir(parents=True, exist_ok=True)
    download_calls = []
    synced = []

    def fake_download(remote_url, prefix, archive_key):
        download_calls.append((remote_url, prefix, archive_key))
        downloaded.write_bytes(b"not-a-valid-mp4")
        return str(downloaded), f"/outputs/{downloaded.name}"

    def fake_inspect(_path):
        if failure_kind == "decode":
            raise RuntimeError("synthetic corrupt container")
        return {
            "width": 1280,
            "height": 720,
            "fps": 24.0,
            "duration": 5.0,
            "video_codec": "h264",
            "audio_codec": "",
        }

    monkeypatch.setattr(
        ArkClient,
        "get_video_task",
        staticmethod(
            lambda _provider_id: {
                "status": "succeeded",
                "content": {"video_url": "https://example.test/result.mp4"},
            }
        ),
    )
    monkeypatch.setattr(
        ArkClient,
        "create_video_task",
        staticmethod(lambda **_kwargs: pytest.fail("must not submit another paid video task")),
    )
    monkeypatch.setattr(StorageManager, "download_remote_video", staticmethod(fake_download))
    monkeypatch.setattr(StorageManager, "inspect_video", staticmethod(fake_inspect))
    monkeypatch.setattr(
        FeishuBitableSync,
        "sync_task",
        staticmethod(lambda saved: synced.append(saved.model_copy(deep=True))),
    )

    await JimengAdapter._process_real_api_task(task.internal_task_id)

    saved = database.get_task(task.internal_task_id)
    assert saved is not None
    assert saved.status == TaskStatus.FAILED
    assert saved.error_code == "ARK_VIDEO_MEDIA_INVALID"
    assert not downloaded.exists()
    assert download_calls == [
        ("https://example.test/result.mp4", "seedance", task.provider_task_id)
    ]
    assert [item.status for item in synced] == [TaskStatus.FAILED]


@pytest.mark.anyio
async def test_background_seedance_success_syncs_terminal_task_without_browser(monkeypatch):
    task = _real_video_task()
    database.upsert_task(task)
    JimengAdapter._tasks.pop(task.internal_task_id, None)
    local_path = settings.OUTPUT_DIR / f"{task.internal_task_id.lower()}-valid.mp4"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b"test-placeholder")
    synced = []

    monkeypatch.setattr(
        ArkClient,
        "get_video_task",
        staticmethod(
            lambda _provider_id: {
                "status": "succeeded",
                "content": {"video_url": "https://example.test/result.mp4"},
            }
        ),
    )
    monkeypatch.setattr(
        StorageManager,
        "download_remote_video",
        staticmethod(lambda *_args: (str(local_path), f"/outputs/{local_path.name}")),
    )
    monkeypatch.setattr(
        StorageManager,
        "inspect_video",
        staticmethod(
            lambda _path: {
                "width": 720,
                "height": 1280,
                "fps": 24.0,
                "duration": 5.0,
                "video_codec": "h264",
                "audio_codec": "",
            }
        ),
    )
    monkeypatch.setattr(
        FeishuBitableSync,
        "sync_task",
        staticmethod(lambda saved: synced.append(saved.model_copy(deep=True))),
    )

    await JimengAdapter._process_real_api_task(task.internal_task_id)

    saved = database.get_task(task.internal_task_id)
    assert saved is not None
    assert saved.status == TaskStatus.QA_PENDING
    assert [item.status for item in synced] == [TaskStatus.QA_PENDING]


@pytest.mark.anyio
async def test_restart_review_failure_is_synced_without_resubmission(monkeypatch):
    task = _real_video_task()
    task.provider_task_id = ""
    synced = []
    monkeypatch.setattr(database, "list_tasks", lambda *_args, **_kwargs: [task])
    monkeypatch.setattr(
        FeishuBitableSync,
        "sync_task",
        staticmethod(lambda saved: synced.append(saved.model_copy(deep=True))),
    )
    monkeypatch.setattr(
        ArkClient,
        "create_video_task",
        staticmethod(lambda **_kwargs: pytest.fail("restart recovery must not resubmit")),
    )

    resumed = await JimengAdapter.resume_pending_tasks()

    assert resumed == 0
    assert task.status == TaskStatus.FAILED
    assert task.error_code == "RESUME_REQUIRES_REVIEW"
    assert [item.error_code for item in synced] == ["RESUME_REQUIRES_REVIEW"]


def test_seedream_archive_failure_retries_saved_url_without_new_generation(monkeypatch):
    provider_calls = 0
    archive_calls = []
    remote_url = "https://example.test/already-generated.png"

    def fake_generate_image(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"data": [{"url": remote_url}]}

    def fake_archive(result, target):
        archive_calls.append(result["data"][0]["url"])
        if len(archive_calls) == 1:
            raise OSError("synthetic local disk interruption")
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (360, 640), (10, 40, 80)).save(target, "PNG")
        return remote_url, 360, 640

    monkeypatch.setattr(ArkClient, "generate_image", staticmethod(fake_generate_image))
    monkeypatch.setattr(FirstFrameService, "_download_image", staticmethod(fake_archive))
    monkeypatch.setattr(settings, "MAX_REAL_IMAGE_TASKS_PER_DAY", 100000)
    product = ProductAnalysis(
        product_id=f"PROD_FRAME_ARCHIVE_{uuid4().hex[:8]}",
        product_name="首帧归档恢复测试",
        source_images=["https://example.test/product.png"],
    )
    request = FirstFrameRequest(
        product_id=product.product_id,
        shot_id="S01",
        prompt_version="1.0",
        idempotency_key=f"frame-archive-{uuid4().hex}",
    )

    failed = FirstFrameService.generate(request, product)
    recovered = FirstFrameService.generate(request, product)
    cached = FirstFrameService.generate(request, product)

    assert failed.status == "FAILED"
    assert failed.error_code == "ARK_IMAGE_ARCHIVE_FAILED"
    assert failed.remote_url == remote_url
    assert recovered.image_task_id == failed.image_task_id
    assert recovered.status == "COMPLETED"
    assert recovered.image_url.endswith(f"{failed.image_task_id.lower()}.png")
    assert cached.image_task_id == failed.image_task_id
    assert provider_calls == 1
    assert archive_calls == [remote_url, remote_url]


def test_seedream_expired_archive_url_becomes_manual_new_attempt(monkeypatch):
    provider_calls = 0
    archive_calls = 0
    remote_url = "https://example.test/expired.png"
    response = requests.Response()
    response.status_code = 410
    response.url = remote_url

    def fake_generate_image(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"data": [{"url": remote_url}]}

    def expired_archive(_result, _target):
        nonlocal archive_calls
        archive_calls += 1
        raise requests.HTTPError("410 Gone", response=response)

    monkeypatch.setattr(ArkClient, "generate_image", staticmethod(fake_generate_image))
    monkeypatch.setattr(FirstFrameService, "_download_image", staticmethod(expired_archive))
    monkeypatch.setattr(settings, "MAX_REAL_IMAGE_TASKS_PER_DAY", 100000)
    product = ProductAnalysis(
        product_id=f"PROD_EXPIRED_FRAME_{uuid4().hex[:8]}",
        product_name="失效首帧归档源测试",
        source_images=["https://example.test/product.png"],
    )
    request = FirstFrameRequest(
        product_id=product.product_id,
        shot_id="S01",
        idempotency_key=f"frame-expired-{uuid4().hex}",
    )

    failed = FirstFrameService.generate(request, product)
    same_attempt = FirstFrameService.generate(request, product)

    assert failed.status == "FAILED"
    assert failed.error_code == "ARK_IMAGE_ARCHIVE_SOURCE_UNAVAILABLE"
    assert same_attempt.image_task_id == failed.image_task_id
    assert provider_calls == 1
    assert archive_calls == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_frontend_seedream_archive_retry_preserves_attempt_and_fingerprint():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    node_script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const storage = new Map();
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    value: "", innerText: "", textContent: "", style: {},
    load: () => {}, removeAttribute: () => {}
  });
  return elements.get(id);
};
let confirmCalls = 0;
const requestBodies = [];
const context = {
  document: {
    addEventListener: () => {},
    getElementById: element,
    querySelector: () => null
  },
  window: { confirm: () => { confirmCalls += 1; return true; } },
  console,
  sessionStorage: {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key)
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  fetch: async (_url, options) => {
    requestBodies.push(JSON.parse(options.body));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        image_task_id: "IMG_SAME",
        product_id: "PROD_1",
        shot_id: "S01",
        prompt_version: "1.0",
        status: "COMPLETED",
        image_url: "/outputs/first_frames/img_same.png"
      })
    };
  },
  setTimeout,
  clearTimeout
};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`
  currentProductId = "PROD_1";
  currentImageModel = "seedream-test";
  uploadedAssets = [];
  globalThis.archiveToastText = "";
  currentFirstFrameFailures.S01 = {
    product_id: "PROD_1",
    prompt_version: "1.0",
    error_code: "ARK_IMAGE_ARCHIVE_FAILED",
    error_message: "archive failed",
    remote_url: "https://example.test/already-generated.png"
  };
  showToast = (_title, description) => { globalThis.archiveToastText = description; };
`, context);
storage.set("first-frame-attempt:PROD_1:S01:1.0", "3");
(async () => {
  await vm.runInContext(`ensureFirstFrame("S01", "1.0", "prompt")`, context);
  const localArchiveToast = vm.runInContext("archiveToastText", context);
  vm.runInContext(`
    currentFirstFrames.S01 = null;
    currentFirstFrameFailures.S01 = {
      product_id: "PROD_1",
      prompt_version: "1.0",
      error_code: "ARK_IMAGE_ARCHIVE_FAILED",
      error_message: "archive failed without a reusable URL"
    };
  `, context);
  await vm.runInContext(`ensureFirstFrame("S01", "1.0", "prompt")`, context);
  process.stdout.write(JSON.stringify({
    confirmCalls,
    idempotencyKeys: requestBodies.map(item => item.idempotency_key),
    persistedAttempt: storage.get("first-frame-attempt:PROD_1:S01:1.0"),
    localArchiveToast
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
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    assert result["confirmCalls"] == 1
    assert result["idempotencyKeys"][0].endswith(":a3")
    assert result["idempotencyKeys"][1].endswith(":a4")
    assert result["persistedAttempt"] == "4"
    assert "不调用新的图片模型" in result["localArchiveToast"]
