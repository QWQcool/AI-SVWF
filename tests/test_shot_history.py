import asyncio
import pytest
import httpx
from uuid import uuid4

from core.repair_engine import RepairEngine
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def wait_for_terminal(client: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(60):
        response = await client.get(f"/api/video/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] not in {"CREATED", "SUBMITTED", "PROCESSING"}:
            return task
        await asyncio.sleep(0.05)
    pytest.fail(f"task did not finish: {task_id}")


@pytest.mark.anyio
async def test_failure_codes_catalog_complete_33_items():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/qa/failure-codes")
        assert resp.status_code == 200, resp.text
        catalog = resp.json()

        assert len(catalog) == 33

        # 26 repairable + 7 hard fails
        hard_fails = [item for item in catalog if item["kind"] == "hard_fail"]
        repairable = [item for item in catalog if item["kind"] == "repairable"]

        assert len(hard_fails) == 7
        assert len(repairable) == 26

        for item in catalog:
            assert "code" in item
            assert "kind" in item
            assert "category" in item
            assert "name" in item
            assert "symptom" in item

        for item in hard_fails:
            assert item["repairable"] is False
            assert item["code"].startswith("HARD_FAIL_")

        for item in repairable:
            assert item["repairable"] is True
            assert "repair_action" in item


def test_natural_version_ordering():
    # 1.9 must be strictly less than 1.10
    v1_9 = RepairEngine.parse_version("1.9")
    v1_10 = RepairEngine.parse_version("1.10")
    v1_2 = RepairEngine.parse_version("1.2")

    assert v1_2 < v1_9 < v1_10
    assert v1_9 < v1_10

    # Test sorting a list with mixed versions
    raw = ["1.10", "1.2", "1.1", "1.9", "2.0"]
    sorted_versions = sorted(raw, key=RepairEngine.parse_version)
    assert sorted_versions == ["1.1", "1.2", "1.9", "1.10", "2.0"]


@pytest.mark.anyio
async def test_shot_revision_evolution_and_attempts():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"分镜历史测试商品-{uuid4().hex[:6]}"
        prod_resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "高品质不锈钢水杯",
            "product_images": [],
            "preferred_scene": "现代简约办公桌",
        })
        product = prod_resp.json()
        product_id = product["product_id"]

        # 1. Generate initial video task for S01
        task_resp = await client.post("/api/video/generate", json={
            "product_id": product_id,
            "shot_id": "S01",
            "prompt": "S01 场景建立提示词 V1.0",
            "provider": "mock",
            "model": "mock-video-v1",
            "prompt_version": "1.0",
        })
        assert task_resp.status_code == 200, task_resp.text
        task1 = task_resp.json()
        assert task1["prompt_version"] == "1.0"
        assert task1["attempt_no"] == 1
        assert task1["generation_kind"] == "initial"
        assert task1["revision_id"] is not None

        # 2. Reroll S01 with same prompt
        reroll_resp = await client.post(f"/api/video/tasks/{task1['internal_task_id']}/reroll")
        assert reroll_resp.status_code == 200, reroll_resp.text
        task2 = reroll_resp.json()

        # Reroll must reuse the SAME revision_id and prompt_version
        assert task2["revision_id"] == task1["revision_id"]
        assert task2["prompt_version"] == "1.0"
        # Attempt number strictly incremented
        assert task2["attempt_no"] == 2
        assert task2["generation_kind"] == "reroll"
        assert task2["root_task_id"] == task1["internal_task_id"]
        task2 = await wait_for_terminal(client, task2["internal_task_id"])
        assert task2["status"] == "QA_PENDING"

        # 3. Create a manual revision for S01
        rev_resp = await client.post(
            f"/api/products/{product_id}/shots/S01/prompt-revisions",
            json={
                "parent_revision_id": task2["revision_id"],
                "parent_task_id": task2["internal_task_id"],
                "prompt_text": "S01 手工修订提示词 V1.1",
                "negative_prompt": "低清, 变形",
                "change_type": "manual_revision",
                "change_note": "调整灯光与视角",
                "generate_immediately": True,
                "provider": "mock",
                "model": "mock-video-v1",
            },
        )
        assert rev_resp.status_code == 200, rev_resp.text
        res_json = rev_resp.json()
        rev_node = res_json.get("revision", res_json)
        assert (rev_node.get("version") or rev_node.get("display_version")) == "1.1"
        assert rev_node["change_type"] in ["manual_edit", "manual_revision"]
        assert rev_node["parent_revision_id"] == task2["revision_id"]
        assert res_json["task"]["internal_task_id"] != task2["internal_task_id"]
        assert res_json["task"]["parent_task_id"] == task2["internal_task_id"]

        # 4. Query full shot history
        history_resp = await client.get(f"/api/products/{product_id}/shots/S01/history")
        assert history_resp.status_code == 200, history_resp.text
        history = history_resp.json()

        assert history["product_id"] == product_id
        assert history["shot_id"] == "S01"
        assert len(history["revisions"]) >= 2

        # Check revision versions order
        v_list = [node["revision"]["version"] for node in history["revisions"]]
        assert "1.0" in v_list
        assert "1.1" in v_list

        # 5. Set selection for S01
        sel_resp = await client.put(
            f"/api/products/{product_id}/shots/S01/selection",
            json={
                "selected_task_id": task2["internal_task_id"],
                "selection_note": "选用第二次重抽尝试",
            },
        )
        assert sel_resp.status_code == 200, sel_resp.text
        selection_record = sel_resp.json()
        assert selection_record["selected_task_id"] == task2["internal_task_id"]

        # Verify history reflects the selection
        history_updated = (await client.get(f"/api/products/{product_id}/shots/S01/history")).json()
        assert history_updated["selected_task_id"] == task2["internal_task_id"]


