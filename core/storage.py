"""
AI-SVWF 视频存储与资源管理模块 (StorageManager)
支持本地 outputs/ 目录持久化，生成永久可访问的服务端 URL，
并预留云端对象存储 (Volcano TOS / Aliyun OSS / S3) 插件化升级接口。
"""

import hashlib
import ipaddress
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
import requests
from core.config import settings


class StorageManager:
    @classmethod
    def get_output_path(cls, filename: str) -> Path:
        """获取本地输出文件绝对路径"""
        return settings.OUTPUT_DIR / filename

    @classmethod
    def get_accessible_url(cls, filename: str, base_url: Optional[str] = None) -> str:
        """
        获取文件的服务端可访问 HTTP URL
        在本地开发为: http://localhost:8000/outputs/{filename}
        在云服务器上为: http://<server_ip>:<port>/outputs/{filename}
        """
        if not base_url:
            host = "localhost" if settings.HOST in {str(ipaddress.ip_address(0)), "127.0.0.1"} else settings.HOST
            base_url = f"http://{host}:{settings.PORT}"
        return f"{base_url.rstrip('/')}/outputs/{filename}"

    @classmethod
    def save_local_file(cls, source_path: str, target_filename: str) -> Tuple[str, str]:
        """保存或归档本地文件到 outputs 目录，返回 (绝对路径, 相对访问URL)"""
        target_path = cls.get_output_path(target_filename)
        if str(source_path) != str(target_path):
            shutil.copy2(source_path, target_path)
        return str(target_path), cls.get_accessible_url(target_filename)

    @classmethod
    def download_remote_video(
        cls,
        remote_url: str,
        prefix: str = "provider",
        archive_key: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Archive a provider result locally so downstream stitching is reproducible.

        ``archive_key`` makes the destination stable for a paid provider task.  A
        process restart can therefore validate an already archived result instead
        of downloading the same result into a new random file on every poll.
        """
        parsed = urlparse(remote_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("视频结果 URL 必须使用 http 或 https")
        safe_prefix = "".join(character for character in prefix if character.isalnum() or character in "_-")
        safe_prefix = safe_prefix[:32] or "provider"
        suffix = (
            hashlib.sha256(archive_key.encode("utf-8")).hexdigest()[:16]
            if archive_key
            else uuid.uuid4().hex[:12]
        )
        filename = f"{safe_prefix}_{suffix}.mp4"
        target = cls.get_output_path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size > 0:
            return str(target), cls.get_accessible_url(filename)
        if target.exists():
            if not target.is_file():
                raise ValueError("供应商视频归档目标不是普通文件")
            target.unlink(missing_ok=True)
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
        downloaded = 0
        max_bytes = 500 * 1024 * 1024
        try:
            with requests.get(remote_url, stream=True, timeout=(10, 120)) as response:
                response.raise_for_status()
                with open(temporary, "xb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise ValueError("供应商视频超过 500MB 安全上限")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if downloaded == 0:
                raise ValueError("供应商返回了空视频文件")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target), cls.get_accessible_url(filename)

    @classmethod
    def discard_output_file(cls, path: str) -> bool:
        """Delete a rejected local artifact, but never leave the output tree."""
        candidate = Path(path).resolve()
        output_root = settings.OUTPUT_DIR.resolve()
        if not candidate.is_relative_to(output_root):
            raise ValueError("拒绝清理 outputs 目录之外的文件")
        if not candidate.exists():
            return False
        if not candidate.is_file():
            raise ValueError("拒绝清理非普通输出文件")
        candidate.unlink()
        return True

    @staticmethod
    def inspect_video(path: str) -> Dict[str, Any]:
        """Read provider media metadata without decoding the whole video."""
        import imageio_ffmpeg

        reader = imageio_ffmpeg.read_frames(str(Path(path).resolve()), pix_fmt="rgb24")
        try:
            metadata = next(reader)
        finally:
            reader.close()
        width, height = metadata.get("size") or metadata.get("source_size") or (0, 0)
        return {
            "width": int(width),
            "height": int(height),
            "fps": float(metadata.get("fps") or 0),
            "duration": float(metadata.get("duration") or 0),
            "video_codec": str(metadata.get("codec") or ""),
            "audio_codec": str(metadata.get("audio_codec") or ""),
        }

    @staticmethod
    def remove_audio_track(path: str) -> None:
        """Atomically remove unapproved provider-native audio from an MP4."""
        import imageio_ffmpeg

        source = Path(path).resolve()
        temporary = source.with_name(f"{source.stem}.{uuid.uuid4().hex}.silent.mp4")
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-an", "-movflags", "+faststart",
            str(temporary),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=60)
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("无法移除供应商原生音轨")
            os.replace(temporary, source)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def create_mock_video(
        cls,
        shot_id: str,
        version: str = "1.0",
        product_name: str = "测试商品",
        duration: int = 5,
        fps: int = 24,
    ) -> Tuple[str, str]:
        """
        高保真离线 Mock 视频生成器:
        利用内置 imageio_ffmpeg 渲染带有 9:16 (720x1280) 画幅、分镜水印与时间码的动态视频流，
        确保断网、无 API Key 或排队时，仍能端到端产出真实的 MP4 文件并完成 15 秒缝合！
        """
        filename = f"{shot_id}_v{version}_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = cls.get_output_path(filename)

        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        import imageio

        width, height = 720, 1280
        total_frames = duration * fps

        # 背景主色系 (深色高质感渐变: S01蓝紫, S02青蓝, S03琥珀金)
        colors = {
            "S01": (24, 28, 48),    # 深星空蓝
            "S02": (18, 38, 42),    # 深青墨绿
            "S03": (42, 32, 20),    # 深琥珀金
        }
        bg_base = colors.get(shot_id, (20, 24, 35))

        writer = imageio.get_writer(
            str(output_path),
            fps=fps,
            codec="libx264",
            format="FFMPEG",
            ffmpeg_params=["-pix_fmt", "yuv420p"],
        )

        for f in range(total_frames):
            t = f / fps
            img = Image.new("RGB", (width, height), bg_base)
            draw = ImageDraw.Draw(img)

            # 动态微波纹 (模拟真实视频运动)
            y_offset = int(np.sin(t * 2) * 20)
            circle_radius = int(140 + np.sin(t * 3) * 15)

            # 居中渲染模拟主体物体
            center_x, center_y = width // 2, (height // 2) + y_offset
            draw.ellipse(
                [
                    center_x - circle_radius,
                    center_y - circle_radius,
                    center_x + circle_radius,
                    center_y + circle_radius,
                ],
                fill=(45, 55, 80),
                outline=(80, 160, 255),
                width=4,
            )

            # 绘制文字与水印
            # 顶部标签
            draw.text((40, 80), f"AI-SVWF AIGC WORKFLOW", fill=(140, 160, 200))
            draw.text((40, 120), f"SHOT: {shot_id}  |  PROMPT V{version}", fill=(255, 255, 255))

            # 中间商品标识
            draw.text((center_x - 100, center_y - 20), f"【{product_name}】", fill=(255, 255, 255))
            draw.text((center_x - 80, center_y + 20), "PRODUCT LOCKED", fill=(100, 220, 180))

            # 底部时间码与进度条
            draw.text((40, height - 120), f"TC: 00:0{int(t)}:{f % fps:02d} / 00:0{duration}:00", fill=(200, 200, 200))
            progress_w = int((f / total_frames) * (width - 80))
            draw.rectangle([40, height - 80, 40 + progress_w, height - 70], fill=(80, 180, 255))
            draw.rectangle([40, height - 80, width - 40, height - 70], outline=(60, 70, 90), width=1)

            writer.append_data(np.array(img))

        writer.close()
        return str(output_path), cls.get_accessible_url(filename)
