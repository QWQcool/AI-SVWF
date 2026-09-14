"""AI-SVWF public API and persistence schemas.

Field names follow sections 8, 12, 13, 23, 24 and 25 of the handoff
document. Request models live here so provider-specific arguments cannot leak
into the workflow layer.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from uuid import uuid4
import hashlib
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


EvidenceSourceType = Literal[
    "user_declaration",
    "image_visible",
    "packaging_text",
    "model_inference",
    "human_confirmation",
]


class EvidenceReference(BaseModel):
    source_type: EvidenceSourceType
    source_asset_ids: List[str] = Field(default_factory=list)
    detail: str = ""
    model: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class ProductClaim(BaseModel):
    claim_id: str = Field(default_factory=lambda: f"CLM_{uuid4().hex[:8].upper()}")
    text: str
    classification: Literal["confirmed", "possible", "rejected"] = "possible"
    provenance: List[EvidenceReference] = Field(default_factory=list)
    compliance_failure_codes: List[str] = Field(default_factory=list)
    human_confirmed: bool = False
    human_confirmed_at: Optional[str] = None
    human_confirmed_by: Optional[str] = None
    human_note: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class EvidenceBreakdown(BaseModel):
    raw_model_score: Optional[float] = None
    source_coverage: float = 0.0
    conflict_penalty: float = 0.0
    occlusion_penalty: float = 0.0
    compliance_penalty: float = 0.0
    human_bonus: float = 0.0
    final_score: Optional[float] = None


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


class PublicVirtualActor(BaseModel):
    """Immutable snapshot of one provider-public actor in the repo allowlist."""

    group_id: str = Field(..., min_length=1, max_length=80)
    asset_uri: str = Field(..., min_length=1, max_length=160)
    country: str = Field(..., min_length=1, max_length=40)
    gender: str = Field(..., min_length=1, max_length=20)
    age: int = Field(..., ge=18, le=100)
    role: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=1000)
    identity_prompt: str = Field(..., min_length=1, max_length=500)
    provider_public: bool
    project_allowlisted: bool


class VirtualActorSelectionRequest(BaseModel):
    group_id: Optional[str] = Field(default=None, max_length=80)


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
    evidence_sufficiency: Optional[float] = None
    evidence_status: Literal["pending", "analyzed", "needs_review"] = "pending"
    evidence_breakdown: Dict[str, Any] = Field(default_factory=dict)
    claims: List[ProductClaim] = Field(default_factory=list)
    information_confidence: Optional[float] = Field(default=None, description="deprecated: use evidence_sufficiency")
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
    virtual_actor_group_id: Optional[str] = Field(default=None, max_length=80)
    virtual_actor: Optional[PublicVirtualActor] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="before")
    @classmethod
    def sync_confidence_and_claims(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        actor = data.get("virtual_actor")
        if actor and not data.get("virtual_actor_group_id"):
            data["virtual_actor_group_id"] = (
                actor.get("group_id") if isinstance(actor, dict) else getattr(actor, "group_id", None)
            )

        # 1. Backwards compatibility: information_confidence <-> evidence_sufficiency
        suff = data.get("evidence_sufficiency")
        conf = data.get("information_confidence")
        if suff is None and conf is not None:
            data["evidence_sufficiency"] = conf
        elif conf is None and suff is not None:
            data["information_confidence"] = suff

        # 2. Reconcile claims with confirmed_information and possible_information
        raw_claims = data.get("claims")
        now = data.get("created_at") or utc_now_iso()
        if raw_claims:
            confirmed = []
            possible = []
            for item in raw_claims:
                text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
                classification = item.get("classification") if isinstance(item, dict) else getattr(item, "classification", "")
                if classification == "confirmed" and text:
                    confirmed.append(text)
                elif classification == "possible" and text:
                    possible.append(text)
            if not data.get("confirmed_information"):
                data["confirmed_information"] = confirmed
            if not data.get("possible_information"):
                data["possible_information"] = possible
        elif data.get("confirmed_information") or data.get("possible_information"):
            # Synthesize claims from legacy data
            synthesized: List[Dict[str, Any]] = []
            asset_ids = data.get("source_asset_ids", [])
            product_id = str(data.get("product_id") or "LEGACY_PRODUCT")
            legacy_created_at = data.get("created_at") or now
            for index, c_text in enumerate(data.get("confirmed_information", [])):
                digest = hashlib.sha256(
                    f"{product_id}|confirmed|{index}|{c_text}".encode("utf-8")
                ).hexdigest()[:12].upper()
                synthesized.append({
                    "claim_id": f"CLM_{digest}",
                    "text": c_text,
                    "classification": "confirmed",
                    "provenance": [{
                        "source_type": "user_declaration",
                        "source_asset_ids": asset_ids,
                        "detail": "从旧版档案迁移的已确认信息",
                        "created_at": legacy_created_at,
                    }],
                    "created_at": legacy_created_at,
                    "updated_at": legacy_created_at,
                })
            for index, p_text in enumerate(data.get("possible_information", [])):
                digest = hashlib.sha256(
                    f"{product_id}|possible|{index}|{p_text}".encode("utf-8")
                ).hexdigest()[:12].upper()
                synthesized.append({
                    "claim_id": f"CLM_{digest}",
                    "text": p_text,
                    "classification": "possible",
                    "provenance": [{
                        "source_type": "model_inference",
                        "source_asset_ids": asset_ids,
                        "detail": "从旧版档案迁移的合理推测",
                        "created_at": legacy_created_at,
                    }],
                    "created_at": legacy_created_at,
                    "updated_at": legacy_created_at,
                })
            data["claims"] = synthesized

        return data


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
    virtual_actor_group_id: Optional[str] = Field(default=None, max_length=80)


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
    virtual_actor_group_id: Optional[str] = Field(default=None, max_length=80)


class FailureOccurrence(BaseModel):
    code: str
    note: str = Field(default="", max_length=500)
    time_point_seconds: Optional[float] = None
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None

    @field_validator("time_point_seconds")
    @classmethod
    def validate_time(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("time_point_seconds cannot be negative")
        return v

    @field_validator("frame_start", "frame_end")
    @classmethod
    def validate_frame(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("frame number cannot be negative")
        return v

    @model_validator(mode="after")
    def validate_frame_range(self) -> "FailureOccurrence":
        if self.frame_start is not None and self.frame_end is not None:
            if self.frame_start > self.frame_end:
                raise ValueError("frame_start must be <= frame_end")
        return self


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
    virtual_actor: Optional[PublicVirtualActor] = None
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
    failure_codes: List[str] = Field(default_factory=list, max_length=33)
    failure_notes: List[str] = Field(default_factory=list, max_length=33)
    failure_occurrences: List[FailureOccurrence] = Field(default_factory=list, max_length=33)
    repair_actions: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    revision_id: Optional[str] = None
    attempt_no: int = 1
    generation_kind: Literal["initial", "reroll", "manual_revision", "failure_repair", "repair"] = "initial"
    root_task_id: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def sync_failure_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        occurrences = data.get("failure_occurrences")
        codes = data.get("failure_codes") or []
        notes = data.get("failure_notes") or []
        if occurrences:
            if not codes:
                extracted_codes = []
                for occ in occurrences:
                    c = occ.get("code") if isinstance(occ, dict) else getattr(occ, "code", "")
                    if c and c not in extracted_codes:
                        extracted_codes.append(c)
                data["failure_codes"] = extracted_codes
            if not notes:
                extracted_notes = []
                for occ in occurrences:
                    n = occ.get("note") if isinstance(occ, dict) else getattr(occ, "note", "")
                    if n:
                        extracted_notes.append(n)
                data["failure_notes"] = extracted_notes
        elif codes:
            data["failure_occurrences"] = [
                {"code": c, "note": notes[i] if i < len(notes) else ""}
                for i, c in enumerate(codes)
            ]
        return data


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
    failure_codes: List[str] = Field(default_factory=list, max_length=33)
    failure_notes: List[str] = Field(default_factory=list, max_length=33)
    failure_occurrences: List[FailureOccurrence] = Field(default_factory=list, max_length=33)

    @model_validator(mode="before")
    @classmethod
    def reconcile_occurrences(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        occurrences = data.get("failure_occurrences")
        codes = data.get("failure_codes") or []
        notes = data.get("failure_notes") or []
        if occurrences:
            if not codes:
                extracted_codes = []
                for occ in occurrences:
                    c = occ.get("code") if isinstance(occ, dict) else getattr(occ, "code", "")
                    if c and c not in extracted_codes:
                        extracted_codes.append(c)
                data["failure_codes"] = extracted_codes
            if not notes:
                extracted_notes = []
                for occ in occurrences:
                    n = occ.get("note") if isinstance(occ, dict) else getattr(occ, "note", "")
                    if n:
                        extracted_notes.append(n)
                data["failure_notes"] = extracted_notes
        elif codes:
            data["failure_occurrences"] = [
                {"code": c, "note": notes[i] if i < len(notes) else ""}
                for i, c in enumerate(codes)
            ]
        return data


class ShotPromptRevision(BaseModel):
    revision_id: str
    product_id: str
    shot_id: str
    revision_sequence: int
    display_version: str
    version: Optional[str] = None
    parent_revision_id: Optional[str] = None
    prompt_text: str
    negative_prompt: str = ""
    prompt_fingerprint: str
    change_type: Literal["initial", "manual_edit", "failure_repair", "repair"] = "initial"
    change_note: str = ""
    source_failure_codes: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="before")
    @classmethod
    def sync_version(cls, data: Any) -> Any:
        if isinstance(data, dict):
            v = data.get("version") or data.get("display_version")
            if v:
                data["version"] = v
                data["display_version"] = v
        return data


class FailureCodeItem(BaseModel):
    code: str
    kind: Literal["repairable", "hard_fail"]
    category: str
    name: str
    symptom: str
    repair_action: str
    repairable: bool


class ClaimConfirmRequest(BaseModel):
    confirmed: bool = True
    confirmed_by: Optional[str] = "human_operator"
    note: str = Field(default="", max_length=1000)


class ShotPromptRevisionCreateRequest(BaseModel):
    parent_revision_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    prompt_text: str = Field(..., min_length=1, max_length=30000)
    negative_prompt: str = Field(default="", max_length=15000)
    change_type: str = Field(default="manual_edit")
    change_note: str = Field(default="", max_length=1000)
    generate_immediately: bool = False
    auto_generate: bool = False
    image_url: Optional[str] = None
    provider: str = "mock"
    model: str = "mock-video-v1"

    @model_validator(mode="before")
    @classmethod
    def sync_auto_generate(cls, data: Any) -> Any:
        if isinstance(data, dict):
            auto = bool(data.get("auto_generate") or data.get("generate_immediately"))
            data["auto_generate"] = auto
            data["generate_immediately"] = auto
        return data


class ShotSelectionRequest(BaseModel):
    selected_task_id: str = Field(..., min_length=1)
    selection_note: str = Field(default="", max_length=1000)


class ShotSelectionRecord(BaseModel):
    product_id: str
    shot_id: str
    selected_task_id: str
    selection_note: str = ""
    selected_at: str = Field(default_factory=utc_now_iso)


class ShotHistoryRevisionNode(BaseModel):
    revision: ShotPromptRevision
    attempts: List[VideoTaskRecord] = Field(default_factory=list)


class ShotHistoryResponse(BaseModel):
    product_id: str
    shot_id: str
    revisions: List[ShotHistoryRevisionNode] = Field(default_factory=list)
    selected_task_id: Optional[str] = None
    selection: Optional[ShotSelectionRecord] = None
    qa_records: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)


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
    virtual_actor_group_id: Optional[str] = Field(default=None, max_length=80)


class ImageGenerationRecord(BaseModel):
    image_task_id: str
    product_id: str
    shot_id: Literal["S01", "S02", "S03"]
    model: str
    prompt_version: str
    prompt_text: str
    source_asset_ids: List[str] = Field(default_factory=list)
    virtual_actor_group_id: Optional[str] = None
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
