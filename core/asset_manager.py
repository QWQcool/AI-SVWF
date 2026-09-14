"""Validated local product-image ingestion with content-addressed de-duplication."""

import hashlib
import os
from email.header import decode_header, make_header
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from core.config import settings
from core.database import database
from core.schemas import AssetRecord


class AssetValidationError(ValueError):
    pass


class AssetManager:
    FORMAT_MAP = {
        "JPEG": (".jpg", "image/jpeg"),
        "PNG": (".png", "image/png"),
        "WEBP": (".webp", "image/webp"),
    }

    @staticmethod
    def _safe_original_name(original_name: str) -> str:
        raw_name = original_name or "upload"
        try:
            raw_name = str(make_header(decode_header(raw_name)))
        except (LookupError, UnicodeError, ValueError):
            pass
        return Path(raw_name).name[:255] or "upload"

    @classmethod
    def _normalize_image(cls, content: bytes) -> tuple[bytes, str, str, int, int]:
        """Fully decode and re-encode an image, dropping EXIF/GPS and trailing bytes."""
        try:
            with Image.open(BytesIO(content)) as source:
                if getattr(source, "n_frames", 1) != 1:
                    raise AssetValidationError("不支持动态图片")
                image_format = (source.format or "").upper()
                if image_format not in cls.FORMAT_MAP:
                    raise AssetValidationError("仅支持 JPEG、PNG 和 WebP 图片")
                normalized = ImageOps.exif_transpose(source)
                normalized.load()
                width, height = normalized.size
                if width < 128 or height < 128:
                    raise AssetValidationError("图片宽高至少为 128 像素")
                if width > 8192 or height > 8192:
                    raise AssetValidationError("图片宽高不能超过 8192 像素")

                output = BytesIO()
                if image_format == "JPEG":
                    normalized.convert("RGB").save(output, "JPEG", quality=95, optimize=True)
                elif image_format == "PNG":
                    mode = "RGBA" if "A" in normalized.getbands() else "RGB"
                    normalized.convert(mode).save(output, "PNG", optimize=True)
                else:
                    mode = "RGBA" if "A" in normalized.getbands() else "RGB"
                    normalized.convert(mode).save(output, "WEBP", quality=95, method=6)
        except AssetValidationError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            raise AssetValidationError("文件不是可安全解析的图片") from exc

        normalized_bytes = output.getvalue()
        if len(normalized_bytes) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise AssetValidationError(f"规范化后的图片不能超过 {settings.MAX_UPLOAD_MB}MB")
        extension, mime_type = cls.FORMAT_MAP[image_format]
        return normalized_bytes, extension, mime_type, width, height

    @classmethod
    def validate_image(cls, content: bytes) -> None:
        if not content:
            raise AssetValidationError("上传文件为空")
        if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise AssetValidationError(f"单张图片不能超过 {settings.MAX_UPLOAD_MB}MB")
        cls._normalize_image(content)

    @classmethod
    def save_image(cls, content: bytes, original_name: str) -> AssetRecord:
        if not content:
            raise AssetValidationError("上传文件为空")
        if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise AssetValidationError(f"单张图片不能超过 {settings.MAX_UPLOAD_MB}MB")

        normalized_content, extension, mime_type, width, height = cls._normalize_image(content)
        digest = hashlib.sha256(normalized_content).hexdigest()
        safe_original_name = cls._safe_original_name(original_name)
        existing = database.get_asset_by_sha256(digest)
        if existing:
            if existing.original_name != safe_original_name:
                existing.original_name = safe_original_name
                database.update_asset(existing)
            return existing

        asset_id = f"ASSET_{uuid4().hex[:12].upper()}"
        stored_name = f"{asset_id.lower()}_{digest[:12]}{extension}"
        target = settings.ASSET_DIR / stored_name
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        settings.ASSET_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(temporary, "xb") as handle:
                handle.write(normalized_content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

        record = AssetRecord(
            asset_id=asset_id,
            sha256=digest,
            original_name=safe_original_name,
            stored_name=stored_name,
            mime_type=mime_type,
            width=width,
            height=height,
            size_bytes=len(normalized_content),
            local_path=str(target.resolve()),
            url=f"/assets/{stored_name}",
        )
        return database.save_asset(record)
