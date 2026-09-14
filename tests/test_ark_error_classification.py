"""Focused offline coverage for actionable Ark video failure codes."""

from uuid import uuid4

import pytest

from core.adapter.jimeng import JimengAdapter
from core.ark_client import ArkAPIError, ArkClient
from core.database import database
from core.schemas import TaskStatus, VideoTaskRecord


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_asset_service_not_activated_has_dedicated_video_error_code(monkeypatch):
    task = VideoTaskRecord(
        internal_task_id=f"TASK_ASSET_SERVICE_{uuid4().hex[:10].upper()}",
        product_id=f"PROD_ASSET_SERVICE_{uuid4().hex[:8].upper()}",
        shot_id="S01",
        provider="volcengine",
        model="doubao-seedance-2-0-test",
        execution_mode="real",
        request_fingerprint=uuid4().hex,
        prompt_text="离线错误分类测试",
        source_image="data:image/png;base64,AA==",
        status=TaskStatus.SUBMITTED,
    )
    database.upsert_task(task)
    JimengAdapter._tasks.pop(task.internal_task_id, None)

    def reject_submission(**_kwargs):
        raise ArkAPIError(
            "方舟 API 返回 HTTP 400: account has not activated the Asset Service",
            status_code=400,
        )

    async def no_external_sync(_task):
        return None

    monkeypatch.setattr(ArkClient, "create_video_task", staticmethod(reject_submission))
    monkeypatch.setattr(
        JimengAdapter,
        "_sync_real_terminal_best_effort",
        staticmethod(no_external_sync),
    )

    await JimengAdapter._process_real_api_task(task.internal_task_id)

    saved = database.get_task(task.internal_task_id)
    assert saved is not None
    assert saved.status == TaskStatus.FAILED
    assert saved.error_code == "ARK_ASSET_SERVICE_NOT_ACTIVATED"
    assert "account has not activated the Asset Service" in (saved.error_message or "")
