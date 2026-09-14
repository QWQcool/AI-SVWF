import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from core.adapter.jimeng import JimengAdapter
from core.config import settings
from core.repair_engine import RepairEngine
from core.modules import PROMPT_MODULES
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def wait_for_terminal(client: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(180):
        response = await client.get(f"/api/video/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] not in {"CREATED", "SUBMITTED", "PROCESSING", "COMPLETED"}:
            return task
        await asyncio.sleep(0.1)
    pytest.fail(f"task did not finish: {task_id}")


@pytest.mark.anyio
async def test_documented_offline_workflow_and_persistence():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"接口契约测试商品-{uuid4().hex[:6]}"
        analyzed = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "用户提供的日常商品描述",
            "product_images": [],
            "preferred_scene": "普通办公室桌面",
        })
        assert analyzed.status_code == 200, analyzed.text
        product = analyzed.json()
        product_id = product["product_id"]
        assert product["source_images"] == []
        assert product["information_confidence"] < 0.90
        assert any("尚未提供商品参考图" in item for item in product["possible_information"])

        plan = await client.post("/api/video-plan/generate", json={"product_id": product_id})
        assert plan.status_code == 200
        assert plan.json()["template"]["generation_strategy"] == "3x5s"
        assert plan.json()["evidence_policy"]["real_generation_ready"] is False

        compiled = await client.post("/api/prompts/compile", json={
            "product_id": product_id,
            "version": "1.0",
            "provider": "mock",
            "model": "mock-video-v1",
        })
        assert compiled.status_code == 200, compiled.text
        prompt_schema = compiled.json()
        assert prompt_schema["source_assets"]["product_images"] == []
        assert len(prompt_schema["shots"]) == 3
        assert all("【第 11 层" in shot["prompt"] for shot in prompt_schema["shots"])

        variants_response = await client.post("/api/prompts/variants/plan", json={
            "product_id": product_id,
            "variants_per_shot": 3,
            "base_version": "1.0",
        })
        assert variants_response.status_code == 200, variants_response.text
        variants = variants_response.json()
        assert len(variants) == 9
        assert len({item["fingerprint"] for item in variants}) == 9

        submitted = []
        for shot in prompt_schema["shots"]:
            response = await client.post("/api/video/generate", json={
                "product_id": product_id,
                "shot_id": shot["shot_id"],
                "provider": "mock",
                "model": "mock-video-contract-test",
                "prompt_version": "1.0",
                "prompt": shot["prompt"],
                "negative_prompt": shot["negative_prompt"],
                "duration": 5,
                "aspect_ratio": "9:16",
                "product_name": name,
            })
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["provider"] == "mock"
            assert body["model"] == "mock-video-contract-test"
            assert body["execution_mode"] == "mock"
            assert body["duration"] == 5
            submitted.append(body)

        tasks = await asyncio.gather(
            *(wait_for_terminal(client, item["internal_task_id"]) for item in submitted)
        )
        assert {task["status"] for task in tasks} == {"QA_PENDING"}
        assert all(Path(task["local_video_path"]).exists() for task in tasks)

        blocked = await client.post("/api/video/stitch", json={
            "product_id": product_id,
            "task_ids": [task["internal_task_id"] for task in tasks],
            "enable_tts": False,
            "require_qa_pass": True,
        })
        assert blocked.status_code == 409

        for task in tasks:
            qa = await client.post(f"/api/video/tasks/{task['internal_task_id']}/qa", json={
                "internal_task_id": task["internal_task_id"],
                "shot_id": task["shot_id"],
            })
            assert qa.status_code == 200, qa.text
            assert qa.json()["task_status"] == "PASS"

        stitched = await client.post("/api/video/stitch", json={
            "product_id": product_id,
            "task_ids": [task["internal_task_id"] for task in tasks],
            "enable_tts": False,
            "require_qa_pass": True,
        })
        assert stitched.status_code == 200, stitched.text
        result = stitched.json()
        assert result["qa_pass_summary"]["status"] == "ALL_SHOTS_PASSED"
        assert Path(result["local_path"]).exists()

        # Clearing the process cache simulates a restart; SQLite remains authoritative.
        remembered_id = tasks[0]["internal_task_id"]
        JimengAdapter._tasks.clear()
        restored = await client.get(f"/api/video/tasks/{remembered_id}")
        assert restored.status_code == 200
        assert restored.json()["status"] == "PASS"
        events = (await client.get(f"/api/video/tasks/{remembered_id}/events")).json()
        assert [event["status"] for event in events] == [
            "CREATED", "SUBMITTED", "PROCESSING", "COMPLETED", "QA_PENDING", "PASS"
        ]

        metrics = await client.get("/api/metrics", params={"product_id": product_id})
        assert metrics.status_code == 200
        assert metrics.json()["summary"]["generation_count"] == 3
        assert metrics.json()["summary"]["pass_rate"] == 100.0


