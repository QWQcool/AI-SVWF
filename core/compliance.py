"""
AI-SVWF 合规审查与风险守护器 (ComplianceGuard)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 4.1 节与第 15 节 (CMP001~CMP003)

硬性拦截五大禁止行为：
1. 未提供的检测数据
2. 未提供的成分参数
3. 未提供的医疗功效
4. 未提供的收益承诺
5. 未验证的“第一、最好、100%”等绝对化极限词
"""

import re
from typing import List, Dict, Any, Tuple


class ComplianceGuard:
    # A percentage can be literal UI/packaging content rather than an
    # advertising promise. Keep this allowlist deliberately narrow: it only
    # covers an objective screen/readout description around ``100%``.
    OBJECTIVE_100_PERCENT_CONTEXT = re.compile(
        r"(?:数显(?:屏)?|显示屏|屏幕|屏上|电量|电池|充电(?:进度)?|进度(?:条)?|读数|界面)"
        r"[^\n。；;%]{0,24}100%"
        r"|100%[^\n。；;%]{0,24}(?:电量|电池|充电完成|进度(?:条)?|数显(?:屏)?|显示屏|屏幕|读数)"
    )

    OBJECTIVE_100_PERCENT_MARKER = re.compile(
        r"数显(?:屏)?|显示屏|屏幕|屏上|电量|电池|充电(?:进度)?|进度(?:条)?|读数|界面"
    )

    PROMOTIONAL_100_PERCENT_CONTEXT = re.compile(
        r"(?:有效率|成功率|满意率|纯度|浓度|功效|效果|保湿|提亮|美白|防晒|修复|"
        r"治愈|治疗|改善|保证|确保|正品|天然|安全|防水|杀菌|抑菌)[^\n。；;%]{0,16}100%"
        r"|100%[^\n。；;%]{0,16}(?:有效|成功|满意|纯度|功效|治愈|治疗|改善|保证|"
        r"正品|天然|安全|防水|杀菌|抑菌|保湿|提亮|美白|防晒|修复)"
    )

    # 1. 绝对化表达拦截模式 (CMP003 | ABSOLUTE_CLAIM)
    ABSOLUTE_PATTERNS = [
        # “第一帧/第一镜/第一层”是制作术语，不属于广告极限词。
        r"第一(?!帧|镜|层|步|次)",
        r"最好",
        r"顶级",
        r"全网最[^\s，,。]+",
        r"100%",
        r"百分之百",
        r"永久",
        r"独家",
        r"唯一",
        r"首选",
        r"极品",
        r"万能",
        r"神级",
        r"史上最[^\s，,。]+",
    ]

    # 2. 医疗与治疗功效拦截模式 (CMP002 | MEDICAL_CLAIM)
    MEDICAL_PATTERNS = [
        r"治疗",
        r"治愈",
        r"消炎",
        r"抗敏",
        r"药用",
        r"降血压",
        r"降血脂",
        r"降血糖",
        r"降三高",
        r"根除",
        r"断根",
        r"防脱生发",
        r"再生",
        r"排毒",
        r"抗癌",
        r"疗效",
    ]

    # 3. 未提供检测数据拦截模式 (CMP001 | UNVERIFIED_CLAIM)
    TEST_DATA_PATTERNS = [
        r"通过[^\s，,。]*检测",
        r"临床验证",
        r"有效率\s*\d+%",
        r"有效率高达",
        r"经权威机构认证",
        r"经国家认证",
        r"诺贝尔奖",
        r"实验证明",
    ]

    # 4. 未提供成分参数拦截模式 (CMP001 | UNVERIFIED_CLAIM)
    INGREDIENT_PATTERNS = [
        r"含[^\s，,。]*\d+%",
        r"添加[^\s，,。]*\d+%",
        r"浓度达\s*\d+%",
        r"纯度\s*100%",
    ]

    # 5. 收益与收益承诺拦截模式
    PROFIT_PATTERNS = [
        r"稳赚",
        r"暴富",
        r"月入[^\s，,。]*",
        r"日入[^\s，,。]*",
        r"零风险",
        r"躺赚",
        r"百分百赚钱",
    ]

    @classmethod
    def _is_objective_100_percent_readout(cls, text: str, match: re.Match[str]) -> bool:
        """Return true only for a literal screen/battery readout description."""
        window_start = max(0, match.start() - 36)
        window = text[window_start: min(len(text), match.end() + 36)]
        target_span = (match.start() - window_start, match.end() - window_start)
        percent_matches = list(re.finditer(r"100%", window))

        def marker_gap(marker: re.Match[str], percent: re.Match[str]) -> int | None:
            if marker.end() <= percent.start():
                between = window[marker.end():percent.start()]
            elif percent.end() <= marker.start():
                between = window[percent.end():marker.start()]
            else:
                return 0
            if len(between) > 24 or re.search(r"[\n。；;%]", between):
                return None
            return len(between)

        objective_for_target = False
        for marker in cls.OBJECTIVE_100_PERCENT_MARKER.finditer(window):
            candidates = [
                (gap, percent)
                for percent in percent_matches
                if (gap := marker_gap(marker, percent)) is not None
            ]
            if not candidates:
                continue
            nearest_gap = min(gap for gap, _ in candidates)
            nearest = [percent for gap, percent in candidates if gap == nearest_gap]
            if len(nearest) == 1 and nearest[0].span() == target_span:
                objective_for_target = True
                break

        def pattern_covers_target(pattern: re.Pattern[str]) -> bool:
            for context_match in pattern.finditer(window):
                for percent_match in re.finditer(r"100%", context_match.group()):
                    percent_span = (
                        context_match.start() + percent_match.start(),
                        context_match.start() + percent_match.end(),
                    )
                    if percent_span == target_span:
                        return True
            return False

        # Bind the exception to this exact occurrence. A nearby objective
        # display must never excuse a second promotional ``100%`` claim.
        return objective_for_target and pattern_covers_target(
            cls.OBJECTIVE_100_PERCENT_CONTEXT
        ) and not (
            pattern_covers_target(cls.PROMOTIONAL_100_PERCENT_CONTEXT)
        )

    @classmethod
    def audit_text(cls, text: str) -> Dict[str, Any]:
        """对单段文本进行全面合规审查"""
        violations: List[Dict[str, str]] = []
        failure_codes: List[str] = []

        # 检查医疗功效
        for pat in cls.MEDICAL_PATTERNS:
            match = re.search(pat, text)
            if match:
                violations.append({
                    "type": "MEDICAL_CLAIM",
                    "code": "CMP002",
                    "matched": match.group(0),
                    "reason": "出现医疗、治疗、消炎等涉医功效表达 (严重违规)",
                })
                if "CMP002" not in failure_codes:
                    failure_codes.append("CMP002")

        # 检查绝对化表达
        for pat in cls.ABSOLUTE_PATTERNS:
            for match in re.finditer(pat, text):
                if pat == r"100%" and cls._is_objective_100_percent_readout(text, match):
                    continue
                violations.append({
                    "type": "ABSOLUTE_CLAIM",
                    "code": "CMP003",
                    "matched": match.group(0),
                    "reason": "出现第一、最好、100%等绝对化表达",
                })
                if "CMP003" not in failure_codes:
                    failure_codes.append("CMP003")
                break

        # 检查检测数据
        for pat in cls.TEST_DATA_PATTERNS:
            match = re.search(pat, text)
            if match:
                violations.append({
                    "type": "UNVERIFIED_TEST_DATA",
                    "code": "CMP001",
                    "matched": match.group(0),
                    "reason": "出现未经验证的检测报告或权威数据宣称",
                })
                if "CMP001" not in failure_codes:
                    failure_codes.append("CMP001")

        # 检查成分参数
        for pat in cls.INGREDIENT_PATTERNS:
            match = re.search(pat, text)
            if match:
                violations.append({
                    "type": "UNVERIFIED_INGREDIENT",
                    "code": "CMP001",
                    "matched": match.group(0),
                    "reason": "出现未经验证的具体成分百分比或浓度参数",
                })
                if "CMP001" not in failure_codes:
                    failure_codes.append("CMP001")

        # 检查收益承诺
        for pat in cls.PROFIT_PATTERNS:
            match = re.search(pat, text)
            if match:
                violations.append({
                    "type": "PROFIT_PROMISE",
                    "code": "CMP001",
                    "matched": match.group(0),
                    "reason": "出现未经证实的收益与暴富承诺",
                })
                if "CMP001" not in failure_codes:
                    failure_codes.append("CMP001")

        is_clean = len(violations) == 0
        return {
            "is_clean": is_clean,
            "violations": violations,
            "failure_codes": failure_codes,
        }

    @classmethod
    def audit_prompt_assertions(cls, prompt: str) -> Dict[str, Any]:
        """Audit positive claims while ignoring explicit prohibition/repair sentences."""
        constraint_words = ("禁止", "不得", "避免", "移除", "不生成", "严禁", "不可", "不能")
        technical_identity_phrases = (
            "唯一产品身份基准",
            "唯一商品身份基准",
            "唯一人物身份基准",
            "唯一人物身份",
            "唯一商品身份来源",
            "唯一主动作",
        )
        assertive_parts = []
        for part in re.split(r"[\n。；;]", str(prompt or "")):
            cleaned = part.strip()
            if cleaned and not any(word in cleaned for word in constraint_words):
                for phrase in technical_identity_phrases:
                    cleaned = cleaned.replace(phrase, "身份基准")
                assertive_parts.append(cleaned)
        return cls.audit_text("\n".join(assertive_parts))

    @classmethod
    def calculate_compliance_penalty(cls, failure_codes: List[str]) -> float:
        penalty = 0.0
        if "CMP002" in failure_codes:
            penalty += 0.40
        if "CMP003" in failure_codes:
            penalty += 0.15
        if "CMP001" in failure_codes:
            penalty += 0.10
        return round(penalty, 2)

    @classmethod
    def calculate_evidence_score(
        cls,
        raw_model_score: float,
        *,
        source_coverage: float = 0.0,
        conflict_penalty: float = 0.0,
        occlusion_penalty: float = 0.0,
        failure_codes: List[str] | None = None,
        human_bonus: float = 0.0,
    ) -> float:
        """Calculate the authoritative evidence score from its persisted parts."""
        codes = list(dict.fromkeys(failure_codes or []))
        score = (
            float(raw_model_score)
            + float(source_coverage)
            - float(conflict_penalty)
            - float(occlusion_penalty)
            - cls.calculate_compliance_penalty(codes)
            + float(human_bonus)
        )
        if "CMP002" in codes:
            score = min(score, 0.45)
        return round(max(0.0, min(1.0, score)), 2)

    @classmethod
    def sanitize_and_score(
        cls, raw_claims: List[str], base_confidence: float = 1.0
    ) -> Tuple[List[str], List[str], float, List[str]]:
        """
        对卖点列表进行批量审查与过滤:
        返回: (合规卖点列表, 违规风险项列表, 最终证据充分度, Failure Codes)
        """
        safe_claims: List[str] = []
        risk_claims: List[str] = []
        all_failure_codes: List[str] = []

        for claim in raw_claims:
            audit = cls.audit_text(claim)
            if audit["is_clean"]:
                safe_claims.append(claim)
            else:
                for v in audit["violations"]:
                    risk_claims.append(f"【{v['matched']}】: {v['reason']}")
                for code in audit["failure_codes"]:
                    if code not in all_failure_codes:
                        all_failure_codes.append(code)

        final_confidence = cls.calculate_evidence_score(
            base_confidence,
            failure_codes=all_failure_codes,
        )
        return safe_claims, risk_claims, final_confidence, all_failure_codes
