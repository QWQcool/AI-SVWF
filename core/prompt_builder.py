"""
AI-SVWF 11 层 Prompt 自动编译装配器 (PromptBuilder)
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 6、7、8、9、10 节

Prompt Builder 统一按照以下 11 步流水线顺序组装：
1. 镜头目标
2. 人物
3. 商品
4. 场景
5. 人物动作
6. 商品交互
7. 镜头
8. 光线
9. 人物真实感
10. 商品锁定
11. 负向约束

禁止每次让大模型完全自由发挥整条 Prompt。
"""

from typing import Dict, Any, List, Optional
from core.schemas import ProductAnalysis, PromptSchemaV1, VideoTemplate, ShotSpec
from core.modules import get_module_text


class PromptBuilder:
    # 默认 15 秒 3×5s 视频模板 (Section 6.4)
    DEFAULT_TEMPLATE = VideoTemplate(
        template_id="TPL_SCENE_PRODUCT_15S_V1",
        template_name="真实场景体验型带货视频",
        video_type="scene_experience",
        duration_total=15,
        generation_strategy="3x5s",
        aspect_ratio="9:16",
        shots=[
            ShotSpec(
                shot_id="S01",
                duration=5,
                purpose="scene_establish",
                difficulty="low",
                product_interaction="none",
                camera_module="CAMERA_002",
                action_modules=["ACTION_001"],
                product_position="人物右手边桌面",
                start_frame_requirement="第一帧人物已处于真实生活/办公场景，商品自然存在",
                end_frame_requirement="人物保持正常工作微动作，未触碰商品",
            ),
            ShotSpec(
                shot_id="S02",
                duration=5,
                purpose="product_interaction",
                difficulty="medium",
                product_interaction="simple",
                camera_module="CAMERA_002",
                action_modules=["ACTION_002", "ACTION_003", "ACTION_004"],
                product_position="从右手边桌面拿起并单手持握",
                start_frame_requirement="第一帧保持上一镜同一空间、同一人物与同一商品位置",
                end_frame_requirement="完成简单正常使用动作，手部未穿模，商品稳定",
            ),
            ShotSpec(
                shot_id="S03",
                duration=5,
                purpose="product_memory",
                difficulty="low",
                product_interaction="low",
                camera_module="CAMERA_003",
                action_modules=["ACTION_005"],
                product_position="放回右侧桌面靠前位置",
                start_frame_requirement="第一帧保持同一空间，人物右手自然持有商品刚完成使用",
                end_frame_requirement="商品放回稳定，手离开，镜头微推使商品清晰，保留生活痕迹",
            ),
        ],
    )

    @classmethod
    def compile_shot_prompt(
        cls,
        product: ProductAnalysis,
        shot_id: str = "S01",
        version: str = "1.0",
        custom_action_override: Optional[str] = None,
        custom_camera_override: Optional[str] = None,
        strengthen_lock: bool = False,
    ) -> Dict[str, str]:
        """
        按照标准 11 层顺序装配单个 5 秒镜头的正向与负向提示词
        """
        # 1. 镜头目标
        goals = {
            "S01": "第一帧直接显示一名30到40岁普通东亚女性坐在真实办公或生活空间正常活动，人物与输入参考商品同时已经自然存在于画面中。",
            "S02": "第一帧保持与上一镜完全一致的真实空间、同一女性、同一个商品和相同桌面空间关系，随后人物自然将手伸向商品并拿起完成简单使用。",
            "S03": "第一帧保持同一空间、同一女性和同一个商品，女性刚完成简单使用动作，右手自然持有商品，随后自然将商品平稳放回桌面并形成清晰产品记忆点。",
        }
        layer_1_goal = goals.get(shot_id, goals["S01"])

        # 2. 人物描述
        layer_2_person = (
            "女性外貌自然生活化，不是标准网红脸，不是商业模特脸。穿普通简洁日常服装，姿态自然放松，"
            + get_module_text("REAL_PERSON_001")
            + get_module_text("REAL_PERSON_003")
        )

        # 3. 商品信息 (严格锁定)
        product_name = product.product_name
        layer_3_product = (
            f"目标商品为【{product_name}】，外形尺寸比例正常。"
            + get_module_text("PRODUCT_LOCK_001")
        )
        if strengthen_lock:
            layer_3_product += " 特别锁定商品形态，不得发生任何几何拉伸、形变或Logo位置漂移。"

        # 4. 场景描述
        scene_desc = (
            get_module_text("SCENE_REAL_001")
            + f" 商品自然摆放在桌面合适位置，符合真实生活摆放逻辑。"
        )
        layer_4_scene = scene_desc

        # 5. 人物动作 & 6. 商品交互 (分镜头特化)
        if shot_id == "S01":
            layer_5_action = (
                "0到5秒人物继续正常看电脑工作，右手轻微操作鼠标，"
                + get_module_text("REAL_PERSON_002")
                + " 人物全程不主动看镜头，不触碰商品。"
            )
            layer_6_interaction = "人物与商品保持静止空间关系，商品稳定处于桌面，人物不与商品产生肢体触碰。"
        elif shot_id == "S02":
            if custom_action_override:
                layer_5_action = custom_action_override
                layer_6_interaction = "人物单手稳定接触商品，动作克制，不旋转商品，商品稳定拿起。"
            else:
                layer_5_action = (
                    "0到1秒人物继续正常工作；约1秒后，人物自然将右手缓慢伸向桌面商品，手臂协调运动。"
                    "单手自然握住商品并稳定拿至胸前适中位置，随后进行一次极其简单的正常使用动作，节奏平缓，动作不机械。"
                )
                layer_6_interaction = (
                    "手指与商品接触面完全符合真实单手抓握力学，商品具有正常物理重量感，"
                    + get_module_text("PRODUCT_LOCK_002")
                )
        else:  # S03
            layer_5_action = (
                "0到2秒人物自然平稳将商品放回桌面靠前位置；手部自然缓慢离开商品；"
                "3.5到5秒人物自然将注意力与视线重新回到原本工作或生活活动中，表情放松从容。"
            )
            layer_6_interaction = (
                get_module_text("ACTION_005")
                + " 商品放置后物理形态完全静止稳定，不倾倒、不滑动。"
            )

        # 7. 镜头运镜
        if custom_camera_override:
            layer_7_camera = custom_camera_override
        else:
            if shot_id in ["S01", "S02"]:
                layer_7_camera = get_module_text("CAMERA_002") + " " + get_module_text("CAMERA_001")
            else:
                layer_7_camera = get_module_text("CAMERA_003") + " 镜头缓慢轻微靠近商品，使其成为视觉记忆点。"

        # 8. 光线
        layer_8_light = get_module_text("LIGHT_001")

        # 9. 人物真实感强化
        layer_9_realism = get_module_text("REAL_PERSON_002")

        # 10. 商品锁定模块
        layer_10_lock = get_module_text("PRODUCT_LOCK_002")

        # 11. 负向约束
        positive_parts = [
            f"【分镜编号: {shot_id} | 版本: V{version}】",
            layer_1_goal,
            layer_2_person,
            layer_3_product,
            layer_4_scene,
            layer_5_action,
            layer_6_interaction,
            layer_7_camera,
            layer_8_light,
            layer_9_realism,
            layer_10_lock,
        ]

        compiled_positive = "\n\n".join(positive_parts)
        compiled_negative = (
            get_module_text("NEGATIVE_001")
            + " "
            + get_module_text("NEGATIVE_002")
            + " 缺失手指、多余肢体、手部扭曲穿模、商品瞬移、背景闪烁、CG塑料质感、过度磨皮。"
        )

        return {
            "shot_id": shot_id,
            "version": version,
            "compiled_positive": compiled_positive,
            "compiled_negative": compiled_negative,
        }

    @classmethod
    def build_full_schema(
        cls,
        product: ProductAnalysis,
        version: str = "1.0",
        task_id: str = "TASK_001",
    ) -> PromptSchemaV1:
        """组装完整的交接文档 PromptSchemaV1 JSON 对象"""
        s01_p = cls.compile_shot_prompt(product, "S01", version)
        s02_p = cls.compile_shot_prompt(product, "S02", version)
        s03_p = cls.compile_shot_prompt(product, "S03", version)

        return PromptSchemaV1(
            task={
                "task_id": task_id,
                "product_id": product.product_id,
                "video_type": "scene_experience",
                "duration_total": 15,
                "generation_strategy": "3x5s",
                "aspect_ratio": "9:16",
                "language": "zh-CN",
            },
            source_assets={
                "product_images": [f"{product.product_name}_ref.jpg"],
                "character_images": [],
                "reference_video": None,
                "documents": [],
            },
            product={
                "name": product.product_name,
                "brand": product.brand,
                "category": product.category,
                "specification": product.specification,
                "appearance_description": product.appearance_description,
                "confirmed_selling_points": product.confirmed_information,
                "possible_selling_points": product.possible_information,
                "usage_scenes": product.usage_scenes,
                "forbidden_claims": product.risk_information,
                "identity_lock": True,
            },
            evidence={
                "source_level": "L1",
                "information_confidence": product.information_confidence,
                "verified_facts": product.confirmed_information,
                "unverified_facts": product.possible_information,
                "allow_unverified_claims": False,
            },
            video_strategy={
                "template_id": "TPL_SCENE_PRODUCT_15S_V1",
                "target_audience": "日常品质生活追求者",
                "primary_selling_point": product.confirmed_information[0]
                if product.confirmed_information
                else product.product_name,
                "creative_direction": "real_life",
                "complexity": "low",
            },
            character={
                "required": True,
                "identity_reference": None,
                "gender": "female",
                "age_range": "30-40",
                "appearance_type": "ordinary_real_person",
                "clothing": "简洁日常办公室服装",
                "expression": "natural",
                "eye_contact": "occasional",
                "realism_modules": ["REAL_PERSON_001", "REAL_PERSON_002", "REAL_PERSON_003"],
            },
            scene={
                "scene_type": "office_or_home",
                "location": product.usage_scenes[0] if product.usage_scenes else "办公室工位",
                "environment_objects": ["电脑", "水杯", "手机", "常用文具"],
                "realism_level": "high",
                "scene_module": "SCENE_REAL_001",
            },
            lighting={
                "lighting_type": "indoor_natural",
                "lighting_module": "LIGHT_001",
                "brightness": "natural",
                "commercial_lighting": False,
            },
            shots=[
                {
                    "shot_id": "S01",
                    "duration": 5,
                    "prompt": s01_p["compiled_positive"],
                    "negative_prompt": s01_p["compiled_negative"],
                },
                {
                    "shot_id": "S02",
                    "duration": 5,
                    "prompt": s02_p["compiled_positive"],
                    "negative_prompt": s02_p["compiled_negative"],
                },
                {
                    "shot_id": "S03",
                    "duration": 5,
                    "prompt": s03_p["compiled_positive"],
                    "negative_prompt": s03_p["compiled_negative"],
                },
            ],
            product_control={
                "reference_required": True,
                "lock_modules": ["PRODUCT_LOCK_001", "PRODUCT_LOCK_002"],
                "allow_package_redesign": False,
                "allow_logo_change": False,
                "allow_product_count_change": False,
            },
            negative_control={"modules": ["NEGATIVE_001", "NEGATIVE_002"]},
            model={
                "provider": "jimeng",
                "model_name": "jimeng-video-v2",
                "generation_mode": "image_to_video",
                "model_specific_parameters": {},
            },
            prompt_output={
                "compiled_positive_prompt": s01_p["compiled_positive"],
                "compiled_negative_prompt": s01_p["compiled_negative"],
            },
            version={"prompt_version": version, "asset_status": "testing"},
        )
