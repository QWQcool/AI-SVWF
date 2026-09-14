"""
AI-SVWF 商品识别与分析节点 (PRODUCT_ANALYSIS)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 4 节与第 5 节规范

核心原则：
1. 用户输入尽量少，内部自动结构化；
2. 严格区分 confirmed_information (事实) 与 possible_information (推测)；
3. 执行 ComplianceGuard 合规审查，严禁凭空捏造检测数据、成分参数与医疗功效。
"""

import uuid
from typing import List, Optional
from core.schemas import ProductInput, ProductAnalysis, ProductClaim, EvidenceReference, utc_now_iso
from core.compliance import ComplianceGuard


class ProductAnalyzer:
    @classmethod
    def analyze(cls, input_data: ProductInput) -> ProductAnalysis:
        """根据用户输入分析商品，输出结构化档案"""
        product_id = f"PROD_{uuid.uuid4().hex[:8].upper()}"
        now = utc_now_iso()

        # 1. 基础信息识别 (基于输入名称与描述)
        name = input_data.product_name.strip()
        desc = input_data.short_description.strip() if input_data.short_description else ""

        # 品类仅用于选择保守的生活场景，不把推断结果当商品事实。
        brand = ""
        category = "日常消费品"
        appearance_desc = "待人工或视觉模型从真实商品图确认"

        if "咖啡" in name or "杯" in name or "水" in name:
            category = "饮品与生活器皿"
            scenes = ["真实办公室工位桌面", "家庭书桌", "日常使用区"]
        elif "霜" in name or "水" in name or "精华" in name or "乳" in name or "美妆" in name:
            category = "美妆个护"
            scenes = ["梳妆台", "浴室日常台面", "自然采光桌面"]
        elif "茶" in name or "零食" in name or "食品" in name:
            category = "食品饮料"
            scenes = ["办公室茶水间", "家庭客厅茶几", "餐桌"]
        else:
            scenes = ["日常办公桌", "现代家庭生活区", "室内平整桌面"]

        if input_data.preferred_scene:
            scenes.insert(0, input_data.preferred_scene)

        # 2. 构造结构化 ProductClaim 列表
        claims: List[ProductClaim] = []

        # 用户声明：商品名称
        claims.append(
            ProductClaim(
                text=f"用户提供的商品名称: {name}",
                classification="confirmed",
                provenance=[
                    EvidenceReference(
                        source_type="user_declaration",
                        detail="用户明确填写的商品名称",
                        created_at=now,
                    )
                ],
                created_at=now,
                updated_at=now,
            )
        )

        all_user_texts = [f"用户提供的商品名称: {name}"]
        if desc:
            desc_audit = ComplianceGuard.audit_text(desc)
            desc_codes = desc_audit.get("failure_codes", [])
            desc_classification = "rejected" if "CMP002" in desc_codes else ("possible" if desc_codes else "confirmed")
            claims.append(
                ProductClaim(
                    text=f"用户提供的描述: {desc}",
                    classification=desc_classification,
                    provenance=[
                        EvidenceReference(
                            source_type="user_declaration",
                            detail="用户在表单填写的商品卖点与日常描述",
                            created_at=now,
                        )
                    ],
                    compliance_failure_codes=desc_codes,
                    created_at=now,
                    updated_at=now,
                )
            )
            all_user_texts.append(f"用户提供的描述: {desc}")

        # 模型推论 claims
        claims.append(
            ProductClaim(
                text=f"可能属于{category}，需要人工确认",
                classification="possible",
                provenance=[
                    EvidenceReference(
                        source_type="model_inference",
                        detail="规则引擎根据商品名称推测的基础品类",
                        created_at=now,
                    )
                ],
                created_at=now,
                updated_at=now,
            )
        )
        claims.append(
            ProductClaim(
                text="可考虑生活化使用场景，但不得作为商品功效或规格事实",
                classification="possible",
                provenance=[
                    EvidenceReference(
                        source_type="model_inference",
                        detail="生活化场景适配建议",
                        created_at=now,
                    )
                ],
                created_at=now,
                updated_at=now,
            )
        )

        if input_data.product_images:
            claims.append(
                ProductClaim(
                    text="已登记商品参考图，但当前规则引擎未读取图像内容，外观事实仍待视觉识别或人工确认",
                    classification="possible",
                    provenance=[
                        EvidenceReference(
                            source_type="user_declaration",
                            detail="用户登记了商品参考图URL，但当前规则引擎尚未读取像素",
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            claims.append(
                ProductClaim(
                    text="尚未提供商品参考图；真实视频生成前必须补充可访问的商品图",
                    classification="possible",
                    provenance=[
                        EvidenceReference(
                            source_type="model_inference",
                            detail="商品参考图缺失提示",
                            created_at=now,
                        )
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )

        # 3. 合规审查与证据充分度核算 (ComplianceGuard)
        if input_data.product_images:
            raw_score = 0.75 if desc else 0.70
        else:
            raw_score = 0.65 if desc else 0.50

        safe_claims, risk_info, _, failure_codes = ComplianceGuard.sanitize_and_score(
            all_user_texts, base_confidence=raw_score
        )

        compliance_penalty = ComplianceGuard.calculate_compliance_penalty(failure_codes)
        source_coverage = 0.05 if (input_data.product_images and desc) else 0.0
        confidence = ComplianceGuard.calculate_evidence_score(
            raw_score,
            source_coverage=source_coverage,
            failure_codes=failure_codes,
        )

        breakdown = {
            "raw_model_score": raw_score,
            "source_coverage": source_coverage,
            "conflict_penalty": 0.0,
            "occlusion_penalty": 0.0,
            "compliance_penalty": compliance_penalty,
            "human_bonus": 0.0,
            "final_score": confidence,
        }

        evidence_status = "needs_review" if (risk_info or confidence < 0.70) else "analyzed"

        # 4. 组装结构化商品档案
        return ProductAnalysis(
            product_id=product_id,
            product_name=name,
            brand=brand,
            category=category,
            specification="",
            appearance_description=appearance_desc,
            confirmed_information=[c.text for c in claims if c.classification == "confirmed"],
            possible_information=[c.text for c in claims if c.classification == "possible"],
            usage_scenes=scenes,
            risk_information=risk_info,
            evidence_sufficiency=confidence,
            evidence_status=evidence_status,
            evidence_breakdown=breakdown,
            claims=claims,
            information_confidence=confidence,
            source_images=input_data.product_images,
            reference_video=input_data.reference_video,
            target_audience=input_data.target_audience,
            preferred_scene=input_data.preferred_scene,
            source_description=desc,
            analysis_source="manual",
            created_at=now,
            updated_at=now,
        )
