"""Offline contract tests for the allowlisted Volcengine public avatars."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from core.ark_client import ArkAPIError, ArkClient
from core.adapter.jimeng import JimengAdapter
from core.public_virtual_actors import PublicVirtualActorCatalog
from core.schemas import FirstFrameRequest, PromptCompileRequest, VideoGenerateRequest
from main import app


EXPECTED_ACTORS = {
    "group-20260804202300-v6kl5": {
        "asset_uri": "asset://asset-20260804202300-dfnsm",
        "country": "中国",
        "gender": "女",
        "age": 24,
        "role": "戏楼名伶",
        "description": (
            "24岁的女性演员，扮演戏楼名伶，是先秦战乱中被士族收进后宅的戏台翘楚，"
            "气质空灵如松间雪，性子孤冷避世，仅为知音唱半折古调，不受俗世馈赠。"
        ),
    },
    "group-20260804202305-nbfxc": {
        "asset_uri": "asset://asset-20260804202305-7b9lc",
        "country": "日本",
        "gender": "女",
        "age": 37,
        "role": "新媒体运营",
        "description": (
            "37岁的女性演员，扮演新媒体运营，熟谙流量逻辑，历过行业野蛮生长到合规迭代的起落，"
            "眼锋扫数据比刷热点快，靠精明稳占部门核心岗。"
        ),
    },
    "group-20260804202241-brp4f": {
        "asset_uri": "asset://asset-20260804202241-z9zcp",
        "country": "韩国",
        "gender": "男",
        "age": 37,
        "role": "高级程序员",
        "description": (
            "37岁的男性演员，扮演高级程序员，在互联网大厂深耕多年，极少掺和职场应酬，"
            "永远冷脸寡言，指尖留键盘磨出的薄茧，只在调试代码时眼底才泛起微末活气。"
        ),
    },
    "group-20260804202323-c7sr5": {
        "asset_uri": "asset://asset-20260804202323-67p4m",
        "country": "中国",
        "gender": "男",
        "age": 24,
        "role": "藏书阁主人",
        "description": (
            "24岁的男性演员，扮演藏书阁主人，是元代避世汉人儒士，守半阁前朝珍本，"
            "温韧沉静，指尖常沾墨香，暗地帮流离文人抄录散佚典籍。"
        ),
    },
    "group-20260804202224-z6tvn": {
        "asset_uri": "asset://asset-20260804202224-tdsdg",
        "country": "中国",
        "gender": "女",
        "age": 24,
        "role": "传令兵",
        "description": (
            "24岁的女性演员，扮演传令兵，乃架空王朝将门孤女，自幼习文懂兵略，"
            "敛去一身雅致文气，凭过目不忘的本事穿梭营盘传讯，从无纰漏。"
        ),
    },
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _actor_map(items: list[dict]) -> dict[str, dict]:
    return {item["group_id"]: item for item in items}


def test_repository_catalog_contains_exactly_the_five_supplied_actors():
    """The checked-in file is the authority; arbitrary client asset URIs are not."""
    assert isinstance(PublicVirtualActorCatalog, type)
    catalog_path = Path(__file__).resolve().parents[1] / "presets" / "public_virtual_actors.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    actors = _actor_map(payload["actors"])

    assert set(actors) == set(EXPECTED_ACTORS)
    assert payload["default_group_id"] in EXPECTED_ACTORS
    for group_id, expected in EXPECTED_ACTORS.items():
        actual = actors[group_id]
        for field, value in expected.items():
            assert actual[field] == value
        assert actual["provider_public"] is True
        assert actual["project_allowlisted"] is True


@pytest.mark.anyio
async def test_public_actor_catalog_endpoint_returns_safe_allowlisted_metadata():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/virtual-actors/public")

    assert response.status_code == 200, response.text
    body = response.json()
    actors = _actor_map(body["actors"])
    assert body["default_group_id"] in EXPECTED_ACTORS
    assert {group_id: item["asset_uri"] for group_id, item in actors.items()} == {
        group_id: expected["asset_uri"] for group_id, expected in EXPECTED_ACTORS.items()
    }
    assert "api_key" not in response.text.lower()


@pytest.mark.anyio
async def test_product_actor_selection_is_allowlisted_and_persisted():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        product_response = await client.post(
            "/api/products/analyze",
            json={"product_name": f"公共虚拟人测试-{uuid4().hex[:8]}", "product_images": []},
        )
        assert product_response.status_code == 200, product_response.text
        product_id = product_response.json()["product_id"]

        group_id = "group-20260804202305-nbfxc"
        selected = await client.put(
            f"/api/products/{product_id}/virtual-actor",
            json={"group_id": group_id},
        )
        assert selected.status_code == 200, selected.text

        restored = await client.get(f"/api/products/{product_id}")
        assert restored.status_code == 200, restored.text
        assert restored.json()["virtual_actor_group_id"] == group_id

        compiled = await client.post(
            "/api/prompts/compile",
            json={"product_id": product_id, "virtual_actor_group_id": group_id},
        )
        assert compiled.status_code == 200, compiled.text
        assert compiled.json()["character"]["group_id"] == group_id
        assert "reference_image" in compiled.json()["shots"][0]["prompt"]

        rejected = await client.put(
            f"/api/products/{product_id}/virtual-actor",
            json={"group_id": "group-20990101000000-not-allowlisted"},
        )
        assert rejected.status_code == 422
        assert "asset://" not in rejected.text


def test_workflow_request_models_accept_a_catalog_group_id():
    group_id = "group-20260804202323-c7sr5"
    compile_request = PromptCompileRequest(
        product_id="product-1",
        virtual_actor_group_id=group_id,
    )
    first_frame_request = FirstFrameRequest(
        product_id="product-1",
        virtual_actor_group_id=group_id,
    )
    video_request = VideoGenerateRequest(
        product_id="product-1",
        shot_id="S01",
        prompt="保持人物和商品一致",
        virtual_actor_group_id=group_id,
    )

    assert compile_request.virtual_actor_group_id == group_id
    assert first_frame_request.virtual_actor_group_id == group_id
    assert video_request.virtual_actor_group_id == group_id


def test_seedance_payload_keeps_product_first_frame_and_actor_reference_separate(monkeypatch):
    captured = {}

    def fake_request(method, path, *, payload=None, timeout=0):
        captured.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"id": "offline-provider-task"}

    monkeypatch.setattr(ArkClient, "_request", staticmethod(fake_request))
    actor_uri = EXPECTED_ACTORS["group-20260804202305-nbfxc"]["asset_uri"]
    result = ArkClient.create_video_task(
        model="doubao-seedance-2-0-260128",
        prompt="保持人物身份、商品外观与首帧构图一致",
        image_reference="data:image/png;base64,AA==",
        virtual_actor_reference=actor_uri,
        duration=5,
        aspect_ratio="9:16",
    )

    assert result["id"] == "offline-provider-task"
    assert captured["path"] == "/contents/generations/tasks"
    image_items = [
        item for item in captured["payload"]["content"] if item.get("type") == "image_url"
    ]
    assert [item["role"] for item in image_items] == ["first_frame", "reference_image"]
    assert image_items[0]["image_url"]["url"] == "data:image/png;base64,AA=="
    assert image_items[1]["image_url"]["url"] == actor_uri


def test_asset_scheme_is_never_accepted_by_the_generic_image_resolver():
    known_actor_uri = EXPECTED_ACTORS["group-20260804202300-v6kl5"]["asset_uri"]
    with pytest.raises(ArkAPIError):
        ArkClient.resolve_image_reference(known_actor_uri)


def test_seedance_rejects_an_arbitrary_unallowlisted_actor_uri(monkeypatch):
    called = False

    def fake_request(*args, **kwargs):  # noqa: ARG001
        nonlocal called
        called = True
        return {"id": "must-not-be-created"}

    monkeypatch.setattr(ArkClient, "_request", staticmethod(fake_request))
    with pytest.raises(ArkAPIError):
        ArkClient.create_video_task(
            model="doubao-seedance-2-0-260128",
            prompt="test",
            image_reference="data:image/png;base64,AA==",
            virtual_actor_reference="asset://asset-20990101000000-attacker",
            duration=5,
            aspect_ratio="9:16",
        )
    assert called is False


def test_selected_actor_changes_the_video_idempotency_fingerprint():
    first = PublicVirtualActorCatalog.require("group-20260804202300-v6kl5")
    second = PublicVirtualActorCatalog.require("group-20260804202305-nbfxc")
    common = {
        "product_id": "product-1",
        "shot_id": "S01",
        "prompt": "same prompt",
        "image_url": "/outputs/frame.png",
        "model": "doubao-seedance-2-0-260128",
    }

    first_fingerprint = JimengAdapter._fingerprint(
        **common, virtual_actor=first.model_dump(mode="json")
    )
    second_fingerprint = JimengAdapter._fingerprint(
        **common, virtual_actor=second.model_dump(mode="json")
    )
    assert first_fingerprint != second_fingerprint
