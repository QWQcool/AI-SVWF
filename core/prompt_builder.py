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
from core.schemas import ProductAnalysis, PromptSchemaV1, PublicVirtualActor, VideoTemplate, ShotSpec
from core.modules import get_module_text


class PromptBuilder:
    CONFIDENCE_POLICY_MARKER = "【证据充分度策略】"
    LEGACY_CONFIDENCE_POLICY_MARKER = "【商品信息可信度策略】"
    VIRTUAL_ACTOR_POLICY_MARKER = "【公共虚拟人身份锁定】"

    @staticmethod
    def _replace_policy_marker(prompt: str, marker: str, rule: Optional[str]) -> str:
        """Replace client-supplied policy text instead of trusting a marker's presence."""
        output: List[str] = []
        marker_written = False
        for line in str(prompt or "").splitlines():
            if marker not in line:
                output.append(line)
                continue
            prefix = line.split(marker, 1)[0].rstrip()
            if prefix:
                output.append(prefix)
            if rule and not marker_written:
                output.append(f"{marker}{rule}")
                marker_written = True
        if rule and not marker_written:
            output.extend(["", f"{marker}{rule}"])
        return "\n".join(output).strip()

    @staticmethod
    def confidence_policy(product: ProductAnalysis) -> Dict[str, Any]:
        """Translate handoff section 5 confidence bands into prompt behavior."""
        confidence = product.information_confidence
        if confidence >= 0.90:
            level = "high"
            rule = "高可信：可按已确认事实进行普通场景展示，仍不得新增未提供的数据或功效。"
        elif confidence >= 0.70:
            level = "guarded"
            rule = "可生成但禁用强事实宣传：只使用已确认事实和可见外观，不做夸张、比较或保证性表达。"
        elif confidence >= 0.50:
            level = "conservative"
            rule = "低可信保守生成：只做普通商品展示与生活场景使用，不表达具体功效、参数、成分或检测结论。"
        else:
            level = "display_only"
            rule = "低于0.50纯展示：仅展示商品外观、摆放和简单拿起/放回，不生成任何功效型文案或使用效果。"
        return {
            "level": level,
            "rule": rule,
            "allow_unverified_claims": False,
            "manual_review_required": confidence < 0.90,
        }

    @classmethod
    def apply_confidence_policy(cls, prompt: str, product: ProductAnalysis) -> str:
        policy = cls.confidence_policy(product)
        without_legacy = cls._replace_policy_marker(prompt, cls.LEGACY_CONFIDENCE_POLICY_MARKER, None)
        return cls._replace_policy_marker(without_legacy, cls.CONFIDENCE_POLICY_MARKER, policy["rule"])

    @classmethod
    def apply_virtual_actor_policy(
        cls, prompt: str, virtual_actor: Optional[PublicVirtualActor]
    ) -> str:
        rule = None
        if virtual_actor:
            rule = (
                "人物身份必须始终与随任务提交的 reference_image 中同一公共虚拟人一致；"
                "不得换脸、改变年龄或性别，不得生成第二位主要人物。"
            )
        return cls._replace_policy_marker(prompt, cls.VIRTUAL_ACTOR_POLICY_MARKER, rule)

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

    @staticmethod
    def _safe_product_action(product: ProductAnalysis) -> str:
        category_text = f"{product.category} {product.product_name}".lower()
        if any(word in category_text for word in ("防晒", "精华", "面霜", "乳液", "护肤", "化妆")):
            return (
                "单手自然拿起商品并将包装正面稳定朝向镜头，在胸前停留展示；"
                "不涂抹皮肤、不挤出内容物、不新增无法由参考图确认的结构。"
            )
        if any(word in category_text for word in ("充电宝", "移动电源", "数码", "电源")):
            return (
                "单手自然拿起商品，保持可见正面与结构方向稳定，在胸前短暂停留查看；"
                "不虚构接口、指示灯状态、电量或充电效果。"
            )
        return "单手自然拿起商品，在胸前适中位置稳定持握并短暂停留查看，不进行复杂操作。"

    @classmethod
    def compile_shot_prompt(
        cls,
        product: ProductAnalysis,
        shot_id: str = "S01",
        version: str = "1.0",
        custom_action_override: Optional[str] = None,
        custom_camera_override: Optional[str] = None,
        custom_scene_override: Optional[str] = None,
        custom_light_override: Optional[str] = None,
        strengthen_lock: bool = False,
        virtual_actor: Optional[PublicVirtualActor] = None,
    ) -> Dict[str, str]:
        """
        按照标准 11 层顺序装配单个 5 秒镜头的正向与负向提示词
        """
        policy = cls.confidence_policy(product)
        display_only = policy["level"] == "display_only"

        # 1. 镜头目标
        person_label = "同一位公共虚拟人演员" if virtual_actor else "同一位30到40岁普通东亚女性"
        goals = {
            "S01": f"第一帧直接显示{person_label}坐在真实办公或生活空间正常活动，人物与输入参考商品同时已经自然存在于画面中。",
            "S02": f"第一帧保持与上一镜完全一致的真实空间、{person_label}、同一个商品和相同桌面空间关系，随后人物自然将手伸向商品并拿起完成简单使用。",
            "S03": f"第一帧保持同一空间、{person_label}和同一个商品，人物刚完成简单使用动作，右手自然持有商品，随后自然将商品平稳放回桌面并形成清晰产品记忆点。",
        }
        layer_1_goal = goals.get(shot_id, goals["S01"])
        if display_only and shot_id == "S02":
            layer_1_goal = f"保持同一生活场景、{person_label}与同一商品，只进行简单拿起展示，不演示功能或效果。"
        elif display_only and shot_id == "S03":
            layer_1_goal = f"保持同一生活场景、{person_label}与同一商品，将商品平稳放回桌面形成纯外观记忆点。"

        # 2. 人物描述
        if virtual_actor:
            layer_2_person = (
                "使用随任务提交的 reference_image 作为唯一人物身份基准。"
                f"人物目录设定：{virtual_actor.identity_prompt}"
                "允许人物在镜头中自然露出面部；不得重新设计长相、换脸、改变年龄或性别。"
                "服装与场景应保持日常、克制，不要求复现目录故事背景。"
                + get_module_text("REAL_PERSON_001")
                + get_module_text("REAL_PERSON_003")
            )
        else:
            layer_2_person = (
                "女性外貌自然生活化，不是标准网红脸，不是商业模特脸。穿普通简洁日常服装，姿态自然放松，"
                + get_module_text("REAL_PERSON_001")
                + get_module_text("REAL_PERSON_003")
            )

        # 3. 商品信息 (严格锁定)
        product_name = "该商品" if display_only or product.risk_information else product.product_name
        identity_parts = [f"目标商品为【{product_name}】"]
        if product.brand:
            identity_parts.append(f"可确认品牌为【{product.brand}】")
        if product.specification:
            identity_parts.append(f"可见规格为【{product.specification}】")
        if product.appearance_description:
            identity_parts.append(f"外观锚点：{product.appearance_description}")
        if product.observed_information:
            identity_parts.append("图片客观证据：" + "；".join(product.observed_information[:4]))
        layer_3_product = (
            "。".join(identity_parts)
            + "。外形尺寸比例正常。"
            + get_module_text("PRODUCT_LOCK_001")
            + f" {cls.CONFIDENCE_POLICY_MARKER}{policy['rule']}"
        )
        if strengthen_lock:
            layer_3_product += " 特别锁定商品形态，不得发生任何几何拉伸、形变或Logo位置漂移。"

        # 4. 场景描述
        requested_scene = product.preferred_scene or (
            product.usage_scenes[0] if product.usage_scenes else "真实日常桌面"
        )
        scene_desc = custom_scene_override or (
            get_module_text("SCENE_REAL_001")
            + f" 场景具体采用【{requested_scene}】。商品自然摆放在桌面合适位置，符合真实生活摆放逻辑。"
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
            elif display_only:
                layer_5_action = (
                    "人物缓慢单手拿起商品，仅稳定展示参考图可确认的外观，短暂停留后保持静止；"
                    "不操作接口、不涂抹、不饮用、不演示任何功能或效果。"
                )
                layer_6_interaction = "只允许低复杂度拿起展示，手指与商品边界清楚，商品形态和可见文字保持不变。"
            else:
                layer_5_action = (
                    "0到1秒人物继续正常工作；约1秒后，人物自然将右手缓慢伸向桌面商品，手臂协调运动。"
                    + cls._safe_product_action(product)
                    + "节奏平缓，动作不机械。"
                )
                layer_6_interaction = (
                    "手指与商品接触面完全符合真实单手抓握力学，商品具有正常物理重量感，"
                    + get_module_text("PRODUCT_LOCK_002")
                )
        else:  # S03
            layer_5_action = custom_action_override or (
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
        layer_8_light = custom_light_override or get_module_text("LIGHT_001")

        # 9. 人物真实感强化
        layer_9_realism = get_module_text("REAL_PERSON_002")

        # 10. 商品锁定模块
        layer_10_lock = get_module_text("PRODUCT_LOCK_002")
        if product.appearance_description:
            layer_10_lock += f" 全程持续匹配以下外观锚点：{product.appearance_description}。"

        # 11. 负向约束
        compiled_negative = (
            get_module_text("NEGATIVE_001")
            + " "
            + get_module_text("NEGATIVE_002")
            + " 缺失手指、多余肢体、手部扭曲穿模、商品瞬移、背景闪烁、CG塑料质感、过度磨皮。"
            + " 禁止把推测、包装宣称或模型联想写成已证实事实；禁止新增价格、参数、成分、检测数据和功效文案。"
        )
        if virtual_actor:
            compiled_negative += " 禁止人物身份漂移、换脸、年龄或性别变化、出现第二位主要人物。"

        positive_parts = [
            f"【分镜编号: {shot_id} | 版本: V{version}】",
            f"【第 1 层: 镜头目标】{layer_1_goal}",
            f"【第 2 层: 人物描述】{layer_2_person}",
            f"【第 3 层: 商品信息】{layer_3_product}",
            f"【第 4 层: 场景描述】{layer_4_scene}",
            f"【第 5 层: 人物动作】{layer_5_action}",
            f"【第 6 层: 商品交互】{layer_6_interaction}",
            f"【第 7 层: 镜头运镜】{layer_7_camera}",
            f"【第 8 层: 光影质感】{layer_8_light}",
            f"【第 9 层: 真实细节】{layer_9_realism}",
            f"【第 10 层: 形态锁定】{layer_10_lock}",
            f"【第 11 层: 负向合规】{compiled_negative}",
        ]

        compiled_positive = "\n\n".join(positive_parts)

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
        provider: str = "mock",
        model: str = "mock-video-v1",
        virtual_actor: Optional[PublicVirtualActor] = None,
    ) -> PromptSchemaV1:
        """组装完整的交接文档 PromptSchemaV1 JSON 对象"""
        s01_p = cls.compile_shot_prompt(product, "S01", version, virtual_actor=virtual_actor)
        s02_p = cls.compile_shot_prompt(product, "S02", version, virtual_actor=virtual_actor)
        s03_p = cls.compile_shot_prompt(product, "S03", version, virtual_actor=virtual_actor)

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
                "product_images": product.source_images,
                "character_images": [virtual_actor.asset_uri] if virtual_actor else [],
                "reference_video": product.reference_video or None,
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
                "source_level": "L1" if product.source_images else "L0",
                "analysis_source": product.analysis_source,
                "analysis_model": product.analysis_model,
                "source_asset_ids": product.source_asset_ids,
                "information_confidence": product.information_confidence,
                "verified_facts": product.confirmed_information,
                "unverified_facts": product.possible_information,
                "observed_information": product.observed_information,
                "packaging_claims": product.packaging_claims,
                "model_inferences": product.model_inferences,
                "vision_notes": product.vision_notes,
                "allow_unverified_claims": False,
                "confidence_policy": cls.confidence_policy(product),
                "source_image_required_before_real_generation": not bool(product.source_images),
            },
            video_strategy={
                "template_id": "TPL_SCENE_PRODUCT_15S_V1",
                "target_audience": product.target_audience or "待确认",
                "primary_selling_point": (
                    "商品外观展示"
                    if cls.confidence_policy(product)["level"] == "display_only"
                    else (product.confirmed_information[0] if product.confirmed_information else product.product_name)
                ),
                "creative_direction": "real_life",
                "complexity": "low",
            },
            character={
                "required": True,
                "identity_reference": virtual_actor.asset_uri if virtual_actor else None,
                "group_id": virtual_actor.group_id if virtual_actor else None,
                "country": virtual_actor.country if virtual_actor else "东亚",
                "gender": virtual_actor.gender if virtual_actor else "女",
                "age_range": str(virtual_actor.age) if virtual_actor else "30-40",
                "role": virtual_actor.role if virtual_actor else "普通生活化人物",
                "appearance_type": "provider_public_virtual_actor" if virtual_actor else "ordinary_real_person",
                "clothing": "符合场景的简洁日常服装",
                "expression": "natural",
                "eye_contact": "occasional",
                "provider_public": bool(virtual_actor),
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
                "provider": provider,
                "model_name": model,
                "generation_mode": "image_to_video",
                "model_specific_parameters": {},
            },
            prompt_output={
                "compiled_positive_prompt": s01_p["compiled_positive"],
                "compiled_negative_prompt": s01_p["compiled_negative"],
            },
            version={"prompt_version": version, "asset_status": "testing"},
        )
