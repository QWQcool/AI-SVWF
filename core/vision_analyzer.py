"""Grounded product analysis using a multimodal Ark model."""

import hashlib
import json
import math
from typing import Any
from uuid import uuid4

from core.ark_client import ArkAPIError, ArkClient
from core.compliance import ComplianceGuard
from core.config import settings
from core.database import database
from core.errors import QuotaExceededError
from core.schemas import (
    AssetRecord,
    EvidenceReference,
    ProductAnalysis,
    ProductClaim,
    VisionAnalyzeRequest,
    utc_now_iso,
)


def _text_list(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text[:500])
    return result[:limit]


class VisionProductAnalyzer:
    @staticmethod
    def _fingerprint(payload: VisionAnalyzeRequest, assets: list[AssetRecord], model: str) -> str:
        source = {
            "asset_sha256": [asset.sha256 for asset in assets],
            "model": model,
            "product_name": payload.product_name,
            "short_description": payload.short_description,
            "target_audience": payload.target_audience,
            "preferred_scene": payload.preferred_scene,
            "schema": "vision-product-v1",
            "idempotency_key": payload.idempotency_key or "",
        }
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def analyze(cls, payload: VisionAnalyzeRequest, assets: list[AssetRecord]) -> ProductAnalysis:
        model = payload.model or settings.VISION_MODEL
        fingerprint = cls._fingerprint(payload, assets, model)
        cached = database.find_vision_product(fingerprint)
        if cached:
            return cached

        product_id = f"PROD_{uuid4().hex[:8].upper()}"
        reservation = database.reserve_vision_analysis(
            product_id,
            model,
            fingerprint,
            payload.asset_ids,
            settings.MAX_REAL_VISION_TASKS_PER_DAY,
        )
        if reservation["result"] == "quota":
            raise QuotaExceededError(
                f"已达到每日真实识图上限 {settings.MAX_REAL_VISION_TASKS_PER_DAY}，停止新提交"
            )
        if reservation["result"] == "existing":
            state = reservation.get("status", "UNKNOWN")
            requires_review = state in {
                "SUBMITTED",
                "SUBMISSION_UNCERTAIN",
                "RESUME_REQUIRES_REVIEW",
            }
            raise ArkAPIError(
                f"同一幂等识图请求处于 {state}；为避免重复扣费未自动重放。"
                "确认重试时请使用新的 idempotency_key",
                status_code=0 if requires_review else 409,
                error_code=(
                    "ARK_VISION_SUBMISSION_UNCERTAIN"
                    if requires_review else "ARK_VISION_RETRY_REQUIRED"
                ),
            )

        hints = json.dumps(
            {
                "user_product_name": payload.product_name,
                "user_description": payload.short_description,
                "preferred_scene": payload.preferred_scene,
            },
            ensure_ascii=False,
        )
        instruction = f"""
你是电商商品图片证据提取器。分析随后按顺序提供的 {len(assets)} 张同一商品图片。
用户补充信息：{hints}

必须只返回一个 JSON 对象，不要 Markdown。严格区分：
1. observed_information：图片直接可见的客观外观、结构和文字，不写功效推断；
2. packaging_claims：包装上印刷的功效、性能、营销宣称，必须按“包装宣称”处理；
3. model_inferences：仅凭图片推测的受众、用途或卖点，不能作为事实；
4. vision_notes：图片之间不一致、概念图、示意图、“待确认”文字、遮挡或无法辨认内容。

禁止编造成分、检测数据、容量、功率、材质、医疗功效或认证。看不清就留空。
返回字段必须为：
{{
  "product_name": "图片上可确认的商品通用名称或空字符串",
  "brand": "图片可见品牌或空字符串",
  "category": "保守品类",
  "specification": "图片明确可见的净含量/规格或空字符串",
  "appearance_description": "用于锁定商品身份的颜色、形状、包装结构、Logo位置",
  "observed_information": ["客观事实"],
  "visible_text": ["逐项抄录的关键包装文字"],
  "packaging_claims": ["包装营销宣称"],
  "model_inferences": ["待确认推测"],
  "usage_scenes": ["保守建议场景"],
  "risk_information": ["可能需要合规复核的点"],
  "vision_notes": ["证据局限"],
  "confidence": 0.0
}}
confidence 取 0 到 1，存在概念图、示意图、图片矛盾时不得超过 0.68。
""".strip()
        try:
            raw = ArkClient.vision_json(assets, instruction, model)
        except Exception as exc:
            submission_uncertain = isinstance(exc, ArkAPIError) and exc.status_code == 0
            error_code = (
                "ARK_VISION_SUBMISSION_UNCERTAIN"
                if submission_uncertain else "ARK_VISION_ANALYSIS_FAILED"
            )
            database.fail_vision_analysis(
                fingerprint,
                str(exc),
                error_code=error_code,
                status="SUBMISSION_UNCERTAIN" if submission_uncertain else "FAILED",
            )
            if isinstance(exc, ArkAPIError):
                exc.error_code = error_code
            raise
        if not isinstance(raw, dict):
            message = "视觉模型返回的 JSON 顶层必须是对象"
            database.fail_vision_analysis(
                fingerprint,
                message,
                error_code="ARK_VISION_RESULT_REVIEW_REQUIRED",
                status="RESUME_REQUIRES_REVIEW",
            )
            raise ArkAPIError(
                message,
                error_code="ARK_VISION_RESULT_REVIEW_REQUIRED",
            )

        observed = _text_list(raw.get("observed_information"))
        visible_text = _text_list(raw.get("visible_text"))
        claims = _text_list(raw.get("packaging_claims"))
        inferences = _text_list(raw.get("model_inferences"))
        notes = _text_list(raw.get("vision_notes"))
        model_risks = _text_list(raw.get("risk_information"))

        raw_product_name = str(raw.get("product_name") or "").strip()
        product_name = payload.product_name or raw_product_name or "待确认商品"
        brand = str(raw.get("brand") or "").strip()
        category = str(raw.get("category") or "日常消费品").strip()
        specification = str(raw.get("specification") or "").strip()
        appearance = str(raw.get("appearance_description") or "").strip()

        confirmed_candidates: list[str] = []
        if payload.product_name:
            confirmed_candidates.append(f"用户提供的商品名称：{product_name}")
        elif raw_product_name:
            confirmed_candidates.append(f"图片可见商品名称：{product_name}")
        if brand:
            confirmed_candidates.append(f"图片可见品牌：{brand}")
        if specification:
            confirmed_candidates.append(f"图片可见规格文字：{specification}")
        if appearance:
            confirmed_candidates.append(f"图片可见外观：{appearance}")
        confirmed_candidates.extend(f"图片可见：{item}" for item in observed)
        if payload.short_description:
            confirmed_candidates.append(f"用户提供的描述：{payload.short_description}")
        confirmed, compliance_risks, _, candidate_failure_codes = ComplianceGuard.sanitize_and_score(
            confirmed_candidates, base_confidence=0.85
        )

        possible = [f"包装宣称（待人工确认）：{item}" for item in claims]
        possible.extend(f"模型推测（未验证）：{item}" for item in inferences)
        risks = list(dict.fromkeys(model_risks + compliance_risks))
        all_failure_codes: list[str] = list(candidate_failure_codes)
        for claim in claims:
            audit = ComplianceGuard.audit_text(claim)
            risks.extend(item["reason"] for item in audit["violations"])
            for code in audit["failure_codes"]:
                if code not in all_failure_codes:
                    all_failure_codes.append(code)
        for v_text in visible_text:
            audit = ComplianceGuard.audit_text(v_text)
            risks.extend(item["reason"] for item in audit["violations"])
            for code in audit["failure_codes"]:
                if code not in all_failure_codes:
                    all_failure_codes.append(code)
        risks = list(dict.fromkeys(risks))

        # 构造结构化 ProductClaim 列表
        now = utc_now_iso()
        asset_ids = [asset.asset_id for asset in assets]
        product_claims: list[ProductClaim] = []

        if payload.product_name:
            product_claims.append(
                ProductClaim(
                    text=f"用户提供的商品名称：{payload.product_name}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="user_declaration",
                            source_asset_ids=asset_ids,
                            detail="用户表单输入的商品名称",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )
        elif raw_product_name:
            product_claims.append(
                ProductClaim(
                    text=f"图片可见商品名称：{raw_product_name}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="image_visible",
                            source_asset_ids=asset_ids,
                            detail="视觉模型在商品外包装上识别到的品名",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        if brand:
            product_claims.append(
                ProductClaim(
                    text=f"图片可见品牌：{brand}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="image_visible",
                            source_asset_ids=asset_ids,
                            detail="视觉模型识别到的品牌文字或标识",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        if specification:
            product_claims.append(
                ProductClaim(
                    text=f"图片可见规格文字：{specification}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="packaging_text",
                            source_asset_ids=asset_ids,
                            detail="外包装印刷的净含量与规格文字",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        if appearance:
            product_claims.append(
                ProductClaim(
                    text=f"图片可见外观：{appearance}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="image_visible",
                            source_asset_ids=asset_ids,
                            detail="像素级别提取的商品外观与结构特征",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        for item in observed:
            product_claims.append(
                ProductClaim(
                    text=f"图片可见：{item}",
                    classification="confirmed",
                    provenance=[
                        EvidenceReference(
                            source_type="image_visible",
                            source_asset_ids=asset_ids,
                            detail="视觉模型直接观察到的像素级客观事实",
                            model=model,
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        # 包装文字：仅代表“包装出现了该文字”，不能自动证明宣称真实
        for item in visible_text:
            audit = ComplianceGuard.audit_text(item)
            c_codes = audit.get("failure_codes", [])
            product_claims.append(
                ProductClaim(
                    text=f"包装文字：{item}",
                    classification="rejected" if "CMP002" in c_codes else "possible",
                    provenance=[
                        EvidenceReference(
                            source_type="packaging_text",
                            source_asset_ids=asset_ids,
                            detail="包装印刷文字抄录，不代表宣称属实",
                            model=model,
                            created_at=now,
                        )
                    ],
                    compliance_failure_codes=c_codes,
                    created_at=now,
                    updated_at=now,
                )
            )

        # 包装宣称：不能直接当成已确认事实
        for item in claims:
            audit = ComplianceGuard.audit_text(item)
            c_codes = audit.get("failure_codes", [])
            product_claims.append(
                ProductClaim(
                    text=f"包装宣称（待人工确认）：{item}",
                    classification="rejected" if "CMP002" in c_codes else "possible",
                    provenance=[
                        EvidenceReference(
                            source_type="packaging_text",
                            source_asset_ids=asset_ids,
                            detail="包装营销宣称，仍需资质核验",
                            model=model,
                            created_at=now,
                        )
                    ],
                    compliance_failure_codes=c_codes,
                    created_at=now,
                    updated_at=now,
                )
            )

        # 模型推测：默认只能是 possible
        for item in inferences:
            audit = ComplianceGuard.audit_text(item)
            c_codes = audit.get("failure_codes", [])
            product_claims.append(
                ProductClaim(
                    text=f"模型推测（未验证）：{item}",
                    classification="possible",
                    provenance=[
                        EvidenceReference(
                            source_type="model_inference",
                            source_asset_ids=asset_ids,
                            detail="多模态模型关于用途与卖点的推测，不可作为硬事实",
                            model=model,
                            created_at=now,
                        )
                    ],
                    compliance_failure_codes=c_codes,
                    created_at=now,
                    updated_at=now,
                )
            )

        if payload.short_description:
            audit = ComplianceGuard.audit_text(payload.short_description)
            c_codes = audit.get("failure_codes", [])
            product_claims.append(
                ProductClaim(
                    text=f"用户提供的描述：{payload.short_description}",
                    classification="rejected" if "CMP002" in c_codes else ("possible" if c_codes else "confirmed"),
                    provenance=[
                        EvidenceReference(
                            source_type="user_declaration",
                            source_asset_ids=asset_ids,
                            detail="用户在表单填写的商品卖点与日常描述",
                            model=model,
                            created_at=now,
                        )
                    ],
                    compliance_failure_codes=c_codes,
                    created_at=now,
                    updated_at=now,
                )
            )

        # 服务端核算证据充分度评分（绝不直接透传模型自报分数）
        try:
            raw_conf = float(raw.get("confidence", 0.75))
        except (TypeError, ValueError):
            raw_conf = 0.75
        if not math.isfinite(raw_conf):
            raw_conf = 0.0
        raw_model_score = max(0.0, min(1.0, round(raw_conf, 2)))

        source_coverage = (0.05 if len(assets) > 1 else 0.0) + (0.05 if payload.short_description else 0.0)

        conflict_penalty = 0.0
        if payload.product_name and raw_product_name:
            if payload.product_name.strip() != raw_product_name.strip() and raw_product_name.strip() not in payload.product_name:
                conflict_penalty = 0.10

        occlusion_penalty = 0.0
        if raw_model_score > 0.50 and any(marker in " ".join(notes) for marker in ("严重遮挡", "完全看不清")):
            occlusion_penalty = 0.10

        compliance_penalty = ComplianceGuard.calculate_compliance_penalty(all_failure_codes)

        final_score = ComplianceGuard.calculate_evidence_score(
            raw_model_score,
            source_coverage=source_coverage,
            conflict_penalty=conflict_penalty,
            occlusion_penalty=occlusion_penalty,
            failure_codes=all_failure_codes,
        )

        evidence_breakdown = {
            "raw_model_score": raw_model_score,
            "source_coverage": round(source_coverage, 2),
            "conflict_penalty": round(conflict_penalty, 2),
            "occlusion_penalty": round(occlusion_penalty, 2),
            "compliance_penalty": round(compliance_penalty, 2),
            "human_bonus": 0.0,
            "final_score": final_score,
        }

        scenes = _text_list(raw.get("usage_scenes"), 8)
        if payload.preferred_scene:
            scenes.insert(0, payload.preferred_scene)
        if not scenes:
            scenes = ["真实日常桌面"]

        evidence_status = "needs_review" if (risks or final_score < 0.70) else "analyzed"

        product = ProductAnalysis(
            product_id=product_id,
            product_name=product_name,
            brand=brand,
            category=category,
            specification=specification,
            appearance_description=appearance or "外观需人工复核",
            confirmed_information=[c.text for c in product_claims if c.classification == "confirmed"],
            possible_information=[c.text for c in product_claims if c.classification == "possible"],
            usage_scenes=list(dict.fromkeys(scenes)),
            risk_information=risks,
            evidence_sufficiency=final_score,
            evidence_status=evidence_status,
            evidence_breakdown=evidence_breakdown,
            claims=product_claims,
            information_confidence=final_score,
            source_images=[asset.url for asset in assets],
            target_audience=payload.target_audience,
            preferred_scene=payload.preferred_scene,
            source_description=payload.short_description,
            analysis_source="vision",
            analysis_model=model,
            source_asset_ids=[asset.asset_id for asset in assets],
            observed_information=observed + [f"包装文字：{item}" for item in visible_text],
            packaging_claims=claims,
            model_inferences=inferences,
            vision_notes=notes,
            created_at=now,
            updated_at=now,
        )
        database.upsert_product(product)
        database.save_vision_analysis(product.product_id, model, fingerprint, payload.asset_ids, raw)
        return product
