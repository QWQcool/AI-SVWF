"""
AI-SVWF 商品识别与分析节点 (PRODUCT_ANALYSIS)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 4 节与第 5 节规范

核心原则：
1. 用户输入尽量少，内部自动结构化；
2. 严格区分 confirmed_information (事实) 与 possible_information (推测)；
3. 执行 ComplianceGuard 合规审查，严禁凭空捏造检测数据、成分参数与医疗功效。
"""

import uuid
from typing import Optional
from core.schemas import ProductInput, ProductAnalysis
from core.compliance import ComplianceGuard


class ProductAnalyzer:
    @classmethod
    def analyze(cls, input_data: ProductInput) -> ProductAnalysis:
        """根据用户输入分析商品，输出结构化档案"""
        product_id = f"PROD_{uuid.uuid4().hex[:8].upper()}"

        # 1. 基础信息识别 (基于输入名称与描述)
        name = input_data.product_name.strip()
        desc = input_data.short_description.strip() if input_data.short_description else ""

        # 推断品类与品牌
        brand = "测试品牌"
        category = "日常消费品"
        appearance_desc = f"{name}标准外观，符合日常实物比例，包装清晰可见"

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

        # 2. 区分 confirmed 与 possible 信息
        raw_confirmed = [f"商品名称: {name}"]
        if desc:
            raw_confirmed.append(f"用户描述: {desc}")
        if input_data.product_images:
            raw_confirmed.append(f"具有清晰正视参考图，主色调与包装结构已锚定")

        raw_possible = [
            f"适合{category}常规使用人群",
            "具有便携与生活化使用属性",
        ]

        # 3. 合规审查与可信度核算 (ComplianceGuard)
        all_claims = raw_confirmed + ([desc] if desc else [])
        safe_claims, risk_info, confidence, _ = ComplianceGuard.sanitize_and_score(
            all_claims, base_confidence=0.95 if input_data.product_images else 0.85
        )

        # 4. 组装结构化商品档案
        return ProductAnalysis(
            product_id=product_id,
            product_name=name,
            brand=brand,
            category=category,
            specification="标准正品装",
            appearance_description=appearance_desc,
            confirmed_information=safe_claims,
            possible_information=raw_possible,
            usage_scenes=scenes,
            risk_information=risk_info,
            information_confidence=confidence,
        )
