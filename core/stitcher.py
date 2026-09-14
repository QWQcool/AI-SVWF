"""
AI-SVWF 视频无缝缝合与后期合成服务 (StitcherService)
借鉴 MoneyPrinterTurbo 与交接文档规范，使用 FFmpeg 将通过验收的 S01、S02、S03 (3×5s)
无缝转码并拼接为 15 秒 9:16 标准电商带货成片。
"""

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
    TARGET_WIDTH = 720
    TARGET_HEIGHT = 1280
    TARGET_FPS = 24.0
    SEGMENT_DURATION_SECONDS = 5.0
    TARGET_DURATION_SECONDS = 15.0

    @classmethod
    def _validate_output(cls, output_path: str, expect_audio: bool) -> Dict[str, Any]:
        """在交付前强制校验成片的画幅、帧率、时长和音频策略。"""
        output = Path(output_path)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("视频缝合未生成有效输出文件")

        metadata = StorageManager.inspect_video(str(output))
        errors = []
        if metadata["width"] != cls.TARGET_WIDTH or metadata["height"] != cls.TARGET_HEIGHT:
            errors.append(
                f"画幅 {metadata['width']}x{metadata['height']}，"
                f"期望 {cls.TARGET_WIDTH}x{cls.TARGET_HEIGHT}"
            )
        if abs(metadata["fps"] - cls.TARGET_FPS) > 0.15:
            errors.append(f"帧率 {metadata['fps']:.3f}fps，期望 {cls.TARGET_FPS:.0f}fps")
        if abs(metadata["duration"] - cls.TARGET_DURATION_SECONDS) > 0.25:
            errors.append(
                f"时长 {metadata['duration']:.3f}s，期望 {cls.TARGET_DURATION_SECONDS:.0f}s"
            )

        has_audio = bool(metadata.get("audio_codec"))
        if expect_audio and not has_audio:
            errors.append("启用 TTS 后成片缺少口播音轨")
        if not expect_audio and has_audio:
            errors.append("未启用 TTS 时成片仍包含未批准音轨")
        if errors:
            raise RuntimeError("成片媒体规范校验失败：" + "；".join(errors))
        return metadata

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

        selected_video_paths = [Path(path).resolve() for path in video_paths[:3]]
        missing_videos = [path.name for path in selected_video_paths if not path.is_file()]
        if missing_videos:
            raise ValueError(f"待拼接分镜视频不存在: {', '.join(missing_videos)}")

        has_audio = bool(audio_path)
        if has_audio and not Path(str(audio_path)).is_file():
            raise ValueError("已启用 TTS，但合并口播音轨不存在")

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        timestamp = int(time.time())
        run_id = uuid.uuid4().hex[:8]
        output_filename = f"final_15s_{product_id}_{timestamp}_{run_id}.mp4"
        output_filepath = StorageManager.get_output_path(output_filename)

        # 每个分镜先独立补帧/裁切到恰好 5 秒，再执行 concat。供应商可能返回
        # 4.x/5.x 秒素材；如果只规范最终总时长，后两个分镜和 TTS 的 5 秒边界会错位。
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]
        for video_path in selected_video_paths:
            cmd.extend(["-i", str(video_path)])

        if has_audio:
            cmd.extend(["-i", str(audio_path)])

        per_shot_filter = (
            f"scale={cls.TARGET_WIDTH}:{cls.TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={cls.TARGET_WIDTH}:{cls.TARGET_HEIGHT},"
            f"fps={cls.TARGET_FPS:.0f},"
            f"tpad=stop_mode=clone:stop_duration={cls.SEGMENT_DURATION_SECONDS:.0f},"
            f"trim=duration={cls.SEGMENT_DURATION_SECONDS:.0f},"
            "setpts=PTS-STARTPTS,format=yuv420p"
        )
        filter_parts = [
            f"[{index}:v:0]{per_shot_filter}[v{index}]"
            for index in range(3)
        ]
        filter_parts.append("[v0][v1][v2]concat=n=3:v=1:a=0[outv]")
        cmd.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{cls.TARGET_FPS:.0f}",
        ])

        if has_audio:
            # Explicit maps prevent the provider's native audio from winning
            # over the approved post-production narration track.
            # The narration is itself normalized to 15 seconds; apad/atrim is
            # repeated here so a malformed caller-provided track cannot shorten
            # the finished video. Both streams are capped independently.
            cmd.extend([
                "-map",
                "3:a:0",
                "-af",
                f"apad,atrim=duration={cls.TARGET_DURATION_SECONDS:.0f},asetpts=N/SR/TB",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
            ])
        else:
            # The handoff policy forbids model-generated voice/audio.
            cmd.append("-an")

        cmd.extend([
            "-t",
            f"{cls.TARGET_DURATION_SECONDS:.0f}",
            "-movflags",
            "+faststart",
            str(output_filepath),
        ])

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
            if result.returncode != 0:
                output_filepath.unlink(missing_ok=True)
                error_message = result.stderr.decode("utf-8", errors="replace")[-1200:]
                mode = "TTS" if has_audio else "无声"
                raise RuntimeError(f"{mode}成片合成失败，未生成不合规兜底文件: {error_message}")
            try:
                metadata = cls._validate_output(str(output_filepath), expect_audio=has_audio)
            except Exception:
                output_filepath.unlink(missing_ok=True)
                raise
        except Exception:
            output_filepath.unlink(missing_ok=True)
            raise

        accessible_url = StorageManager.get_accessible_url(output_filename)

        return StitchResult(
            product_id=product_id,
            task_ids=task_ids or [],
            shots=shots or ["S01", "S02", "S03"],
            total_duration=15,
            final_video_url=accessible_url,
            local_path=str(output_filepath),
            qa_pass_summary={
                "status": "ALL_SHOTS_PASSED",
                "shots_count": 3,
                "media_validation": {
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "fps": metadata["fps"],
                    "duration": metadata["duration"],
                    "audio_codec": metadata["audio_codec"],
                    "audio_policy": "tts_only" if has_audio else "no_audio",
                },
            },
        )
