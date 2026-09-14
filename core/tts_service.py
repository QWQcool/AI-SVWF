"""
AI-SVWF TTS 口播语音合成引擎 (core/tts_service.py)
对齐 MoneyPrinterTurbo 与 WebLockShot 工业级口播标准：
1. 基于 edge-tts 提供超高拟真度神经网络语音 (如: zh-CN-XiaoxiaoNeural, zh-CN-YunxiNeural)；
2. 自动根据结构化商品卖点与分镜时序生成 3 段各 5 秒对齐口播台词；
3. 输出与视频微秒级对齐的 MP3 音频切片，并合并为 15s 完整配音轨；
4. 启用 TTS 时失败即停止交付，绝不把静音占位伪装成真实口播。
"""

import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import imageio_ffmpeg

from core.config import settings

VOICE_PRESETS = {
    "xiaoxiao": {"name": "晓晓 (带货推荐女声)", "voice": "zh-CN-XiaoxiaoNeural"},
    "yunxi": {"name": "云溪 (阳光带货男声)", "voice": "zh-CN-YunxiNeural"},
    "yunjian": {"name": "云健 (影视专业解说)", "voice": "zh-CN-YunjianNeural"},
    "xiaoyi": {"name": "小怡 (亲和生活女声)", "voice": "zh-CN-XiaoyiNeural"},
}


