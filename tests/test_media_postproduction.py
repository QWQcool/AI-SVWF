"""Focused regression tests for exact TTS timing and final media policy."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import imageio_ffmpeg
import pytest
from PIL import Image

from core.config import settings
from core.stitcher import StitcherService
from core.storage import StorageManager
from core.tts_service import TTSService


def _create_test_video(path: Path, duration: float = 5.0, color: str = "navy") -> None:
    """Create a small portrait fixture with native audio to test its removal."""
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=180x320:r=24:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-t",
        str(duration),
        str(path),
    ]
    subprocess.run(command, capture_output=True, check=True, timeout=45)


def test_batch_tts_normalizes_each_shot_and_the_merged_track(monkeypatch):
    durations = iter([1.2, 6.8, 3.4])

    async def fake_synthesize(cls, text, output_path, voice):  # noqa: ARG001
        cls._create_dummy_audio(output_path, next(durations))
        return "test_synthesizer"

    monkeypatch.setattr(TTSService, "synthesize_speech", classmethod(fake_synthesize))
    result = asyncio.run(TTSService.generate_batch_tts("测试商品", "测试描述"))

    assert len(result["scripts"]) == 3
    for script in result["scripts"]:
        metadata = TTSService._probe_audio(script["audio_path"])
        assert metadata["has_audio"] is True
        assert metadata["duration"] == pytest.approx(5.0, abs=0.20)

    merged_metadata = TTSService._probe_audio(result["merged_audio_path"])
    assert merged_metadata["has_audio"] is True
    assert merged_metadata["duration"] == pytest.approx(15.0, abs=0.20)


def test_tts_provider_failure_does_not_create_silent_delivery(monkeypatch, tmp_path):
    import edge_tts

    def fail_provider(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("synthetic provider outage")

    output = tmp_path / "must-not-exist.mp3"
    monkeypatch.setattr(edge_tts, "Communicate", fail_provider)
    with pytest.raises(RuntimeError, match="已停止有声成片交付"):
        asyncio.run(TTSService.synthesize_speech("测试口播", str(output)))
    assert not output.exists()


def test_stitcher_enforces_delivery_media_and_audio_policy(tmp_path):
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source_video = tmp_path / "source-with-provider-audio.mp4"
    narration = tmp_path / "approved-tts.mp3"
    _create_test_video(source_video)
    TTSService._create_dummy_audio(str(narration), 15.0)

    with_tts = StitcherService.stitch_3x5s_videos(
        [str(source_video)] * 3,
        product_id="MEDIA_WITH_TTS",
        audio_path=str(narration),
    )
    with_tts_metadata = StorageManager.inspect_video(with_tts.local_path)
    assert with_tts_metadata["width"] == 720
    assert with_tts_metadata["height"] == 1280
    assert with_tts_metadata["fps"] == pytest.approx(24.0, abs=0.15)
    assert with_tts_metadata["duration"] == pytest.approx(15.0, abs=0.25)
    assert with_tts_metadata["audio_codec"]
    assert with_tts.qa_pass_summary["media_validation"]["audio_policy"] == "tts_only"

    without_tts = StitcherService.stitch_3x5s_videos(
        [str(source_video)] * 3,
        product_id="MEDIA_WITHOUT_TTS",
    )
    without_tts_metadata = StorageManager.inspect_video(without_tts.local_path)
    assert without_tts_metadata["width"] == 720
    assert without_tts_metadata["height"] == 1280
    assert without_tts_metadata["fps"] == pytest.approx(24.0, abs=0.15)
    assert without_tts_metadata["duration"] == pytest.approx(15.0, abs=0.25)
    assert without_tts_metadata["audio_codec"] == ""
    assert without_tts.qa_pass_summary["media_validation"]["audio_policy"] == "no_audio"


def test_tts_stitch_failure_never_retries_as_silent_copy(monkeypatch, tmp_path):
    videos = []
    for index in range(3):
        video = tmp_path / f"shot-{index}.mp4"
        video.write_bytes(b"placeholder")
        videos.append(str(video))
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"placeholder")

    commands = []

    def fail_once(command, **kwargs):  # noqa: ARG001
        commands.append(command)
        return SimpleNamespace(returncode=1, stderr=b"synthetic ffmpeg failure")

    monkeypatch.setattr("core.stitcher.subprocess.run", fail_once)
    with pytest.raises(RuntimeError, match="未生成不合规兜底文件"):
        StitcherService.stitch_3x5s_videos(
            videos,
            product_id="MEDIA_FAIL_CLOSED",
            audio_path=str(narration),
        )

    assert len(commands) == 1
    assert "-an" not in commands[0]


def test_stitcher_normalizes_each_shot_before_five_second_boundaries(tmp_path):
    colors = (("red", 4.0), ("green", 6.0), ("blue", 5.0))
    videos = []
    for index, (color, duration) in enumerate(colors):
        path = tmp_path / f"{index}-{color}.mp4"
        _create_test_video(path, duration=duration, color=color)
        videos.append(str(path))

    result = StitcherService.stitch_3x5s_videos(videos, product_id="MEDIA_BOUNDARIES")

    def sample_rgb(seconds: float) -> tuple[int, int, int]:
        frame = tmp_path / f"frame-{seconds}.png"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(seconds),
            "-i",
            result.local_path,
            "-frames:v",
            "1",
            "-y",
            str(frame),
        ]
        subprocess.run(command, capture_output=True, check=True, timeout=30)
        with Image.open(frame) as image:
            return image.convert("RGB").getpixel((image.width // 2, image.height // 2))

    before_first_boundary = sample_rgb(4.5)
    after_first_boundary = sample_rgb(5.5)
    after_second_boundary = sample_rgb(10.5)
    assert before_first_boundary[0] > max(before_first_boundary[1:]) + 50
    assert after_first_boundary[1] > max(after_first_boundary[0], after_first_boundary[2]) + 25
    assert after_second_boundary[2] > max(after_second_boundary[:2]) + 50