@pytest.mark.anyio
async def test_real_final_selection_requires_archived_video_and_qa_pass(tmp_path):
    from core.adapter.jimeng import JimengAdapter
    from core.database import database
    from core.schemas import TaskStatus, VideoTaskRecord

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product_response = await client.post("/api/products/analyze", json={
            "product_name": f"真实选用门禁-{uuid4().hex[:6]}",
            "product_images": [],
        })
        product_id = product_response.json()["product_id"]
        task_id = f"TASK_SELECTION_{uuid4().hex[:10]}"
        task = VideoTaskRecord(
            internal_task_id=task_id,
            product_id=product_id,
            shot_id="S01",
            provider="volcengine",
            model="doubao-seedance-2-0-test",
            execution_mode="real",
            prompt_text="真实任务选用门禁",
            status=TaskStatus.QA_PENDING,
        )
        database.upsert_task(task)

        no_video = await client.put(
            f"/api/products/{product_id}/shots/S01/selection",
            json={"selected_task_id": task_id},
        )
        assert no_video.status_code == 409
        assert "no archived local video" in no_video.json()["detail"]

        local_video = tmp_path / "selection.mp4"
        local_video.write_bytes(b"test")
        task.video_url = "/outputs/selection.mp4"
        task.local_video_path = str(local_video)
        database.upsert_task(task)
        JimengAdapter._tasks.pop(task_id, None)

        not_passed = await client.put(
            f"/api/products/{product_id}/shots/S01/selection",
            json={"selected_task_id": task_id},
        )
        assert not_passed.status_code == 409
        assert "must PASS QA" in not_passed.json()["detail"]

        task.status = TaskStatus.PASS
        database.upsert_task(task)
        JimengAdapter._tasks.pop(task_id, None)
        accepted = await client.put(
            f"/api/products/{product_id}/shots/S01/selection",
            json={"selected_task_id": task_id, "selection_note": "验收通过后选用"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["selected_task_id"] == task_id


@pytest.mark.anyio
async def test_qa_failure_occurrences_and_hard_fail_rejection():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"质检测试商品-{uuid4().hex[:6]}"
        prod_resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "便携咖啡杯",
            "product_images": [],
            "preferred_scene": "现代简约工位",
        })
        product_id = prod_resp.json()["product_id"]

        # Create task for S02
        task_resp = await client.post("/api/video/generate", json={
            "product_id": product_id,
            "shot_id": "S02",
            "prompt": "S02 提示词",
            "provider": "mock",
            "model": "mock-video-v1",
            "prompt_version": "1.0",
        })
        task = task_resp.json()
        task_id = task["internal_task_id"]

        # Wait for task to reach QA_PENDING terminal state
        completed_task = await wait_for_terminal(client, task_id)
        assert completed_task["status"] == "QA_PENDING"

        # 1. Normal QA with HAND001
        qa_resp1 = await client.post(
            f"/api/video/tasks/{task_id}/qa",
            json={
                "internal_task_id": task_id,
                "shot_id": "S02",
                "score_product_consistency": 15,
                "score_person_realism": 12,
                "score_action_naturalness": 10,
                "score_hand_limb": 4,
                "score_prompt_following": 8,
                "score_scene_realism": 8,
                "failure_codes": ["HAND001"],
                "failure_occurrences": [
                    {
                        "code": "HAND001",
                        "note": "手指粘连变形",
                        "time_point_seconds": 2.4,
                        "frame_start": 48,
                        "frame_end": 60,
                    }
                ],
            },
        )
        assert qa_resp1.status_code == 200, qa_resp1.text
        res1 = qa_resp1.json()
        assert res1["task_status"] == "REPAIR"
        assert res1["qa_status"] == "REPAIR"
        assert "HAND001" in res1["failure_codes"]
        history = (await client.get(f"/api/products/{product_id}/shots/S02/history")).json()
        saved_occurrence = history["qa_records"][task_id][0]["input"]["failure_occurrences"][0]
        assert saved_occurrence == {
            "code": "HAND001",
            "note": "手指粘连变形",
            "time_point_seconds": 2.4,
            "frame_start": 48,
            "frame_end": 60,
        }

        invalid_time = await client.post(
            f"/api/video/tasks/{task_id}/qa",
            json={
                "internal_task_id": task_id,
                "shot_id": "S02",
                "failure_codes": ["HAND001"],
                "failure_occurrences": [{"code": "HAND001", "time_point_seconds": 5.1}],
            },
        )
        assert invalid_time.status_code == 422

        # 2. Hard Fail QA forces status to FAIL
        qa_resp2 = await client.post(
            f"/api/video/tasks/{task_id}/qa",
            json={
                "internal_task_id": task_id,
                "shot_id": "S02",
                "score_product_consistency": 20,
                "score_person_realism": 15,
                "score_action_naturalness": 15,
                "score_hand_limb": 10,
                "score_prompt_following": 10,
                "score_scene_realism": 10,
                "hard_fail_code": "HARD_FAIL_01",
                "failure_codes": ["HARD_FAIL_01"],
                "failure_occurrences": [
                    {"code": "HARD_FAIL_01", "note": "商品身份严重改变", "severity": "CRITICAL", "is_hard_fail": True}
                ],
            },
        )
        assert qa_resp2.status_code == 200, qa_resp2.text
        res2 = qa_resp2.json()
        assert res2["task_status"] == "REJECTED"
        assert res2["qa_status"] == "FAIL"