class TTSService:
    SEGMENT_DURATION_SECONDS = 5.0
    MERGED_DURATION_SECONDS = 15.0

    @classmethod
    def generate_shot_scripts(cls, product_name: str, desc: str = "") -> List[Dict[str, Any]]:
        """
        根据商品名称与描述，自动生成 3×5s 契合分镜节拍的带货口播台词
        S01 (0~5s): 黄金 3 秒钩子，品牌与卖点前置
        S02 (5~10s): 核心动作与单手交互体验
        S03 (10~15s): 场景记忆点与行动号召 (CTA)
        """
        short_desc = desc.split("，")[0].split("。")[0] if desc else "品质生活优选"
        if len(short_desc) > 16:
            short_desc = short_desc[:16]

        return [
            {
                "shot_id": "S01",
                "text": f"画面展示{product_name}。用户提供的信息是：{short_desc}。",
                "subtitle": f"{product_name} · 用户提供信息：{short_desc}",
                "duration_sec": 5.0,
            },
            {
                "shot_id": "S02",
                "text": "人物在日常场景中拿起商品，展示一个简单动作。",
                "subtitle": "日常场景 · 简单动作",
                "duration_sec": 5.0,
            },
            {
                "shot_id": "S03",
                "text": "商品回到桌面，画面保留清晰的商品主体。",
                "subtitle": "商品主体 · 清晰呈现",
                "duration_sec": 5.0,
            },
        ]

    @classmethod
    async def synthesize_speech(cls, text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
        """调用 edge-tts 合成单段语音"""
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
                return "edge_tts"
            raise RuntimeError("Edge TTS 返回了空音频")
        except Exception as exc:
            Path(output_path).unlink(missing_ok=True)
            raise RuntimeError(f"Edge TTS 口播生成失败，已停止有声成片交付: {exc}") from exc

    @classmethod
    def _create_dummy_audio(cls, output_path: str, duration_sec: float = 5.0):
        """生成测试夹具使用的合法静音 MP3；生产 TTS 路径不会调用它。"""
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(duration_sec),
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(destination),
        ]
        result = subprocess.run(command, capture_output=True, timeout=30)
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            message = result.stderr.decode("utf-8", errors="replace")[-800:]
            raise RuntimeError(f"TTS 静音兜底轨生成失败: {message}")

    @classmethod
    def _probe_audio(cls, audio_path: str) -> Dict[str, Any]:
        """使用随项目安装的 FFmpeg 校验音频流和时长。"""
        source = Path(audio_path)
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f"音频文件不存在或为空: {source.name}")

        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-i",
            str(source),
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(command, capture_output=True, timeout=45)
        diagnostic = result.stderr.decode("utf-8", errors="replace")
        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", diagnostic)
        if result.returncode != 0 or "Audio:" not in diagnostic or not duration_match:
            raise RuntimeError(f"音频文件校验失败: {source.name}")
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return {"duration": duration, "has_audio": True}

    @classmethod
    def _normalize_audio_duration(
        cls,
        input_path: str,
        output_path: str,
        duration_sec: float,
    ) -> None:
        """将一段语音截断或补静音到精确的分镜时长，再原子替换目标文件。"""
        source = Path(input_path)
        destination = Path(output_path)
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError("TTS 合成结果不存在或为空")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.normalized.mp3")
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            f"apad,atrim=duration={duration_sec},asetpts=N/SR/TB",
            "-t",
            str(duration_sec),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(temporary),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=45)
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                message = result.stderr.decode("utf-8", errors="replace")[-800:]
                raise RuntimeError(f"TTS 分镜音频时长归一化失败: {message}")
            metadata = cls._probe_audio(str(temporary))
            if abs(metadata["duration"] - duration_sec) > 0.20:
                raise RuntimeError(
                    f"TTS 分镜音频时长不合规: {metadata['duration']:.2f}s，期望 {duration_sec:.2f}s"
                )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    async def generate_batch_tts(
        cls,
        product_name: str,
        desc: str = "",
        voice_key: str = "xiaoxiao",
    ) -> Dict[str, Any]:
        """
        批量生成 S01, S02, S03 语音，并输出全量 15s 对齐音频
        """
        voice_info = VOICE_PRESETS.get(voice_key, VOICE_PRESETS["xiaoxiao"])
        voice_tag = voice_info["voice"]
        scripts = cls.generate_shot_scripts(product_name, desc)

        output_dir = settings.OUTPUT_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        run_id = uuid.uuid4().hex[:8]

        shot_audio_paths = {}
        synthesis_modes = []
        for s in scripts:
            shot_id = s["shot_id"]
            raw_audio_file = output_dir / f".tts_{shot_id}_{timestamp}_{run_id}.raw.mp3"
            audio_file = output_dir / f"tts_{shot_id}_{timestamp}_{run_id}.mp3"
            try:
                mode = await cls.synthesize_speech(s["text"], str(raw_audio_file), voice_tag)
                cls._normalize_audio_duration(
                    str(raw_audio_file),
                    str(audio_file),
                    cls.SEGMENT_DURATION_SECONDS,
                )
            finally:
                raw_audio_file.unlink(missing_ok=True)
            synthesis_modes.append(mode)
            s["synthesis_mode"] = mode
            s["audio_path"] = str(audio_file)
            s["audio_url"] = f"/outputs/{audio_file.name}"
            shot_audio_paths[shot_id] = str(audio_file)

        # 合并 3 段音频为 15s 完整配音轨
        merged_audio_path = output_dir / f"tts_full_15s_{timestamp}_{run_id}.mp3"
        cls._merge_audio_files(list(shot_audio_paths.values()), str(merged_audio_path))

        return {
            "voice": voice_info["name"],
            "scripts": scripts,
            "merged_audio_path": str(merged_audio_path),
            "merged_audio_url": f"/outputs/{merged_audio_path.name}",
            "tts_available": all(mode == "edge_tts" for mode in synthesis_modes),
            "degraded": any(mode != "edge_tts" for mode in synthesis_modes),
            "warning": "当前使用测试替身音轨" if any(mode != "edge_tts" for mode in synthesis_modes) else "",
            "timestamp": timestamp,
        }

    @classmethod
    def _merge_audio_files(cls, audio_paths: List[str], output_merged_path: str):
        """重编码拼接三段严格 5 秒的音频，输出严格 15 秒的完整口播轨。"""
        if len(audio_paths) != 3:
            raise ValueError(f"15 秒口播轨必须由 3 段音频组成，当前提供: {len(audio_paths)}")

        destination = Path(output_merged_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.merging.mp3")
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        command = [ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y"]
        for audio_path in audio_paths:
            source = Path(audio_path)
            if not source.is_file() or source.stat().st_size == 0:
                raise RuntimeError(f"待合并的 TTS 分镜音频不存在或为空: {source.name}")
            command.extend(["-i", str(source)])

        filters = []
        labels = []
        for index in range(3):
            label = f"a{index}"
            filters.append(
                f"[{index}:a:0]apad,atrim=duration={cls.SEGMENT_DURATION_SECONDS},"
                f"asetpts=N/SR/TB[{label}]"
            )
            labels.append(f"[{label}]")
        filters.append(f"{''.join(labels)}concat=n=3:v=0:a=1[outa]")
        command.extend([
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outa]",
            "-t",
            str(cls.MERGED_DURATION_SECONDS),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(temporary),
        ])
        try:
            result = subprocess.run(command, capture_output=True, timeout=60)
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
                message = result.stderr.decode("utf-8", errors="replace")[-800:]
                raise RuntimeError(f"15 秒 TTS 口播轨合并失败: {message}")
            metadata = cls._probe_audio(str(temporary))
            if abs(metadata["duration"] - cls.MERGED_DURATION_SECONDS) > 0.20:
                raise RuntimeError(
                    f"合并口播轨时长不合规: {metadata['duration']:.2f}s，"
                    f"期望 {cls.MERGED_DURATION_SECONDS:.2f}s"
                )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