@pytest.mark.anyio
async def test_settings_are_redacted_and_llm_is_optional():
    old_key = settings.LLM_API_KEY
    settings.LLM_API_KEY = "secret-that-must-not-be-returned"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/system/settings")
            assert response.status_code == 200
            body = response.json()
            assert body["llm_api_key"] == ""
            assert body["has_llm_key"] is True
            assert "secret-that-must-not-be-returned" not in response.text
    finally:
        settings.LLM_API_KEY = old_key


@pytest.mark.anyio
async def test_failure_code_drives_versioned_single_shot_retry():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product = (await client.post("/api/products/analyze", json={
            "product_name": f"修复测试-{uuid4().hex[:6]}",
            "product_images": [],
        })).json()
        compiled = (await client.post("/api/prompts/compile", json={
            "product_id": product["product_id"], "version": "1.0",
        })).json()
        s02 = next(shot for shot in compiled["shots"] if shot["shot_id"] == "S02")
        submitted = (await client.post("/api/video/generate", json={
            "product_id": product["product_id"], "shot_id": "S02", "provider": "mock",
            "model": "mock-video-v1", "prompt_version": "1.0", "prompt": s02["prompt"],
            "negative_prompt": s02["negative_prompt"], "duration": 5, "aspect_ratio": "9:16",
        })).json()
        original = await wait_for_terminal(client, submitted["internal_task_id"])

        qa = await client.post(f"/api/video/tasks/{original['internal_task_id']}/qa", json={
            "internal_task_id": original["internal_task_id"], "shot_id": "S02",
            "score_product_consistency": 12, "score_person_realism": 12,
            "score_action_naturalness": 10, "score_hand_limb": 4,
            "score_prompt_following": 8, "score_scene_realism": 8,
            "failure_codes": ["HAND001", "PRO001"],
            "failure_notes": ["手指粘连", "商品轻微形变"],
        })
        assert qa.status_code == 200, qa.text
        assert qa.json()["task_status"] in {"REPAIR", "REJECTED"}
        assert qa.json()["next_prompt_version"] == "1.1"

        retry = await client.post(f"/api/video/tasks/{original['internal_task_id']}/retry", json={
            "failure_codes": ["HAND001", "PRO001"],
        })
        assert retry.status_code == 200, retry.text
        child = retry.json()
        assert child["parent_task_id"] == original["internal_task_id"]
        assert child["shot_id"] == "S02"
        assert child["prompt_version"] == "1.1"
        assert "Failure Code 定向修复约束" in child["prompt_text"]
        repaired = await wait_for_terminal(client, child["internal_task_id"])
        assert repaired["status"] == "QA_PENDING"


@pytest.mark.anyio
async def test_invalid_contract_inputs_are_rejected():
    assert len(RepairEngine.FAILURE_CODE_MAP) == 26
    assert len(PROMPT_MODULES) == 20
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        bad_shot = await client.post("/api/video/generate", json={
            "product_id": "missing",
            "shot_id": "S99",
            "prompt": "x",
        })
        assert bad_shot.status_code == 422

        missing_product = await client.post("/api/prompts/compile", json={
            "product_id": "missing",
            "version": "1.0",
        })
        assert missing_product.status_code == 404
