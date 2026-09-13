"""
AI-SVWF QA 质量验收评分引擎 (QAEngine)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 14 节规范

1. 100 分制加权评分模型:
   - 商品一致性 (20)
   - 人物真实性 (15)
   - 动作自然度 (15)
   - 手部与肢体 (10)
   - Prompt遵循度 (10)
   - 场景真实性 (10)
   - 镜头合理性 (5)
   - 画面稳定性 (5)
   - 信息准确性 (5)
   - 合规性 (5)

2. 验收区间:
   - QA >= 85: PASS
   - 70 <= QA < 85: REPAIR
   - QA < 70: FAIL

3. 7 项 HARD FAIL 强制拦截 (不看总分直接判 FAIL)
"""

from typing import Dict, Any, Tuple, Optional
from core.schemas import QARecordInput, QAStatus


class QAEngine:
    HARD_FAILS: Dict[str, str] = {
        "HARD_FAIL_01": "商品明显变成其他商品",
        "HARD_FAIL_02": "Logo明显错误",
        "HARD_FAIL_03": "商品严重变形",
        "HARD_FAIL_04": "人物出现严重肢体畸形",
        "HARD_FAIL_05": "出现未经资料证明的医疗、功效、收益事实",
        "HARD_FAIL_06": "生成错误价格、规格等关键商品信息",
        "HARD_FAIL_07": "出现无法接受的严重文字乱码",
    }

    @classmethod
    def evaluate(cls, qa_input: QARecordInput) -> Tuple[int, QAStatus, Dict[str, Any]]:
        """
        计算 QA 评分并得出验收结论状态
        返回: (总分, QAStatus, 详细诊断报告)
        """
        # 1. 检查是否命中强制失败 (HARD FAIL)
        if qa_input.hard_fail_code and qa_input.hard_fail_code in cls.HARD_FAILS:
            return (
                min(50, qa_input.score_product_consistency),
                QAStatus.FAIL,
                {
                    "is_hard_fail": True,
                    "hard_fail_code": qa_input.hard_fail_code,
                    "reason": cls.HARD_FAILS[qa_input.hard_fail_code],
                    "recommendation": "触发致命硬缺陷，直接标记为FAIL，禁止进入下游合成",
                },
            )

        # 2. 计算加权总分
        total_score = (
            qa_input.score_product_consistency
            + qa_input.score_person_realism
            + qa_input.score_action_naturalness
            + qa_input.score_hand_limb
            + qa_input.score_prompt_following
            + qa_input.score_scene_realism
            + qa_input.score_camera_rationality
            + qa_input.score_frame_stability
            + qa_input.score_info_accuracy
            + qa_input.score_compliance
        )

        # 3. 判定验收状态 (Section 14.1)
        if total_score >= 85:
            status = QAStatus.PASS
        elif total_score >= 70:
            status = QAStatus.REPAIR
        else:
            status = QAStatus.FAIL

        report = {
            "total_score": total_score,
            "status": status.value,
            "is_hard_fail": False,
            "sub_scores": {
                "product_consistency": qa_input.score_product_consistency,
                "person_realism": qa_input.score_person_realism,
                "action_naturalness": qa_input.score_action_naturalness,
                "hand_limb": qa_input.score_hand_limb,
                "prompt_following": qa_input.score_prompt_following,
                "scene_realism": qa_input.score_scene_realism,
                "camera_rationality": qa_input.score_camera_rationality,
                "frame_stability": qa_input.score_frame_stability,
                "info_accuracy": qa_input.score_info_accuracy,
                "compliance": qa_input.score_compliance,
            },
            "failure_codes": qa_input.failure_codes,
            "failure_notes": qa_input.failure_notes,
        }

        return total_score, status, report
