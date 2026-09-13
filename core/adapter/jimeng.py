"""
AI-SVWF 即梦 (Jimeng / Seedance) 视频生成适配器
严格对齐《AI带货视频工作流_MVP技术交接文档_V1.0.md》第 11、12、13、24 节

设计核心：
1. 统一标准接口：generate_video(provider, model, prompt, image_url, duration, aspect_ratio)；
2. 任务状态机：CREATED -> SUBMITTED -> PROCESSING -> COMPLETED (或 FAILED) -> QA_PENDING；
3. 双模驱动：支持真实企业级 REST API 调用与离线高保真 Mock 发生器无缝切换。
"""

import uuid
import time
import asyncio
import requests
from typing import Dict, Optional, Any
from core.config import settings
from core.schemas import VideoTaskRecord, TaskStatus
from core.storage import StorageManager


class JimengAdapter:
    # 内存任务注册表 (供快速查询)
    _tasks: Dict[str, VideoTaskRecord] = {}

    @classmethod
    def get_task(cls, internal_task_id: str) -> Optional[VideoTaskRecord]:
        """查询任务状态"""
        return cls._tasks.get(internal_task_id)

    @classmethod
    def list_tasks(cls, product_id: Optional[str] = None) -> list[VideoTaskRecord]:
        """获取所有任务或指定商品的任务列表"""
        tasks = list(cls._tasks.values())
        if product_id:
            tasks = [t for t in tasks if t.product_id == product_id]
        return sorted(tasks, key=lambda x: x.created_at, reverse=True)

    @classmethod
    async def submit_video_task(
        cls,
        product_id: str,
        shot_id: str,
        prompt: str,
        negative_prompt: str = "",
        image_url: str = "",
        provider: str = "jimeng",
        model: str = "jimeng-video-v2",
        prompt_version: str = "1.0",
        duration: int = 5,
        aspect_ratio: str = "9:16",
        product_name: str = "测试商品",
    ) -> VideoTaskRecord:
        """
        提交视频生成任务 (异步)
        遵循交接文档 Section 24 统一出参规范
        """
        internal_task_id = f"TASK_{uuid.uuid4().hex[:10].upper()}"

        task = VideoTaskRecord(
            internal_task_id=internal_task_id,
            provider_task_id="",
            product_id=product_id,
            shot_id=shot_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            prompt_text=prompt,
            negative_prompt=negative_prompt,
            source_image=image_url,
            duration=duration,
            aspect_ratio=aspect_ratio,
            status=TaskStatus.SUBMITTED,
        )

        cls._tasks[internal_task_id] = task

        # 判断是否走 Mock 模式或真实 API
        is_mock = settings.MOCK_MODE or not bool(settings.JIMENG_API_KEY)

        if is_mock:
            # 启动 Mock 异步模拟处理管线
            asyncio.create_task(
                cls._process_mock_task(internal_task_id, product_name, duration, prompt_version)
            )
        else:
            # 启动真实即梦 API 调度与轮询管线
            asyncio.create_task(
                cls._process_real_api_task(internal_task_id)
            )

        return task

    @classmethod
    async def _process_mock_task(
        cls,
        internal_task_id: str,
        product_name: str,
        duration: int,
        prompt_version: str,
    ):
        """模拟即梦视频生成生命周期 (秒级平滑流转)"""
        task = cls._tasks[internal_task_id]
        start_time = time.time()

        task.status = TaskStatus.PROCESSING
        task.provider_task_id = f"jm_mock_{uuid.uuid4().hex[:8]}"
        await asyncio.sleep(1.2)  # 模拟提交与排队耗时

        # 模拟生成真实 9:16 MP4 文件
        local_path, accessible_url = StorageManager.create_mock_video(
            shot_id=task.shot_id,
            version=prompt_version,
            product_name=product_name,
            duration=duration,
        )

        elapsed = round(time.time() - start_time, 2)
        cost_info = settings.calculate_cost(duration)

        task.status = TaskStatus.COMPLETED
        task.video_url = accessible_url
        task.local_video_path = local_path
        task.generation_time_seconds = elapsed
        task.estimated_cost = cost_info["cost_cny"]
        task.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    async def _process_real_api_task(cls, internal_task_id: str):
        """调用真实即梦/火山引擎 REST API 生成视频并异步轮询"""
        task = cls._tasks[internal_task_id]
        start_time = time.time()
        task.status = TaskStatus.PROCESSING

        try:
            headers = {
                "Authorization": f"Bearer {settings.JIMENG_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": task.model,
                "prompt": task.prompt_text,
                "negative_prompt": task.negative_prompt,
                "image_url": task.source_image,
                "duration": task.duration,
                "aspect_ratio": task.aspect_ratio,
            }

            # 1. 提交任务
            resp = requests.post(settings.JIMENG_API_BASE_URL, json=payload, headers=headers, timeout=15)
            data = resp.json()
            provider_task_id = data.get("task_id") or data.get("id") or f"jm_{uuid.uuid4().hex[:8]}"
            task.provider_task_id = provider_task_id

            # 2. 异步轮询 (指数退避)
            poll_url = f"{settings.JIMENG_API_BASE_URL}/{provider_task_id}"
            max_retries = 60  # 最多等待约 3 分钟
            interval = 2.0

            for _ in range(max_retries):
                await asyncio.sleep(interval)
                poll_resp = requests.get(poll_url, headers=headers, timeout=10)
                poll_data = poll_resp.json()
                status = poll_data.get("status", "").upper()

                if status in ["COMPLETED", "SUCCESS", "SUCCEEDED"]:
                    video_url = poll_data.get("video_url") or poll_data.get("result", {}).get("video_url")
                    elapsed = round(time.time() - start_time, 2)
                    cost_info = settings.calculate_cost(task.duration)

                    task.status = TaskStatus.COMPLETED
                    task.video_url = video_url
                    task.generation_time_seconds = elapsed
                    task.estimated_cost = cost_info["cost_cny"]
                    task.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    return
                elif status in ["FAILED", "ERROR"]:
                    task.status = TaskStatus.FAILED
                    task.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    return

                interval = min(interval * 1.2, 5.0)

            # 超时处理
            task.status = TaskStatus.FAILED
        except Exception as e:
            task.status = TaskStatus.FAILED
