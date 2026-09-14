"""Persisted Seedream first-frame generation for the three-shot workflow."""

import base64
import hashlib
import json
import os
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

from core.ark_client import ArkAPIError, ArkClient
from core.config import settings
from core.database import database
from core.errors import QuotaExceededError
from core.schemas import (
    FirstFrameRequest,
    ImageGenerationRecord,
    ProductAnalysis,
    PublicVirtualActor,
    utc_now_iso,
)

class FirstFrameService:
    @staticmethod
    def _archive_error_code(exc: Exception, remote_url: str) -> str:
        """Distinguish retryable local/network failures from an expired source URL."""
        status_code = 0
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            status_code = exc.response.status_code
        elif isinstance(exc, ArkAPIError):
            status_code = exc.status_code
        if remote_url and 400 <= status_code < 500 and status_code not in {408, 409, 425, 429}:
            return "ARK_IMAGE_ARCHIVE_SOURCE_UNAVAILABLE"
        return "ARK_IMAGE_ARCHIVE_FAILED"

    @staticmethod
    def _static_prompt_context(compiled_prompt: str) -> str:
        """Use only static layers from the 11-layer video prompt for the first frame."""
        if not compiled_prompt.strip():
            return ""
        sections = re.split(r"\n\s*\n", compiled_prompt)
        selected = [
            section.strip()
            for section in sections
            if any(f"第 {index} 层" in section for index in (1, 3, 4, 8, 10))
        ]
        if not selected:
            selected = [compiled_prompt.strip()[:3000]]
        return "；".join(selected)[:6000]

    @staticmethod
    def _fingerprint(
        request: FirstFrameRequest,
        product: ProductAnalysis,
        prompt: str,
        model: str,
        virtual_actor: PublicVirtualActor | None = None,
    ) -> str:
        payload = {
            "idempotency_key": request.idempotency_key,
            "product_id": product.product_id,
            "shot_id": request.shot_id,
            "prompt_version": request.prompt_version,
            "prompt": prompt,
            "asset_ids": request.asset_ids or product.source_asset_ids,
            "model": model,
            "size": request.size,
            "virtual_actor_group_id": virtual_actor.group_id if virtual_actor else None,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _prompt(
        product: ProductAnalysis,
        request: FirstFrameRequest,
        virtual_actor: PublicVirtualActor | None = None,
    ) -> str:
        appearance = product.appearance_description or "严格遵循输入商品参考图"
        scene = product.preferred_scene or (
            product.usage_scenes[0] if product.usage_scenes else "真实日常桌面"
        )
        if virtual_actor:
            safe_compositions = {
                "S01": (
                    "人物完整自然地坐在桌前正常工作，头部和面部可自然入镜，双手在键盘鼠标附近；"
                    "商品完整放在桌面前景，尚未被触碰。"
                ),
                "S02": (
                    "保持同一工位和同一人物，头部和面部可自然入镜，右手停在商品旁边、尚未抓握；"
                    "商品完整清晰，给后续拿起动作留出空间。"
                ),
                "S03": (
                    "保持同一工位和同一人物，头部和面部可自然入镜，商品处于桌面靠前稳定位置；"
                    "构图给后续手离开商品与镜头微推留出空间。"
                ),
            }
            person_policy = (
                f"人物设定：{virtual_actor.identity_prompt}"
                "该人物来自已选火山方舟公共虚拟人目录，视频阶段将使用独立 reference_image 锁定身份；"
                "首帧不得另行指定明星、现实公众人物或第二位主要人物。"
            )
        else:
            safe_compositions = {
                "S01": (
                    "办公者坐在桌前正常工作，只出现肩部以下或自然背影，双手在键盘鼠标附近；"
                    "商品完整放在桌面前景，尚未被触碰。"
                ),
                "S02": (
                    "保持同一工位，只出现人物肩部以下，右手自然停在商品旁边、尚未抓握；"
                    "商品完整清晰，给后续拿起动作留出空间。"
                ),
                "S03": (
                    "保持同一工位，只出现人物肩部以下和自然手部，商品处于桌面靠前稳定位置；"
                    "构图给后续手离开商品与镜头微推留出空间。"
                ),
            }
            person_policy = (
                "人物设定为30到40岁普通东亚成年女性，深色及肩长发，穿简洁日常服装。"
                "为遵守视频模型的肖像隐私要求，画面不得出现任何可识别人脸：不出现正脸、侧脸、"
                "眼睛、鼻子或嘴部，人物头部完全在画外或仅为无法识别身份的自然背影。"
            )
        static_context = FirstFrameService._static_prompt_context(request.prompt)
        confidence = (
            product.evidence_sufficiency
            if product.evidence_sufficiency is not None
            else (product.information_confidence if product.information_confidence is not None else 0.0)
        )
        policy = (
            "仅做商品外观与摆放展示，不表达功效、参数或使用效果。"
            if confidence < 0.50
            else "不得把推测信息或包装宣称表现为已证实事实。"
        )
        return (
            f"为竖屏 9:16 写实带货短视频生成 {request.shot_id} 的第一帧。"
            f"场景为{scene}，画面像手机自然拍摄，不是商业棚拍。"
            f"{person_policy}"
            f"构图要求：{safe_compositions[request.shot_id]}"
            f"商品身份要求：{appearance}。严格使用输入参考图中的同一商品，不得重新设计包装、Logo、"
            "文字位置、瓶盖或接口结构，不添加不存在的部件。商品完整清晰且不被手遮挡。"
            "只生成一个画面，不主动绘制字幕、促销字或边框；平台合规水印按接口策略保留。"
            f"商品证据充分度策略：{policy}"
            + (f"11层工业提示词的静态首帧依据：{static_context}" if static_context else "")
        )

    @staticmethod
    def _download_image(result: dict, target: Path) -> tuple[str, int, int]:
        data = (result.get("data") or [{}])[0]
        raw: bytes
        remote_url = str(data.get("url") or "")
        if remote_url:
            parsed = urlparse(remote_url)
            if parsed.scheme not in {"http", "https"}:
                raise ArkAPIError("Seedream 返回了不安全的图片 URL")
            response = requests.get(remote_url, timeout=120, stream=True)
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(1024 * 1024):
                total += len(chunk)
                if total > 30 * 1024 * 1024:
                    raise ArkAPIError("Seedream 图片超过 30MB 安全上限")
                chunks.append(chunk)
            raw = b"".join(chunks)
        elif data.get("b64_json"):
            raw = base64.b64decode(data["b64_json"], validate=True)
        else:
            raise ArkAPIError("Seedream 响应缺少 url/b64_json")

        with Image.open(BytesIO(raw)) as source:
            source.verify()
        with Image.open(BytesIO(raw)) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            width, height = normalized.size
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        try:
            normalized.save(temporary, format="PNG", optimize=True)
            with open(temporary, "rb+") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return remote_url, width, height

    @classmethod
    def _retry_cached_archive(cls, record: ImageGenerationRecord) -> ImageGenerationRecord:
        """Retry only the local archive step for an already billed Seedream result.

        The provider URL is part of the persisted idempotency record.  Reusing it
        here is deliberately separate from ``ArkClient.generate_image`` so a
        transient download/disk failure can never create another paid generation.
        """
        target = settings.OUTPUT_DIR / "first_frames" / f"{record.image_task_id.lower()}.png"
        try:
            remote_url, width, height = cls._download_image(
                {"data": [{"url": record.remote_url}]}, target
            )
            record.status = "COMPLETED"
            record.remote_url = remote_url or record.remote_url
            record.local_path = str(target.resolve())
            record.image_url = f"/outputs/first_frames/{target.name}"
            record.width = width
            record.height = height
            record.error_code = ""
            record.error_message = ""
        except Exception as exc:
            record.status = "FAILED"
            record.error_code = cls._archive_error_code(exc, record.remote_url)
            record.error_message = str(exc)[:1500]
        record.updated_at = utc_now_iso()
        database.upsert_image_generation(record)
        return record

    @classmethod
    def generate(
        cls,
        request: FirstFrameRequest,
        product: ProductAnalysis,
        virtual_actor: PublicVirtualActor | None = None,
    ) -> ImageGenerationRecord:
        model = request.model or settings.IMAGE_MODEL_PRIMARY
        prompt = cls._prompt(product, request, virtual_actor)
        fingerprint = cls._fingerprint(request, product, prompt, model, virtual_actor)
        cached = database.find_image_generation(fingerprint)
        if cached:
            if (
                cached.status == "FAILED"
                and cached.error_code == "ARK_IMAGE_ARCHIVE_FAILED"
                and cached.remote_url
            ):
                return cls._retry_cached_archive(cached)
            return cached

        record = ImageGenerationRecord(
            image_task_id=f"IMG_{uuid4().hex[:12].upper()}",
            product_id=product.product_id,
            shot_id=request.shot_id,
            model=model,
            prompt_version=request.prompt_version,
            prompt_text=prompt,
            source_asset_ids=request.asset_ids or product.source_asset_ids,
            virtual_actor_group_id=virtual_actor.group_id if virtual_actor else None,
            request_fingerprint=fingerprint,
            status="SUBMITTED",
        )
        reservation, reserved_record = database.reserve_image_generation(
            record, settings.MAX_REAL_IMAGE_TASKS_PER_DAY
        )
        if reservation == "existing" and reserved_record:
            return reserved_record
        if reservation == "quota":
            raise QuotaExceededError(
                f"已达到每日首帧上限 {settings.MAX_REAL_IMAGE_TASKS_PER_DAY}，停止新提交"
            )
        assets = database.get_assets(record.source_asset_ids)
        references = [asset.url for asset in assets] or product.source_images
        provider_response_received = False
        try:
            try:
                result = ArkClient.generate_image(
                    model=model,
                    prompt=prompt,
                    image_references=references,
                    size=request.size,
                )
                provider_response_received = True
            except ArkAPIError as primary_error:
                fallback = settings.IMAGE_MODEL_FALLBACK
                if not fallback or fallback == model or primary_error.status_code not in {400, 404}:
                    raise
                model = fallback
                record.model = fallback
                result = ArkClient.generate_image(
                    model=fallback,
                    prompt=prompt,
                    image_references=references,
                    size=request.size,
                )
                provider_response_received = True
            result_data = (result.get("data") or [{}])[0]
            record.remote_url = str(result_data.get("url") or "")
            target = settings.OUTPUT_DIR / "first_frames" / f"{record.image_task_id.lower()}.png"
            remote_url, width, height = cls._download_image(result, target)
            record.status = "COMPLETED"
            record.remote_url = remote_url or record.remote_url
            record.local_path = str(target.resolve())
            record.image_url = f"/outputs/first_frames/{target.name}"
            record.width = width
            record.height = height
        except Exception as exc:
            record.status = "FAILED"
            if provider_response_received:
                record.error_code = cls._archive_error_code(exc, record.remote_url)
            elif isinstance(exc, ArkAPIError) and exc.status_code == 0:
                record.error_code = "ARK_IMAGE_SUBMISSION_UNCERTAIN"
            else:
                record.error_code = "ARK_IMAGE_GENERATION_FAILED"
            record.error_message = str(exc)[:1500]
        record.updated_at = utc_now_iso()
        database.upsert_image_generation(record)
        return record
