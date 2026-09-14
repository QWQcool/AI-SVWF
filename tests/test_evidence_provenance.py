import pytest
import httpx
from uuid import uuid4

from core.compliance import ComplianceGuard
from core.schemas import ProductAnalysis
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_evidence_source_categorization_and_claims():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"证据充分度测试商品-{uuid4().hex[:6]}"
        resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "316不锈钢内胆，6小时长效保温，一键弹盖。",
            "product_images": [],
            "preferred_scene": "现代简约办公桌",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "evidence_sufficiency" in data
        assert "evidence_status" in data
        assert "evidence_breakdown" in data
        assert "claims" in data

        claims = data["claims"]
        assert len(claims) > 0

        # Validate that claims have required provenance fields
        for claim in claims:
            assert "claim_id" in claim
            assert "text" in claim
            assert "classification" in claim
            assert "provenance" in claim
            assert len(claim["provenance"]) > 0
            for ref in claim["provenance"]:
                assert ref["source_type"] in [
                    "user_declaration",
                    "image_visible",
                    "packaging_text",
                    "model_inference",
                    "human_confirmation",
                ]

        # Packaging text and model inference must NEVER automatically become confirmed
        for claim in claims:
            sources = [ref["source_type"] for ref in claim["provenance"]]
            if any(s in {"packaging_text", "model_inference"} for s in sources) and "user_declaration" not in sources:
                assert claim["classification"] in {"possible", "rejected"}
                assert claim["classification"] != "confirmed"


@pytest.mark.anyio
async def test_evidence_breakdown_formula_and_structure():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"核算明细测试商品-{uuid4().hex[:6]}"
        resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "高品质双层隔热玻璃杯，手工吹制。",
            "product_images": [],
            "preferred_scene": "茶室原木茶桌",
        })
        assert resp.status_code == 200
        data = resp.json()
        bd = data["evidence_breakdown"]

        assert "raw_model_score" in bd
        assert "source_coverage" in bd
        assert "compliance_penalty" in bd
        assert "human_bonus" in bd
        assert "final_score" in bd

        # Without images, source_coverage is 0.0
        assert bd["source_coverage"] == 0.0
        assert bd["final_score"] == data["evidence_sufficiency"]
        assert 0.0 <= bd["final_score"] <= 1.0


@pytest.mark.anyio
async def test_compliance_penalty_with_medical_claim():
    # 1. Direct unit test of ComplianceGuard penalty logic
    penalty = ComplianceGuard.calculate_compliance_penalty(["CMP002"])
    assert penalty >= 0.40

    # 2. Integration test via API: medical / absolute claims trigger penalty
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        name = f"违规降血压治疗能量杯-{uuid4().hex[:6]}"
        resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "诺贝尔奖认证，100%治愈高血压糖尿病，包治百病，稳赚暴富！",
            "product_images": [],
            "preferred_scene": "豪华养生会所",
        })
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["risk_information"]) > 0
        # Sufficiency must be heavily penalized to <= 0.45
        assert data["evidence_sufficiency"] <= 0.45
        assert data["evidence_breakdown"]["compliance_penalty"] >= 0.40


