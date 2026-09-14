"""Minimal Volcano Ark client for multimodal, Seedream and Seedance APIs."""

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

from core.config import settings
from core.schemas import AssetRecord


class ArkAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        request_id: str = "",
        error_code: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.error_code = error_code


class ArkClient:
    @staticmethod
    def configured() -> bool:
        return bool(settings.ARK_API_KEY or settings.SEEDANCE_ARK_API_KEY)

    @staticmethod
    def _headers() -> Dict[str, str]:
        key = settings.ARK_API_KEY or settings.SEEDANCE_ARK_API_KEY
        if not key:
            raise ArkAPIError("未配置 ARK_API_KEY")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @classmethod
    def _request(cls, method: str, path: str, *, payload: Dict[str, Any] | None = None, timeout: int = 90):
        url = f"{settings.ARK_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = requests.request(
                method,
                url,
                headers=cls._headers(),
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise ArkAPIError(f"方舟网络请求失败: {exc}") from exc
        if not response.ok:
            request_id = response.headers.get("x-request-id", "")
            detail = response.text[:1200]
            key = settings.ARK_API_KEY or settings.SEEDANCE_ARK_API_KEY
            if key:
                detail = detail.replace(key, "***")
            raise ArkAPIError(
                f"方舟 API 返回 HTTP {response.status_code}: {detail}",
                response.status_code,
                request_id,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ArkAPIError("方舟 API 返回了非 JSON 响应", response.status_code) from exc

    @staticmethod
    def _path_to_data_url(path: Path, max_edge: int = 1800) -> str:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=88, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    @classmethod
    def asset_data_url(cls, asset: AssetRecord) -> str:
        path = Path(asset.local_path).resolve()
        asset_root = settings.ASSET_DIR.resolve()
        if not path.is_relative_to(asset_root) or not path.is_file():
            raise ArkAPIError(f"商品素材不存在或越界: {asset.asset_id}")
        return cls._path_to_data_url(path)

    @classmethod
    def resolve_image_reference(cls, reference: str) -> str:
        """Convert local app URLs to data URLs; keep public HTTPS references intact."""
        if not reference:
            return ""
        if reference.startswith("/assets/"):
            path = (settings.ASSET_DIR / Path(reference).name).resolve()
            if not path.is_relative_to(settings.ASSET_DIR.resolve()):
                raise ArkAPIError("非法商品素材路径")
            return cls._path_to_data_url(path)
        if reference.startswith("/outputs/"):
            relative = reference.removeprefix("/outputs/")
            path = (settings.OUTPUT_DIR / relative).resolve()
            if not path.is_relative_to(settings.OUTPUT_DIR.resolve()):
                raise ArkAPIError("非法生成素材路径")
            return cls._path_to_data_url(path)
        parsed = urlparse(reference)
        if parsed.scheme in {"http", "https", "data"}:
            return reference
        path = Path(reference).resolve()
        if path.is_file() and (
            path.is_relative_to(settings.ASSET_DIR.resolve())
            or path.is_relative_to(settings.OUTPUT_DIR.resolve())
        ):
            return cls._path_to_data_url(path)
        raise ArkAPIError("图片引用必须是已上传素材、本地生成结果或 HTTP(S) URL")

    @staticmethod
    def validate_virtual_actor_reference(reference: str) -> str:
        """Validate a provider Asset URI without enabling it for Seedream inputs."""
        if not re.fullmatch(r"asset://asset-\d{14}-[a-z0-9]+", reference or ""):
            raise ArkAPIError("公共虚拟人人物引用必须是项目白名单解析出的标准 asset:// Asset URI")
        from core.public_virtual_actors import (
            PublicVirtualActorCatalog,
            PublicVirtualActorCatalogError,
        )

        try:
            PublicVirtualActorCatalog.require_asset_uri(reference)
        except PublicVirtualActorCatalogError as exc:
            raise ArkAPIError(str(exc)) from exc
        return reference

    @classmethod
    def list_models(cls) -> list[str]:
        response = cls._request("GET", "/models", timeout=30)
        return sorted(str(item.get("id", "")) for item in response.get("data", []) if item.get("id"))

    @classmethod
    def vision_json(cls, assets: Iterable[AssetRecord], instruction: str, model: str) -> Dict[str, Any]:
        content: list[Dict[str, Any]] = [{"type": "text", "text": instruction}]
        for asset in assets:
            content.append({"type": "image_url", "image_url": {"url": cls.asset_data_url(asset)}})
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 1800,
            "response_format": {"type": "json_object"},
        }
        response = cls._request("POST", "/chat/completions", payload=payload, timeout=120)
        message = response.get("choices", [{}])[0].get("message", {})
        text = message.get("content", "")
        if isinstance(text, list):
            text = "".join(str(item.get("text", "")) for item in text if isinstance(item, dict))
        cleaned = str(text).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ArkAPIError(f"视觉模型未返回合法 JSON: {cleaned[:500]}") from exc

    @classmethod
    def generate_image(
        cls, *, model: str, prompt: str, image_references: list[str], size: str
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "stream": False,
            "watermark": True,
        }
        if "pro" not in model.lower():
            payload["sequential_image_generation"] = "disabled"
        references = [cls.resolve_image_reference(item) for item in image_references if item]
        if references:
            payload["image"] = references if len(references) > 1 else references[0]
        return cls._request("POST", "/images/generations", payload=payload, timeout=180)

    @classmethod
    def create_video_task(
        cls,
        *,
        model: str,
        prompt: str,
        image_reference: str,
        duration: int,
        aspect_ratio: str,
        virtual_actor_reference: str = "",
    ) -> Dict[str, Any]:
        if "seedance-2-0" in model.lower() and not 4 <= duration <= 15:
            raise ArkAPIError("Seedance 2.0 duration 必须为 4 到 15 秒")
        if virtual_actor_reference and "seedance-2-0" not in model.lower():
            raise ArkAPIError("公共虚拟人 reference_image 当前仅允许用于 Seedance 2.0")
        content: list[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        # Seedream produces a product/scene composition plate, not a contractual
        # first video frame. Submit it as reference media in every mode: this
        # avoids first-frame/reference-media conflicts and generated-face privacy
        # rejections while still preserving the product and scene anchors.
        image_role = "reference_image"
        if image_reference:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": cls.resolve_image_reference(image_reference)},
                    "role": image_role,
                }
            )
        if virtual_actor_reference:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": cls.validate_virtual_actor_reference(virtual_actor_reference)
                    },
                    "role": "reference_image",
                }
            )
        ratio = "adaptive" if "seedance-2-5" in model.lower() and image_reference else aspect_ratio
        payload = {
            "model": model,
            "content": content,
            "duration": duration,
            "ratio": ratio,
            "resolution": "720p",
            "generate_audio": settings.VIDEO_GENERATE_AUDIO,
        }
        return cls._request(
            "POST",
            "/contents/generations/tasks",
            payload=payload,
            timeout=60,
        )

    @classmethod
    def get_video_task(cls, provider_task_id: str) -> Dict[str, Any]:
        return cls._request("GET", f"/contents/generations/tasks/{provider_task_id}", timeout=30)
