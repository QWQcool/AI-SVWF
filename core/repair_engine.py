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
        "CAM003": {
            "name": "FRAMING_ERROR",
            "symptom": "主体出画、构图不合理或商品被裁切",
            "action": "recenter_safe_framing",
        },
        # SCENE & TEXT & COMPLIANCE
        "SCN001": {
            "name": "SCENE_AI_LOOK",
            "symptom": "环境太假、棚拍感严重",
            "action": "add_life_traces",
        },
        "SCN002": {
            "name": "BACKGROUND_MUTATION",
            "symptom": "背景结构在镜头中发生变化",
            "action": "lock_background_layout",
        },
        "SCN003": {
            "name": "OBJECT_FLICKER",
            "symptom": "背景物体闪烁、消失或新增",
            "action": "reduce_background_objects",
        },
        "TXT001": {
            "name": "TEXT_GARBLED",
            "symptom": "文字乱码",
            "action": "disable_model_generated_text",
        },
        "TXT002": {
            "name": "PRODUCT_INFO_WRONG",
            "symptom": "生成了错误的价格、规格或商品文字",
            "action": "remove_generated_product_info",
        },
        "CMP001": {
            "name": "UNVERIFIED_CLAIM",
            "symptom": "AI自行添加未经证实的卖点",
            "action": "remove_unverified_claim",
        },
        "CMP002": {
            "name": "MEDICAL_CLAIM",
            "symptom": "出现医疗、治疗或疾病功效表达",
            "action": "remove_medical_claim",
        },
        "CMP003": {
            "name": "ABSOLUTE_CLAIM",
            "symptom": "出现第一、最好或保证性绝对表达",
            "action": "remove_absolute_claim",
        },
    }

    REPAIR_PROMPT_RULES: Dict[str, str] = {
        "strengthen_real_person_prompt": "强化真实人物皮肤纹理和自然微表情，禁止塑料脸与蜡像感。",
        "strengthen_character_identity": "锁定同一人物的脸型、发型、年龄、服装和体态，不得身份漂移。",
        "strengthen_skin_texture": "保留毛孔、细纹和自然肤色变化，禁止过度磨皮。",
        "reduce_action_complexity": "只保留一个简单主动作，禁止多步骤复合操作。",
        "reduce_motion_range": "缩小手臂与肩部动作幅度，保持符合人体结构的自然姿态。",
        "simplify_grip": "采用单手低复杂度抓握，手指与商品边界清晰，禁止穿模。",
        "strengthen_product_lock": "双重锁定商品轮廓、比例、颜色、Logo位置和数量。",
        "hard_fail_regenerate": "严格以输入商品参考图为唯一商品身份来源，禁止替换成相似商品。",
        "avoid_logo_closeup": "避免Logo特写，不重绘、不改写包装文字。",
        "disable_model_generated_text": "画面内不得生成任何新增文字，文字统一留到后期叠加。",
        "enforce_single_product": "画面中始终只保留一个目标商品，不复制、不增减。",
        "strengthen_natural_micro_motion": "加入自然呼吸和微小停顿，动作速度连续且不机械。",
        "slow_action": "整体动作明显放慢，匀速完成并保留动作前后停顿。",
        "regenerate_shot": "保持首尾状态连续，禁止瞬移、跳帧和动作突变。",
        "move_primary_action_earlier": "将唯一主动作提前到镜头前半段并明确动作终点。",
        "switch_fixed_camera": "改用固定机位，禁止推拉摇移。",
        "reduce_handheld_strength": "取消明显手持抖动，仅允许不可感知的自然微漂移。",
        "recenter_safe_framing": "主体置于安全构图区，人物手部和商品全程不得出画。",
        "add_life_traces": "增加克制的真实生活痕迹，避免样板间和摄影棚质感。",
        "lock_background_layout": "锁定背景物体的位置、数量、形态和光照关系。",
        "reduce_background_objects": "减少背景物体数量，禁止背景物体闪烁、增减和变形。",
        "remove_unverified_claim": "移除所有未经商品档案确认的卖点、参数和功效。",
        "remove_generated_product_info": "不得生成价格、规格、成分或包装文字信息。",
        "remove_medical_claim": "移除医疗、治疗、疾病和疗效表达。",
        "remove_absolute_claim": "移除第一、最好、保证、绝对化和收益承诺表达。",
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

    @staticmethod
    def next_version(current_version: str) -> str:
        """Increment a numeric prompt version without overwriting history."""
        try:
            major, minor = current_version.split(".")
            return f"{int(major)}.{int(minor) + 1}"
        except (TypeError, ValueError):
            return "1.1"

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
        next_version = cls.next_version(current_version)

        action_override = None
        camera_override = None
        strengthen_lock = False

        # 核心场景: 针对 S01, S02, S03 提供精准的因果归因降级规则
        if shot_id == "S01":
            if "switch_fixed_camera" in actions or "CAM001" in codes or "CAM002" in codes:
                camera_override = "采用纯正前方生活化中景绝对固定机位，完全无任何推拉摇移与机位晃动，保持办公桌面构图完全稳定。"
            if "strengthen_real_person_prompt" in actions or "PER001" in codes or "SCN001" in codes:
                action_override = "0到5秒人物自然专注于电脑屏幕正常办公，仅有极细微的自然呼吸与偶发的眼部眨动，全程不看镜头，消除任何表演感与生硬转头动作。"

        elif shot_id == "S02":
            # 按照文档第 19 节核心规范：将原复合动作"伸手->拿起->使用"降级为"伸手->拿起"，并强化商品锁定与固定机位
            if "reduce_action_complexity" in actions or "HAND001" in codes or "HAND003" in codes:
                action_override = (
                    "【降级优化动作】0到1.5秒人物继续正常工作看电脑；"
                    "随后人物极其缓慢自然将右手单手伸向桌面商品，稳稳握住商品下部并缓慢提起至桌面正上方5厘米稳定悬停，"
                    "不进行任何开盖、饮用或复杂操作，手指保持单手平稳抓握，手腕动作幅度极小。"
                )
            if "strengthen_product_lock" in actions or "PRO001" in codes:
                strengthen_lock = True
            if "switch_fixed_camera" in actions or "CAM001" in codes:
                camera_override = "采用纯正前方中景绝对固定机位，完全无任何推拉摇移，保持构图基准线完全稳定。"

        elif shot_id == "S03":
            # S03 放回动作优化: 解决 MOT002(动作过快抽搐) 与 PRO001(放回变形)
            if "slow_action" in actions or "MOT002" in codes or "MOT001" in codes:
                action_override = (
                    "【放缓优化动作】0到2秒人物手持商品稳定定格于胸前，形成清晰产品记忆点；"
                    "2到4秒手部极其缓慢平稳地将商品放回原位桌面，速度均匀柔和；"
                    "4到5秒手部自然平稳移开，商品静止于桌面正前方。"
                )
            if "strengthen_product_lock" in actions or "PRO001" in codes:
                strengthen_lock = True
            if "switch_fixed_camera" in actions or "CAM001" in codes:
                camera_override = "采用标准中景轻微缓慢推近，聚焦放回后的商品主体，画面丝滑平稳无突变。"

        compiled = PromptBuilder.compile_shot_prompt(
            product=product,
            shot_id=shot_id,
            version=next_version,
            custom_action_override=action_override,
            custom_camera_override=camera_override,
            strengthen_lock=strengthen_lock,
        )

        extra_rules = [cls.REPAIR_PROMPT_RULES[action] for action in actions if action in cls.REPAIR_PROMPT_RULES]
        if extra_rules:
            compiled["compiled_positive"] += "\n\n【Failure Code 定向修复约束】" + " ".join(extra_rules)

        return {
            "shot_id": shot_id,
            "previous_version": current_version,
            "next_version": next_version,
            "failure_codes": codes,
            "repair_actions": actions,
            "compiled_positive": compiled["compiled_positive"],
            "compiled_negative": compiled["compiled_negative"],
        }
