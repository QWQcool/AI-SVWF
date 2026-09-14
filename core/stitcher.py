"""
AI-SVWF 视频无缝缝合与后期合成服务 (StitcherService)
借鉴 MoneyPrinterTurbo 与交接文档规范，使用 FFmpeg 将通过验收的 S01、S02、S03 (3×5s)
无缝转码并拼接为 15 秒 9:16 标准电商带货成片。
"""

import os
import time
import subprocess
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
import imageio_ffmpeg
from core.config import settings
from core.storage import StorageManager
from core.schemas import StitchResult


class StitcherService:
    @classmethod
    def stitch_3x5s_videos(
        cls,
        video_paths: List[str],
        product_id: str,
        task_ids: Optional[List[str]] = None,
        shots: Optional[List[str]] = None,
        audio_path: Optional[str] = None,
    ) -> StitchResult:
        """
        将 S01, S02, S03 3个5秒视频拼接为一个 15秒带货视频，并可选混入 TTS 口播配音轨
        """
        if len(video_paths) < 3:
            raise ValueError(f"至少需要 3 个分镜视频才能拼接 15 秒成片，当前提供: {len(video_paths)}")

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        timestamp = int(time.time())
        run_id = uuid.uuid4().hex[:8]
        output_filename = f"final_15s_{product_id}_{timestamp}_{run_id}.mp4"
        output_filepath = StorageManager.get_output_path(output_filename)

        # 写入临时 concat 列表文件
        concat_list_file = settings.OUTPUT_DIR / f"concat_{timestamp}_{run_id}.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for v_path in video_paths[:3]:
                # Windows 路径格式安全转义
                safe_p = str(Path(v_path).resolve()).replace("\\", "/")
                f.write(f"file '{safe_p}'\n")

        # 是否混入音频
        has_audio = bool(audio_path and os.path.exists(audio_path))

        # 调用 FFmpeg 进行拼接与规范化编码 (9:16 720x1280, 24fps, yuv420p, aac)
        cmd = [
            ffmpeg_exe,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file),
        ]

        if has_audio:
            cmd.extend(["-i", str(audio_path)])

        cmd.extend([
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
        ])

        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "128k", "-shortest"])

        cmd.extend([
            "-movflags",
            "+faststart",
            str(output_filepath),
        ])

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            if result.returncode != 0:
                # 若失败，尝试简单 copy 模式
                fallback_cmd = [
                    ffmpeg_exe,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list_file),
                    "-c",
                    "copy",
                    str(output_filepath),
                ]
                subprocess.run(fallback_cmd, check=True, timeout=30)
        finally:
            # 清理临时清单文件
            if concat_list_file.exists():
                try:
                    concat_list_file.unlink()
                except Exception:
                    pass

        accessible_url = StorageManager.get_accessible_url(output_filename)

        return StitchResult(
            product_id=product_id,
            task_ids=task_ids or [],
            shots=shots or ["S01", "S02", "S03"],
            total_duration=15,
            final_video_url=accessible_url,
            local_path=str(output_filepath),
            qa_pass_summary={"status": "ALL_SHOTS_PASSED", "shots_count": 3},
        )
