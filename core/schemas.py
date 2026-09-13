"""
AI-SVWF 核心数据类型规范
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》中的各类 Schema
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# ------------------------------------------------------------------------------
# 1. 任务状态枚举 (Section 12)
# ------------------------------------------------------------------------------
class TaskStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QA_PENDING = "QA_PENDING"
    PASS = "PASS"
    REPAIR = "REPAIR"
    REJECTED = "REJECTED"


class QAStatus(str, Enum):
    PASS = "PASS"
    REPAIR = "REPAIR"
    FAIL = "FAIL"


# ------------------------------------------------------------------------------
# 2. 用户最小输入 (Section 3)
# ------------------------------------------------------------------------------
class ProductInput(BaseModel):
    product_name: str = Field(..., description="测试商品名称")
    product_images: List[str] = Field(default_factory=list, description="商品正面图URL/本地路径列表")
    short_description: Optional[str] = Field(default="", description="用户可选描述")
    reference_video: Optional[str] = Field(default="", description="参考视频")
    target_audience: Optional[str] = Field(default="", description="目标受众")
    preferred_scene: Optional[str] = Field(default="", description="偏好场景")


# ------------------------------------------------------------------------------
# 3. 内部商品识别与可信度档案 (Section 4 & 5)
# ------------------------------------------------------------------------------
class ProductAnalysis(BaseModel):
    product_id: str = Field(..., description="内部商品唯一ID")
    product_name: str = Field(..., description="商品名称")
    brand: str = Field(default="", description="品牌")
    category: str = Field(default="", description="品类")
    specification: str = Field(default="", description="规格")
    appearance_description: str = Field(default="", description="外观形态描述")
    confirmed_information: List[str] = Field(
        default_factory=list, description="已确认事实信息 (仅限从图片/用户明确提供的真实信息)"
    )
    possible_information: List[str] = Field(
        default_factory=list, description="可能性推测信息 (不能自动作为事实卖点)"
    )
    usage_scenes: List[str] = Field(
        default_factory=list, description="适用日常生活/办公场景"
    )
    risk_information: List[str] = Field(
        default_factory=list, description="检测到的合规风险信息"
    )
    information_confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="信息可信度评分 0.0~1.0"
    )


# ------------------------------------------------------------------------------
# 4. 单镜头分镜规格 (Section 6)
# ------------------------------------------------------------------------------
class ShotSpec(BaseModel):
    shot_id: str = Field(..., description="镜头编号: S01 | S02 | S03")
    duration: int = Field(default=5, description="镜头时长 (秒)")
    purpose: str = Field(..., description="镜头目的: scene_establish | product_interaction | product_memory")
    difficulty: str = Field(default="low", description="难度评级: low | medium | high")
    product_interaction: str = Field(default="none", description="商品互动程度: none | simple | low")
    camera_module: str = Field(default="", description="镜头运镜模块ID")
    action_modules: List[str] = Field(default_factory=list, description="动作模块ID列表")
    product_position: str = Field(default="", description="商品在画面中的位置")
    start_frame_requirement: str = Field(default="", description="首帧锁定要求")
    end_frame_requirement: str = Field(default="", description="尾帧要求")


# ------------------------------------------------------------------------------
# 5. 15秒视频模板定义 (Section 6.4)
# ------------------------------------------------------------------------------
class VideoTemplate(BaseModel):
    template_id: str = Field(default="TPL_SCENE_PRODUCT_15S_V1")
    template_name: str = Field(default="真实场景体验型带货视频")
    video_type: str = Field(default="scene_experience")
    duration_total: int = Field(default=15)
    generation_strategy: str = Field(default="3x5s")
    aspect_ratio: str = Field(default="9:16")
    shots: List[ShotSpec] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# 6. Prompt Schema V1.0 完整定义 (Section 8)
# ------------------------------------------------------------------------------
class PromptSchemaV1(BaseModel):
    schema_version: str = "1.0"
    task: Dict[str, Any] = Field(default_factory=dict)
    source_assets: Dict[str, Any] = Field(default_factory=dict)
    product: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    video_strategy: Dict[str, Any] = Field(default_factory=dict)
    character: Dict[str, Any] = Field(default_factory=dict)
    scene: Dict[str, Any] = Field(default_factory=dict)
    lighting: Dict[str, Any] = Field(default_factory=dict)
    shots: List[Dict[str, Any]] = Field(default_factory=list)
    product_control: Dict[str, Any] = Field(default_factory=dict)
    negative_control: Dict[str, Any] = Field(default_factory=dict)
    text_policy: Dict[str, bool] = Field(
        default_factory=lambda: {
            "allow_model_generated_text": False,
            "overlay_text_in_post": True,
        }
    )
    audio_policy: Dict[str, bool] = Field(
        default_factory=lambda: {
            "generate_voice_in_video_model": False,
            "voiceover_post_process": True,
            "background_music": False,
        }
    )
    model: Dict[str, Any] = Field(default_factory=dict)
    prompt_output: Dict[str, str] = Field(
        default_factory=lambda: {
            "compiled_positive_prompt": "",
            "compiled_negative_prompt": "",
        }
    )
    version: Dict[str, str] = Field(
        default_factory=lambda: {
            "prompt_version": "1.0",
            "asset_status": "testing",
        }
    )


# ------------------------------------------------------------------------------
# 7. 每次生成记录数据 (Section 13 & 24)
# ------------------------------------------------------------------------------
class VideoTaskRecord(BaseModel):
    internal_task_id: str = Field(..., description="内部唯一任务ID")
    provider_task_id: Optional[str] = Field(default="", description="视频模型服务商返回的任务ID")
    product_id: str
    shot_id: str
    template_id: str = "TPL_SCENE_PRODUCT_15S_V1"
    provider: str = "jimeng"
    model: str = "jimeng-video-v2"
    prompt_version: str = "1.0"
    prompt_text: str
    negative_prompt: str = ""
    source_image: Optional[str] = ""
    duration: int = 5
    aspect_ratio: str = "9:16"
    status: TaskStatus = TaskStatus.CREATED
    video_url: Optional[str] = None
    local_video_path: Optional[str] = None
    generation_time_seconds: Optional[float] = None
    estimated_cost: Optional[float] = None
    qa_score: Optional[int] = None
    qa_status: Optional[QAStatus] = None
    failure_codes: List[str] = Field(default_factory=list)
    failure_notes: List[str] = Field(default_factory=list)
    repair_actions: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ------------------------------------------------------------------------------
# 8. QA 打分与回写请求 (Section 14 & 25)
# ------------------------------------------------------------------------------
class QARecordInput(BaseModel):
    internal_task_id: str
    shot_id: str
    # 10 项细项分数 (满分100)
    score_product_consistency: int = Field(20, ge=0, le=20, description="商品一致性 (满分20)")
    score_person_realism: int = Field(15, ge=0, le=15, description="人物真实性 (满分15)")
    score_action_naturalness: int = Field(15, ge=0, le=15, description="动作自然度 (满分15)")
    score_hand_limb: int = Field(10, ge=0, le=10, description="手部与肢体 (满分10)")
    score_prompt_following: int = Field(10, ge=0, le=10, description="Prompt遵循度 (满分10)")
    score_scene_realism: int = Field(10, ge=0, le=10, description="场景真实性 (满分10)")
    score_camera_rationality: int = Field(5, ge=0, le=5, description="镜头合理性 (满分5)")
    score_frame_stability: int = Field(5, ge=0, le=5, description="画面稳定性 (满分5)")
    score_info_accuracy: int = Field(5, ge=0, le=5, description="商品信息准确性 (满分5)")
    score_compliance: int = Field(5, ge=0, le=5, description="合规性 (满分5)")

    hard_fail_code: Optional[str] = Field(default=None, description="命中硬失败规则代码 HARD_FAIL_01~07")
    failure_codes: List[str] = Field(default_factory=list, description="具体 Failure Code 列表")
    failure_notes: List[str] = Field(default_factory=list, description="问题表现说明")


# ------------------------------------------------------------------------------
# 9. 视频缝合交付物
# ------------------------------------------------------------------------------
class StitchResult(BaseModel):
    product_id: str
    task_ids: List[str]
    shots: List[str]
    total_duration: int = 15
    final_video_url: str
    local_path: str
    qa_pass_summary: Dict[str, Any]
    stitched_at: str = Field(default_factory=lambda: datetime.now().isoformat())