@pytest.mark.anyio
async def test_round1_variant_versions_survive_submission_and_repair():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product = (await client.post("/api/products/analyze", json={
            "product_name": f"变体版本测试-{uuid4().hex[:6]}",
            "product_images": [],
        })).json()
        planned = (await client.post("/api/prompts/variants/plan", json={
            "product_id": product["product_id"],
            "shot_ids": ["S02"],
            "variants_per_shot": 3,
            "base_version": "1.0",
        })).json()
        assert [item["prompt_version"] for item in planned] == ["1.0-v01", "1.0-v02", "1.0-v03"]

        tasks = []
        for variant in planned:
            response = await client.post("/api/video/generate", json={
                "product_id": product["product_id"],
                "shot_id": "S02",
                "provider": "mock",
                "model": "mock-video-v1",
                "prompt_version": variant["prompt_version"],
                "prompt": variant["prompt_text"],
                "negative_prompt": variant["negative_prompt"],
                "variant_id": variant["variant_id"],
            })
            assert response.status_code == 200, response.text
            tasks.append(await wait_for_terminal(client, response.json()["internal_task_id"]))

        assert [task["prompt_version"] for task in tasks] == ["1.0-v01", "1.0-v02", "1.0-v03"]
        repaired_versions = []
        for task in tasks:
            qa = await client.post(f"/api/video/tasks/{task['internal_task_id']}/qa", json={
                "internal_task_id": task["internal_task_id"],
                "shot_id": "S02",
                "score_action_naturalness": 5,
                "score_hand_limb": 0,
                "failure_codes": ["HAND001"],
                "failure_occurrences": [{"code": "HAND001", "note": "变体手部问题"}],
            })
            assert qa.status_code == 200, qa.text
            retry = await client.post(
                f"/api/video/tasks/{task['internal_task_id']}/retry",
                json={"failure_codes": ["HAND001"]},
            )
            assert retry.status_code == 200, retry.text
            repaired_versions.append(retry.json()["prompt_version"])
        assert repaired_versions == ["1.1-v01", "1.1-v02", "1.1-v03"]


@pytest.mark.anyio
async def test_manual_revision_rejects_promotional_claim_and_cross_shot_parent():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product = (await client.post("/api/products/analyze", json={
            "product_name": f"Prompt门禁-{uuid4().hex[:6]}",
            "product_images": [],
        })).json()
        unsafe = await client.post(
            f"/api/products/{product['product_id']}/shots/S01/prompt-revisions",
            json={"prompt_text": "该商品100%治愈高血压", "provider": "mock"},
        )
        assert unsafe.status_code == 422

        other = await client.post("/api/video/generate", json={
            "product_id": product["product_id"],
            "shot_id": "S02",
            "prompt": "普通安全商品展示",
            "provider": "mock",
        })
        assert other.status_code == 200
        cross_parent = await client.post(
            f"/api/products/{product['product_id']}/shots/S01/prompt-revisions",
            json={
                "parent_revision_id": other.json()["revision_id"],
                "prompt_text": "普通安全商品展示的新构图",
                "provider": "mock",
            },
        )
        assert cross_parent.status_code == 422