@pytest.mark.anyio
async def test_human_claim_confirmation_lifecycle():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Create product with possible claims
        name = f"人工确认测试商品-{uuid4().hex[:6]}"
        resp = await client.post("/api/products/analyze", json={
            "product_name": name,
            "short_description": "极简便携保温杯，适合旅行使用。",
            "product_images": [],
            "preferred_scene": "户外草坪",
        })
        assert resp.status_code == 200
        product = resp.json()
        product_id = product["product_id"]
        initial_score = product["evidence_sufficiency"]

        # Pick a claim that is currently 'possible'
        claims = product["claims"]
        target_claim = next((c for c in claims if c["classification"] == "possible"), claims[0])
        claim_id = target_claim["claim_id"]

        # Step 1: Human confirms the claim
        confirm_resp = await client.post(
            f"/api/products/{product_id}/claims/{claim_id}/confirm",
            json={"confirmed": True, "confirmed_by": "tester_qa"},
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
        confirmed_prod = confirm_resp.json()

        updated_claim = next(c for c in confirmed_prod["claims"] if c["claim_id"] == claim_id)
        assert updated_claim["classification"] == "confirmed"
        assert updated_claim["human_confirmed"] is True
        assert any(ref["source_type"] == "human_confirmation" for ref in updated_claim["provenance"])

        # Score must recalculate and increase
        assert confirmed_prod["evidence_sufficiency"] == round(initial_score + 0.05, 2)
        assert confirmed_prod["evidence_breakdown"]["human_bonus"] > 0

        # Step 2: Revoke confirmation
        revoke_resp = await client.post(
            f"/api/products/{product_id}/claims/{claim_id}/confirm",
            json={"confirmed": False, "confirmed_by": "tester_qa"},
        )
        assert revoke_resp.status_code == 200
        revoked_prod = revoke_resp.json()

        revoked_claim = next(c for c in revoked_prod["claims"] if c["claim_id"] == claim_id)
        assert revoked_claim["classification"] != "confirmed"
        assert revoked_claim["human_confirmed"] is False

        # Score decreases back
        assert revoked_prod["evidence_sufficiency"] == initial_score


@pytest.mark.anyio
async def test_compliance_risk_cannot_be_human_confirmed():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/products/analyze", json={
            "product_name": f"违规确认门禁-{uuid4().hex[:6]}",
            "short_description": "100%治愈高血压",
            "product_images": [],
        })
        product = response.json()
        risky = next(claim for claim in product["claims"] if claim["compliance_failure_codes"])

        blocked = await client.post(
            f"/api/products/{product['product_id']}/claims/{risky['claim_id']}/confirm",
            json={"confirmed": True, "confirmed_by": "tester"},
        )
        assert blocked.status_code == 409, blocked.text

        persisted = (await client.get(f"/api/products/{product['product_id']}")).json()
        unchanged = next(claim for claim in persisted["claims"] if claim["claim_id"] == risky["claim_id"])
        assert unchanged["classification"] != "confirmed"
        assert unchanged["human_confirmed"] is False
        assert persisted["evidence_sufficiency"] <= 0.45


@pytest.mark.anyio
async def test_human_confirmation_is_idempotent_and_audited_once():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/products/analyze", json={
            "product_name": f"幂等确认-{uuid4().hex[:6]}",
            "product_images": [],
        })
        product = response.json()
        claim = next(item for item in product["claims"] if item["classification"] == "possible")
        path = f"/api/products/{product['product_id']}/claims/{claim['claim_id']}/confirm"

        first = await client.post(path, json={"confirmed": True, "confirmed_by": "tester", "note": "已核验"})
        second = await client.post(path, json={"confirmed": True, "confirmed_by": "tester", "note": "重复点击"})
        assert first.status_code == second.status_code == 200
        updated = second.json()
        selected = next(item for item in updated["claims"] if item["claim_id"] == claim["claim_id"])
        assert sum(ref["source_type"] == "human_confirmation" for ref in selected["provenance"]) == 1
        audit = (await client.get(f"/api/products/{product['product_id']}/claims/audit")).json()
        assert len([event for event in audit if event["claim_id"] == claim["claim_id"]]) == 1
        assert selected["human_confirmed_by"] == "tester"


def test_legacy_claim_ids_are_stable_across_deserialization():
    payload = {
        "product_id": "PROD_LEGACY_STABLE",
        "product_name": "旧商品",
        "confirmed_information": ["旧确认信息"],
        "possible_information": ["旧推测信息"],
        "information_confidence": 0.6,
    }
    first = ProductAnalysis.model_validate(dict(payload))
    second = ProductAnalysis.model_validate(dict(payload))
    assert [claim.claim_id for claim in first.claims] == [claim.claim_id for claim in second.claims]
