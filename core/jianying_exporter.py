"""
AI-SVWF 剪映电脑版 (Jianying Pro / CapCut) 草稿导出引擎 (core/jianying_exporter.py)
对齐 WebLockShot 与行业通用微秒级声画字对齐规范：
1. 时间基准：严格遵循微秒 (microseconds, 1秒 = 1,000,000 微秒)；
2. 画面规格：标准 1080×1920 (9:16 竖屏短视频), 30 FPS；
3. 工业级三轨微秒绝对对齐：
   - 视频主轨 (track_video): 3 个 5 秒分镜首尾无缝相接，绝无黑帧；
   - 旁白音频轨 (track_audio): TTS 生成的高清口播配音，按分镜严格入画；
   - 花字字幕轨 (track_text): 黄白高对比度带货文案，精准绑定分镜起止点；
4. 双重交付方式：
   - 生成便携 .zip 压缩包供下载与跨机器导入；
   - 自动检测并直写本机《剪映专业版》草稿库，打开剪映即可直接在时间线上二次精剪！
"""

import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from core.config import settings


def _random_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class JianyingExporter:
    @classmethod
    def get_local_jianying_draft_path(cls) -> Optional[Path]:
        """检测 Windows 本机是否安装剪映专业版并返回草稿存放根路径"""
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            return None
        jianying_drafts = Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
        if jianying_drafts.exists() and jianying_drafts.is_dir():
            return jianying_drafts
        return None

    @classmethod
    def export_draft(
        cls,
        product_name: str,
        video_paths: List[str],
        audio_paths: List[str],
        subtitles: List[str],
        durations_sec: Optional[List[float]] = None,
        project_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构建标准剪映工程草稿包 (draft_content.json + draft_meta_info.json + assets)
        """
        if durations_sec is None:
            durations_sec = [5.0] * len(video_paths)

        draft_id = _random_id("draft_")
        timestamp = int(time.time())
        title = project_title or f"AI-SVWF_{product_name}_{timestamp}"

        # 临时工作目录
        output_dir = settings.OUTPUT_DIR
        projects_dir = output_dir / "jianying_projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        draft_folder = projects_dir / f"AI-SVWF_{product_name[:12]}_{timestamp}"
        draft_folder.mkdir(parents=True, exist_ok=True)
        assets_folder = draft_folder / "assets"
        assets_folder.mkdir(parents=True, exist_ok=True)

        video_materials = []
        audio_materials = []
        text_materials = []

        video_segments = []
        audio_segments = []
        text_segments = []

        current_start_us = 0

        for i, (v_path, dur_sec) in enumerate(zip(video_paths, durations_sec)):
            dur_us = int(dur_sec * 1_000_000)
            a_path = audio_paths[i] if i < len(audio_paths) else ""
            sub_text = subtitles[i] if i < len(subtitles) else f"镜头 {i+1}"

            # 拷贝视频至工程 assets 目录
            v_src = Path(v_path)
            v_dest_name = f"video_S0{i+1}_{v_src.name}"
            v_dest = assets_folder / v_dest_name
            if v_src.exists():
                shutil.copy(v_src, v_dest)
            v_rel_path = str(v_dest.resolve())

            # 拷贝音频至工程 assets 目录
            a_rel_path = ""
            if a_path and os.path.exists(a_path):
                a_src = Path(a_path)
                a_dest_name = f"audio_S0{i+1}_{a_src.name}"
                a_dest = assets_folder / a_dest_name
                shutil.copy(a_src, a_dest)
                a_rel_path = str(a_dest.resolve())

            # 1. 视频素材及片段
            v_mat_id = _random_id("mat_v_")
            video_materials.append({
                "id": v_mat_id,
                "duration": dur_us,
                "height": 1920,
                "width": 1080,
                "material_name": v_dest_name,
                "path": v_rel_path,
                "type": "video",
                "category_name": "local",
                "extra_type_option": 0,
                "has_audio": True,
            })
            video_segments.append({
                "id": _random_id("seg_v_"),
                "material_id": v_mat_id,
                "target_timerange": {
                    "duration": dur_us,
                    "start": current_start_us,
                },
                "source_timerange": {
                    "duration": dur_us,
                    "start": 0,
                },
                "speed": 1.0,
                "volume": 1.0,
                "render_index": 0,
                "extra_material_refs": [],
                "clip": {
                    "alpha": 1.0,
                    "flip": {"horizontal": False, "vertical": False},
                    "rotation": 0.0,
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": 0.0, "y": 0.0},
                },
                "common_keyframes": [],
                "uniform_scale": {"on": True, "value": 1.0},
            })

            # 2. 旁白音频素材及片段
            if a_rel_path:
                a_mat_id = _random_id("mat_a_")
                audio_materials.append({
                    "id": a_mat_id,
                    "duration": dur_us,
                    "material_name": Path(a_rel_path).name,
                    "path": a_rel_path,
                    "type": "audio",
                    "category_name": "local",
                    "extra_type_option": 0,
                })
                audio_segments.append({
                    "id": _random_id("seg_a_"),
                    "material_id": a_mat_id,
                    "target_timerange": {
                        "duration": dur_us,
                        "start": current_start_us,
                    },
                    "source_timerange": {
                        "duration": dur_us,
                        "start": 0,
                    },
                    "speed": 1.0,
                    "volume": 1.0,
                    "render_index": 0,
                    "extra_material_refs": [],
                    "clip": {
                        "alpha": 1.0,
                        "flip": {"horizontal": False, "vertical": False},
                        "rotation": 0.0,
                        "scale": {"x": 1.0, "y": 1.0},
                        "transform": {"x": 0.0, "y": 0.0},
                    },
                    "common_keyframes": [],
                    "uniform_scale": {"on": True, "value": 1.0},
                })

            # 3. 花字字幕素材及片段
            t_mat_id = _random_id("mat_t_")
            text_materials.append({
                "id": t_mat_id,
                "content": json.dumps({
                    "styles": [
                        {
                            "fill": {
                                "alpha": 1.0,
                                "content": {
                                    "render_type": "solid",
                                    "solid": {
                                        "color": [1.0, 0.85, 0.0] if i == 0 else [1.0, 1.0, 1.0],
                                    },
                                },
                            },
                            "font": {"id": "", "path": ""},
                            "range": [0, len(sub_text)],
                            "size": 9.5 if i == 0 else 8.0,
                        }
                    ],
                    "text": sub_text,
                }, ensure_ascii=False),
                "type": "text",
            })
            text_segments.append({
                "id": _random_id("seg_t_"),
                "material_id": t_mat_id,
                "target_timerange": {
                    "duration": dur_us,
                    "start": current_start_us,
                },
                "render_index": 0,
                "extra_material_refs": [],
                "clip": {
                    "alpha": 1.0,
                    "flip": {"horizontal": False, "vertical": False},
                    "rotation": 0.0,
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": 0.0, "y": -0.65},  # 居于下部黄金字幕安全区
                },
                "common_keyframes": [],
                "uniform_scale": {"on": True, "value": 1.0},
            })

            current_start_us += dur_us

        # 组装 draft_content.json
        draft_content = {
            "canvas_config": {
                "height": 1920,
                "ratio": "9:16",
                "width": 1080,
            },
            "color_space": 0,
            "cover": "",
            "config": {
                "adjust_max_index": 1,
                "attachment_info": [],
                "combination_max_index": 1,
                "export_range": None,
                "extract_light_source": False,
                "lyrics_recognition_id": "",
                "lyrics_sync": True,
                "lyrics_taskinfo": [],
                "maintrack_adsorb": True,
                "material_save_mode": 0,
                "original_sound_last_has_read": False,
                "record_audio_last_has_read": False,
                "roughcut_time_range": {"duration": 0, "start": 0},
                "sub_scene": "default",
                "timeline_speed_scale": 1.0,
                "video_speed_curve": False,
                "video_time_base": 30,
                "zoom_info_params": None,
                "project_name": title,
            },
            "duration": current_start_us,
            "fps": 30.0,
            "id": draft_id,
            "platform": {
                "all": False,
                "app_id": 3704,
                "app_source": "ai-svwf",
                "app_version": "6.0.0",
                "device_id": "",
                "os": "windows",
            },
            "materials": {
                "videos": video_materials,
                "audios": audio_materials,
                "texts": text_materials,
                "speeds": [],
                "canvases": [],
            },
            "tracks": [
                {
                    "attribute": 0,
                    "flag": 0,
                    "id": _random_id("track_v_"),
                    "is_default_name": True,
                    "name": "视频主轨道",
                    "segments": video_segments,
                    "type": "video",
                },
                {
                    "attribute": 0,
                    "flag": 0,
                    "id": _random_id("track_a_"),
                    "is_default_name": True,
                    "name": "TTS 口播配音轨",
                    "segments": audio_segments,
                    "type": "audio",
                },
                {
                    "attribute": 0,
                    "flag": 0,
                    "id": _random_id("track_t_"),
                    "is_default_name": True,
                    "name": "带货花字字幕轨",
                    "segments": text_segments,
                    "type": "text",
                },
            ],
            "version": 3000000,
        }

        # 写入 draft_content.json
        content_path = draft_folder / "draft_content.json"
        with open(content_path, "w", encoding="utf-8") as f:
            json.dump(draft_content, f, ensure_ascii=False, indent=2)

        # 写入 draft_meta_info.json
        draft_meta = {
            "draft_fold_path": str(draft_folder.resolve()),
            "draft_id": draft_id,
            "draft_name": title,
            "draft_timeline_materials_size": len(video_materials) + len(audio_materials) + len(text_materials),
            "tm_draft_cloud_completed": "",
            "tm_draft_create": timestamp * 1000,
            "tm_draft_modified": timestamp * 1000,
            "tm_duration": current_start_us,
        }
        meta_path = draft_folder / "draft_meta_info.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(draft_meta, f, ensure_ascii=False, indent=2)

        # 4. 打包为 Zip 压缩包
        zip_filename = f"Jianying_Draft_{product_name[:10]}_{timestamp}.zip"
        zip_path = output_dir / zip_filename
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(draft_folder):
                for file in files:
                    full_p = Path(root) / file
                    rel_p = full_p.relative_to(draft_folder)
                    zf.write(full_p, arcname=str(rel_p))

        # 5. 自动同步直写本机剪映目录
        local_draft_base = cls.get_local_jianying_draft_path()
        synced_local_path = None
        if local_draft_base:
            try:
                target_local_draft = local_draft_base / draft_folder.name
                shutil.copytree(draft_folder, target_local_draft, dirs_exist_ok=True)
                synced_local_path = str(target_local_draft.resolve())
            except Exception as e:
                print(f"[JianyingExporter] 同步本机剪映目录失败: {e}")

        return {
            "draft_id": draft_id,
            "draft_name": title,
            "duration_sec": current_start_us / 1_000_000,
            "zip_path": str(zip_path.resolve()),
            "zip_url": f"/outputs/{zip_filename}",
            "zip_filename": zip_filename,
            "synced_to_local_jianying": bool(synced_local_path),
            "local_draft_path": synced_local_path,
        }
