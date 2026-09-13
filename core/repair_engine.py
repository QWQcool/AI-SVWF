"""
AI-SVWF 缺陷定位与单镜头修复引擎 (RepairEngine)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 15、16、17、19 节规范

核心机制：
检测 Failure Code -> 自动匹配 repair_action -> 编译 V1.1 针对性修复 Prompt -> 【仅单独重跑对应 shot_id】
"""

from typing import List, Dict, Any, Tuple, Optional
from core.schemas import ProductAnalysis
from core.prompt_builder import PromptBuilder


class RepairEngine:
    # Failure Code 知识映射库 (Section 15)
    FAILURE_CODE_MAP: Dict[str, Dict[str, str]] = {
        # PERSON
        "PER001": {
            "name": "AI_FACE",
            "symptom": "明显AI脸、塑料脸、蜡像感",
            "action": "strengthen_real_person_prompt",
        },
        "PER002": {
            "name": "IDENTITY_DRIFT",
            "symptom": "人物前后不像同一个人",
            "action": "strengthen_character_identity",
        },
        "PER003": {
            "name": "SKIN_OVER_SMOOTH",
            "symptom": "皮肤过度磨皮、没有真实纹理",
            "action": "strengthen_skin_texture",
        },
        # HAND
        "HAND001": {
            "name": "FINGER_DEFORMATION",
            "symptom": "多指、少指、粘连、融合",
            "action": "reduce_action_complexity",
        },
        "HAND002": {
            "name": "BODY_DEFORMATION",
            "symptom": "手臂、肩膀、身体结构异常",
            "action": "reduce_motion_range",
        },
        "HAND003": {
            "name": "OBJECT_PENETRATION",
            "symptom": "手穿过商品或商品穿模",
            "action": "simplify_grip",
        },
        # PRODUCT
        "PRO001": {
            "name": "PRODUCT_DEFORMATION",
            "symptom": "商品拉伸、缩小、结构变化",
            "action": "strengthen_product_lock",
        },
        "PRO002": {
            "name": "PRODUCT_IDENTITY_CHANGE",
            "symptom": "商品变成另一款商品",
            "action": "hard_fail_regenerate",
        },
        "PRO003": {
            "name": "LOGO_ERROR",
            "symptom": "Logo变化、Logo位置错误、品牌字错误",
            "action": "avoid_logo_closeup",
        },
        "PRO004": {
            "name": "PACKAGE_TEXT_ERROR",
            "symptom": "包装文字乱码或被重绘",
            "action": "disable_model_generated_text",
        },
        "PRO005": {
            "name": "PRODUCT_COUNT_CHANGE",
            "symptom": "一件产品突然变成多件",
            "action": "enforce_single_product",
        },
        # MOTION
        "MOT001": {
            "name": "ROBOTIC_MOTION",
            "symptom": "动作僵硬、机器人感",
            "action": "strengthen_natural_micro_motion",
        },
        "MOT002": {
            "name": "ACTION_TOO_FAST",
            "symptom": "动作节奏过快",
            "action": "slow_action",
        },
        "MOT003": {
            "name": "ACTION_JUMP",
            "symptom": "突然跳帧、瞬移、动作不连续",
            "action": "regenerate_shot",
        },
        "MOT004": {
            "name": "ACTION_NOT_FOLLOWED",
            "symptom": "模型没有执行指定动作",
            "action": "move_primary_action_earlier",
        },
        # CAMERA
        "CAM001": {
            "name": "CAMERA_OVERMOVE",
            "symptom": "推拉摇移过大、乱运镜",
            "action": "switch_fixed_camera",
        },
        "CAM002": {
            "name": "CAMERA_SHAKE",
            "symptom": "抖动过大",
            "action": "reduce_handheld_strength",
        },
        # SCENE & TEXT & COMPLIANCE
        "SCN001": {
            "name": "SCENE_AI_LOOK",
            "symptom": "环境太假、棚拍感严重",
            "action": "add_life_traces",
        },
        "TXT001": {
            "name": "TEXT_GARBLED",
            "symptom": "文字乱码",
            "action": "disable_model_generated_text",
        },
        "CMP001": {
            "name": "UNVERIFIED_CLAIM",
            "symptom": "AI自行添加未经证实的卖点",
            "action": "remove_unverified_claim",
        },
    }

    @classmethod
    def resolve_repair_actions(cls, failure_codes: List[str]) -> List[str]:
        """将故障代码列表映射为标准修复动作指令"""
        actions = []
        for code in failure_codes:
            if code in cls.FAILURE_CODE_MAP:
                act = cls.FAILURE_CODE_MAP[code]["action"]
                if act not in actions:
                    actions.append(act)
        return actions

    @classmethod
    def generate_v1_1_prompt(
        cls,
        product: ProductAnalysis,
        shot_id: str,
        current_version: str = "1.0",
        failure_codes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        根据 Failure Code 针对性构建 V1.1 修复提示词 (Section 19)
        仅针对失败镜头定制，保留核心资产一致性
        """
        codes = failure_codes or []
        actions = cls.resolve_repair_actions(codes)

        # 步进版本号
        try:
            major, minor = current_version.split(".")
            next_version = f"{major}.{int(minor) + 1}"
        except Exception:
            next_version = "1.1"

        action_override = None
        camera_override = None
        strengthen_lock = False

        # 核心场景: 如果 S02 命中 HAND001 (手指畸形) 或 PRO001 (形变)
        # 按照文档第 19 节规范：将原动作"伸手->拿起->使用"降级为"伸手->拿起"，并强化商品锁定与固定机位
        if "reduce_action_complexity" in actions or "HAND001" in codes:
            action_override = (
                "【降级优化动作】0到1.5秒人物继续正常工作看电脑；"
                "随后人物极其缓慢自然将右手单手伸向桌面商品，稳稳握住商品下部并缓慢提起至桌面正上方5厘米稳定悬停，"
                "不进行任何开盖、饮用或复杂操作，手指保持单手平稳抓握，手腕动作幅度极小。"
            )

        if "strengthen_product_lock" in actions or "PRO001" in codes:
            strengthen_lock = True

        if "switch_fixed_camera" in actions or "CAM001" in codes:
            camera_override = "采用纯正前方中景绝对固定机位，完全无任何推拉摇移，保持构图基准线完全稳定。"

        compiled = PromptBuilder.compile_shot_prompt(
            product=product,
            shot_id=shot_id,
            version=next_version,
            custom_action_override=action_override,
            custom_camera_override=camera_override,
            strengthen_lock=strengthen_lock,
        )

        return {
            "shot_id": shot_id,
            "previous_version": current_version,
            "next_version": next_version,
            "failure_codes": codes,
            "repair_actions": actions,
            "compiled_positive": compiled["compiled_positive"],
            "compiled_negative": compiled["compiled_negative"],
        }
