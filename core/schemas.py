"""AI-SVWF public API and persistence schemas.

Field names follow sections 8, 12, 13, 23, 24 and 25 of the handoff
document. Request models live here so provider-specific arguments cannot leak
into the workflow layer.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class ProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_name: str = Field(..., min_length=1, max_length=200)
    product_images: List[str] = Field(default_factory=list)
    short_description: str = Field(default="", max_length=4000)
    reference_video: str = Field(default="", max_length=2000)
    target_audience: str = Field(default="", max_length=500)
    preferred_scene: str = Field(default="", max_length=500)

    @field_validator("product_images")
    @classmethod
    def clean_images(cls, value: List[str]) -> List[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        return list(dict.fromkeys(cleaned))[:12]


class ProductAnalysis(BaseModel):
    product_id: str
    product_name: str
    brand: str = ""
    category: str = ""
    specification: str = ""
    appearance_description: str = ""
    confirmed_information: List[str] = Field(default_factory=list)
    possible_information: List[str] = Field(default_factory=list)
    usage_scenes: List[str] = Field(default_factory=list)
    risk_information: List[str] = Field(default_factory=list)
    information_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_images: List[str] = Field(default_factory=list)
    reference_video: str = ""
    target_audience: str = ""
    preferred_scene: str = ""
    source_description: str = ""
    analysis_source: Literal["manual", "vision"] = "manual"
    analysis_model: str = ""
    source_asset_ids: List[str] = Field(default_factory=list)
    observed_information: List[str] = Field(default_factory=list)
    packaging_claims: List[str] = Field(default_factory=list)
    model_inferences: List[str] = Field(default_factory=list)
    vision_notes: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AssetRecord(BaseModel):
    asset_id: str
    sha256: str
    original_name: str
    stored_name: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    local_path: str
    url: str
    created_at: str = Field(default_factory=utc_now_iso)


class VisionAnalyzeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    asset_ids: List[str] = Field(..., min_length=1, max_length=6)
    product_name: str = Field(default="", max_length=200)
    short_description: str = Field(default="", max_length=4000)
    target_audience: str = Field(default="", max_length=500)
    preferred_scene: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=128)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)

    @field_validator("asset_ids")
    @classmethod
    def unique_asset_ids(cls, value: List[str]) -> List[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("asset_ids must contain at least one non-empty asset ID")
        return cleaned


class ShotSpec(BaseModel):
    shot_id: Literal["S01", "S02", "S03"]
    duration: int = Field(default=5, ge=4, le=15)
    purpose: str
    difficulty: Literal["low", "medium", "high"] = "low"
    product_interaction: str = "none"
    camera_module: str = ""
    action_modules: List[str] = Field(default_factory=list)
    product_position: str = ""
    start_frame_requirement: str = ""
    end_frame_requirement: str = ""


class VideoTemplate(BaseModel):
    template_id: str = "TPL_SCENE_PRODUCT_15S_V1"
    template_name: str = "真实场景体验型带货视频"
    video_type: str = "scene_experience"
    duration_total: int = 15
    generation_strategy: str = "3x5s"
    aspect_ratio: Literal["9:16"] = "9:16"
    shots: List[ShotSpec] = Field(default_factory=list)


class VideoPlanRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    template_id: str = "TPL_SCENE_PRODUCT_15S_V1"


class PromptCompileRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    version: str = Field(default="1.0", pattern=r"^[0-9]+\.[0-9]+$")
    provider: str = "mock"
    model: str = "mock-video-v1"


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
    text_policy: Dict[str, bool] = Field(default_factory=lambda: {
        "allow_model_generated_text": False,
        "overlay_text_in_post": True,
    })
    audio_policy: Dict[str, bool] = Field(default_factory=lambda: {
        "generate_voice_in_video_model": False,
        "voiceover_post_process": True,
        "background_music": False,
    })
    model: Dict[str, Any] = Field(default_factory=dict)
    prompt_output: Dict[str, str] = Field(default_factory=dict)
    version: Dict[str, str] = Field(default_factory=dict)


class PromptVariantPlanRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    shot_ids: List[Literal["S01", "S02", "S03"]] = Field(
        default_factory=lambda: ["S01", "S02", "S03"]
    )
    variants_per_shot: int = Field(default=3, ge=1, le=6)
    base_version: str = Field(default="1.0", pattern=r"^[0-9]+\.[0-9]+$")
    axes: List[Literal["scene", "action", "camera", "lighting", "product_lock"]] = Field(
        default_factory=lambda: ["scene", "action", "camera", "lighting", "product_lock"]
    )


class PromptVariant(BaseModel):
    variant_id: str
    product_id: str
    shot_id: Literal["S01", "S02", "S03"]
    prompt_version: str
    variant_index: int
    axes: Dict[str, str]
    prompt_text: str
    negative_prompt: str
    fingerprint: str
    created_at: str = Field(default_factory=utc_now_iso)


class VideoGenerateRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    shot_id: Literal["S01", "S02", "S03"]
    provider: str = Field(default="mock", min_length=1, max_length=64)
    model: str = Field(default="mock-video-v1", min_length=1, max_length=128)
    prompt_version: str = Field(default="1.0", min_length=1, max_length=32)
    prompt: str = Field(..., min_length=1, max_length=30000)
    negative_prompt: str = Field(default="", max_length=15000)
    image_url: str = Field(default="", max_length=4000)
    duration: int = Field(default=5, ge=4, le=15)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    product_name: str = Field(default="测试商品", max_length=200)
    variant_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class VideoTaskRecord(BaseModel):
    internal_task_id: str
    provider_task_id: str = ""
    parent_task_id: Optional[str] = None
    product_id: str
    shot_id: str
    template_id: str = "TPL_SCENE_PRODUCT_15S_V1"
    provider: str = "mock"
    model: str = "mock-video-v1"
    execution_mode: Literal["mock", "real"] = "mock"
    prompt_version: str = "1.0"
    variant_id: Optional[str] = None
    request_fingerprint: str = ""
    prompt_text: str
    negative_prompt: str = ""
    source_image: str = ""
    duration: int = 5
    aspect_ratio: str = "9:16"
    status: TaskStatus = TaskStatus.CREATED
    video_url: Optional[str] = None
    local_video_path: Optional[str] = None
    generation_time_seconds: Optional[float] = None
    output_width: Optional[int] = None
    output_height: Optional[int] = None
    output_fps: Optional[float] = None
    output_duration_seconds: Optional[float] = None
    output_audio_codec: Optional[str] = None
    estimated_cost: Optional[float] = None
    qa_score: Optional[int] = None
    qa_status: Optional[QAStatus] = None
    failure_codes: List[str] = Field(default_factory=list)
    failure_notes: List[str] = Field(default_factory=list)
    repair_actions: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None


class QARecordInput(BaseModel):
    internal_task_id: str
    shot_id: Literal["S01", "S02", "S03"]
    score_product_consistency: int = Field(20, ge=0, le=20)
    score_person_realism: int = Field(15, ge=0, le=15)
    score_action_naturalness: int = Field(15, ge=0, le=15)
    score_hand_limb: int = Field(10, ge=0, le=10)
    score_prompt_following: int = Field(10, ge=0, le=10)
    score_scene_realism: int = Field(10, ge=0, le=10)
    score_camera_rationality: int = Field(5, ge=0, le=5)
    score_frame_stability: int = Field(5, ge=0, le=5)
    score_info_accuracy: int = Field(5, ge=0, le=5)
    score_compliance: int = Field(5, ge=0, le=5)
    hard_fail_code: Optional[str] = Field(default=None, pattern=r"^HARD_FAIL_0[1-7]$")
    failure_codes: List[str] = Field(default_factory=list)
    failure_notes: List[str] = Field(default_factory=list)


class TaskRetryRequest(BaseModel):
    failure_codes: List[str] = Field(default_factory=list)


class FirstFrameRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    shot_id: Literal["S01", "S02", "S03"] = "S01"
    prompt_version: str = Field(default="1.0", pattern=r"^[0-9]+\.[0-9]+$")
    prompt: str = Field(default="", max_length=30000)
    asset_ids: List[str] = Field(default_factory=list, max_length=6)
    model: str = Field(default="", max_length=128)
    size: str = Field(default="1440x2560", max_length=32)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class ImageGenerationRecord(BaseModel):
    image_task_id: str
    product_id: str
    shot_id: Literal["S01", "S02", "S03"]
    model: str
    prompt_version: str
    prompt_text: str
    source_asset_ids: List[str] = Field(default_factory=list)
    request_fingerprint: str
    status: Literal["SUBMITTED", "COMPLETED", "FAILED"]
    remote_url: str = ""
    image_url: str = ""
    local_path: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    error_code: str = ""
    error_message: str = ""
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class StitchRequest(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=128)
    task_ids: List[str] = Field(..., min_length=3, max_length=3)
    product_name: str = Field(default="带货商品", max_length=200)
    product_desc: str = Field(default="", max_length=4000)
    enable_tts: bool = False
    voice: str = Field(default="xiaoxiao", max_length=64)
    require_qa_pass: bool = True


class StitchResult(BaseModel):
    product_id: str
    task_ids: List[str]
    shots: List[str]
    total_duration: int = 15
    final_video_url: str
    local_path: str
    qa_pass_summary: Dict[str, Any]
    stitched_at: str = Field(default_factory=utc_now_iso)


class LLMEnhancementRequest(BaseModel):
    product_id: str
    instruction: str = Field(default="提取可能的场景、受众和模块建议", max_length=1000)
