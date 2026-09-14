"""Bounded deterministic prompt variant planning.

Variants change a small, auditable set of axes. This deliberately avoids a
Cartesian explosion and never lets an LLM rewrite the complete 11-layer prompt.
"""

import hashlib
from typing import Dict, List

from core.database import WorkflowDatabase, database
from core.modules import get_module_text
from core.prompt_builder import PromptBuilder
from core.schemas import ProductAnalysis, PromptVariant, PromptVariantPlanRequest


class PromptVariantPlanner:
    SCENES = [
        get_module_text("SCENE_REAL_001"),
        get_module_text("SCENE_REAL_002"),
        get_module_text("SCENE_REAL_003"),
    ]
    CAMERAS = [
        get_module_text("CAMERA_002"),
        get_module_text("CAMERA_001"),
        "固定中景机位，画面稳定，主体始终位于安全构图区，不进行推拉摇移。",
    ]
    LIGHTING = [get_module_text("LIGHT_001"), get_module_text("LIGHT_002")]
    LOCKS = ["standard", "double", "strict"]
    ACTIONS = {
        "S01": [
            "人物继续自然工作，仅有眨眼、呼吸和轻微鼠标操作，不看镜头，不接触商品。",
            "人物自然阅读屏幕并偶尔移动鼠标，动作幅度小，商品始终静止在桌面。",
            "人物保持放松坐姿完成单一日常工作动作，全程不表演、不触碰商品。",
        ],
        "S02": [
            "人物缓慢伸出一只手，单手稳稳拿起商品至桌面上方并停住，不执行开盖或饮用。",
            "人物先保持工作状态，再以小幅度单手抓握商品并缓慢提起，动作连续且只有一个主动作。",
            "人物自然伸手接触商品，稳定拿起后保持，手腕不旋转，另一只手不参与。",
        ],
        "S03": [
            "人物将商品缓慢放回原位，手部离开后商品保持稳定，再自然回到原活动。",
            "人物先短暂停留展示商品，再匀速放回桌面，手指自然松开并离开。",
            "人物单手平稳降低商品并放置于固定位置，不滑动、不倾倒，随后视线回到工作。",
        ],
    }

    @classmethod
    def plan(
        cls,
        product: ProductAnalysis,
        request: PromptVariantPlanRequest,
        repo: WorkflowDatabase = database,
    ) -> List[PromptVariant]:
        axes_requested = set(request.axes)
        variants: List[PromptVariant] = []
        seen = set()

        for shot_id in request.shot_ids:
            for index in range(request.variants_per_shot):
                axes: Dict[str, str] = {}
                kwargs = {}
                if "scene" in axes_requested:
                    scene = cls.SCENES[index % len(cls.SCENES)]
                    axes["scene"] = f"SCENE_{index % len(cls.SCENES) + 1}"
                    kwargs["custom_scene_override"] = scene
                if "action" in axes_requested:
                    action = cls.ACTIONS[shot_id][index % len(cls.ACTIONS[shot_id])]
                    axes["action"] = f"ACTION_SAFE_{index % len(cls.ACTIONS[shot_id]) + 1}"
                    kwargs["custom_action_override"] = action
                if "camera" in axes_requested:
                    camera = cls.CAMERAS[index % len(cls.CAMERAS)]
                    axes["camera"] = f"CAMERA_SAFE_{index % len(cls.CAMERAS) + 1}"
                    kwargs["custom_camera_override"] = camera
                if "lighting" in axes_requested:
                    lighting = cls.LIGHTING[index % len(cls.LIGHTING)]
                    axes["lighting"] = f"LIGHT_{index % len(cls.LIGHTING) + 1}"
                    kwargs["custom_light_override"] = lighting
                if "product_lock" in axes_requested:
                    lock = cls.LOCKS[index % len(cls.LOCKS)]
                    axes["product_lock"] = lock
                    kwargs["strengthen_lock"] = lock != "standard"

                prompt_version = f"{request.base_version}-v{index + 1:02d}"
                compiled = PromptBuilder.compile_shot_prompt(
                    product=product,
                    shot_id=shot_id,
                    version=prompt_version,
                    virtual_actor=product.virtual_actor,
                    **kwargs,
                )
                combined = compiled["compiled_positive"] + "\n" + compiled["compiled_negative"]
                fingerprint = hashlib.sha256(combined.encode("utf-8")).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                variant = PromptVariant(
                    variant_id=f"VAR_{fingerprint[:12].upper()}",
                    product_id=product.product_id,
                    shot_id=shot_id,
                    prompt_version=prompt_version,
                    variant_index=index + 1,
                    axes=axes,
                    prompt_text=compiled["compiled_positive"],
                    negative_prompt=compiled["compiled_negative"],
                    fingerprint=fingerprint,
                )
                repo.save_variant(variant)
                variants.append(variant)

        return variants
