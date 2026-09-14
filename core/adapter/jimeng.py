"""Video task orchestration behind the handoff document's provider contract.

The historic class name is kept for backwards compatibility. It now persists
every state transition in SQLite and respects provider/model/duration/ratio.
"""

import asyncio
import time
import uuid
from typing import Dict, Optional

import requests

from core.config import settings
from core.database import database
from core.schemas import TaskStatus, VideoTaskRecord, utc_now_iso
from core.storage import StorageManager


class JimengAdapter:
    _tasks: Dict[str, VideoTaskRecord] = {}

    @classmethod
    def _save(cls, task: VideoTaskRecord, detail: Optional[dict] = None) -> None:
        task.updated_at = utc_now_iso()
        cls._tasks[task.internal_task_id] = task
        database.upsert_task(task, detail)

    @classmethod
    def get_task(cls, internal_task_id: str) -> Optional[VideoTaskRecord]:
        task = cls._tasks.get(internal_task_id) or database.get_task(internal_task_id)
        if task:
            cls._tasks[internal_task_id] = task
        return task

    @classmethod
    def list_tasks(cls, product_id: Optional[str] = None) -> list[VideoTaskRecord]:
        return database.list_tasks(product_id)

    @staticmethod
    def _provider_key(provider: str) -> str:
        provider = provider.lower()
        if provider in {"jimeng", "seedance"}:
            return settings.SEEDANCE_ARK_API_KEY or settings.JIMENG_API_KEY
        if provider == "kling":
            return settings.KLING_API_KEY
        return ""

    @classmethod
    async def submit_video_task(
        cls,
        product_id: str,
        shot_id: str,
        prompt: str,
        negative_prompt: str = "",
        image_url: str = "",
        provider: str = "mock",
        model: str = "mock-video-v1",
        prompt_version: str = "1.0",
        duration: int = 5,
        aspect_ratio: str = "9:16",
        product_name: str = "测试商品",
        parent_task_id: Optional[str] = None,
        variant_id: Optional[str] = None,
    ) -> VideoTaskRecord:
        internal_task_id = f"TASK_{uuid.uuid4().hex[:10].upper()}"
        task = VideoTaskRecord(
            internal_task_id=internal_task_id,
            product_id=product_id,
            shot_id=shot_id,
            provider=provider.strip().lower(),
            model=model.strip(),
            execution_mode="mock" if settings.MOCK_MODE or provider.strip().lower() == "mock" else "real",
            prompt_version=prompt_version,
            variant_id=variant_id,
            prompt_text=prompt,
            negative_prompt=negative_prompt,
            source_image=image_url,
            duration=duration,
            aspect_ratio=aspect_ratio,
            parent_task_id=parent_task_id,
            status=TaskStatus.CREATED,
        )
        cls._save(task)
        task.status = TaskStatus.SUBMITTED
        cls._save(task)

        if task.execution_mode == "mock":
            asyncio.create_task(cls._process_mock_task(task.internal_task_id, product_name))
        else:
            api_key = cls._provider_key(task.provider)
            if not api_key:
                task.status = TaskStatus.FAILED
                task.error_code = "PROVIDER_NOT_CONFIGURED"
                task.error_message = f"未配置 {task.provider} 的真实视频 API Key"
                cls._save(task)
            elif task.provider not in {"jimeng", "seedance"}:
                task.status = TaskStatus.FAILED
                task.error_code = "PROVIDER_ADAPTER_PENDING"
                task.error_message = f"{task.provider} 已保留统一契约，但需要按供应商文档实现签名和轮询"
                cls._save(task)
            elif not settings.JIMENG_API_BASE_URL:
                task.status = TaskStatus.FAILED
                task.error_code = "PROVIDER_ENDPOINT_NOT_CONFIGURED"
                task.error_message = "未配置供应商 API endpoint；不会猜测或调用未经确认的地址"
                cls._save(task)
            else:
                asyncio.create_task(cls._process_real_api_task(task.internal_task_id, api_key))
        return task

    @classmethod
    async def _process_mock_task(cls, internal_task_id: str, product_name: str) -> None:
        task = cls.get_task(internal_task_id)
        if not task:
            return
        started = time.monotonic()
        try:
            task.status = TaskStatus.PROCESSING
            task.provider_task_id = f"mock_{uuid.uuid4().hex[:10]}"
            cls._save(task)
            await asyncio.sleep(0.15)
            local_path, url = await asyncio.to_thread(
                StorageManager.create_mock_video,
                task.shot_id,
                task.prompt_version,
                product_name,
                task.duration,
            )
            task.status = TaskStatus.COMPLETED
            task.video_url = url
            task.local_video_path = local_path
            task.generation_time_seconds = round(time.monotonic() - started, 2)
            task.estimated_cost = settings.calculate_cost(task.duration)["cost_cny"]
            task.completed_at = utc_now_iso()
            cls._save(task)
            task.status = TaskStatus.QA_PENDING
            cls._save(task)
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error_code = "MOCK_GENERATION_FAILED"
            task.error_message = str(exc)
            cls._save(task)

    @classmethod
    async def _process_real_api_task(cls, internal_task_id: str, api_key: str) -> None:
        """Generic async HTTP skeleton; final vendor mapping is completed with the real API docs/key."""
        task = cls.get_task(internal_task_id)
        if not task:
            return
        started = time.monotonic()
        task.status = TaskStatus.PROCESSING
        cls._save(task)
        try:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": task.model,
                "prompt": task.prompt_text,
                "negative_prompt": task.negative_prompt,
                "image_url": task.source_image,
                "duration": task.duration,
                "aspect_ratio": task.aspect_ratio,
            }
            response = await asyncio.to_thread(
                requests.post, settings.JIMENG_API_BASE_URL, json=payload, headers=headers, timeout=30
            )
            response.raise_for_status()
            data = response.json()
            provider_task_id = data.get("task_id") or data.get("id")
            if not provider_task_id:
                raise ValueError("供应商提交响应缺少 task_id/id")
            task.provider_task_id = str(provider_task_id)
            cls._save(task)

            poll_url = f"{settings.JIMENG_API_BASE_URL.rstrip('/')}/{provider_task_id}"
            interval = 2.0
            for _ in range(60):
                await asyncio.sleep(interval)
                poll = await asyncio.to_thread(requests.get, poll_url, headers=headers, timeout=20)
                poll.raise_for_status()
                result = poll.json()
                provider_status = str(result.get("status", "")).upper()
                if provider_status in {"COMPLETED", "SUCCESS", "SUCCEEDED"}:
                    remote_url = result.get("video_url") or result.get("result", {}).get("video_url")
                    if not remote_url:
                        raise ValueError("供应商完成响应缺少 video_url")
                    local_path, local_url = await asyncio.to_thread(
                        StorageManager.download_remote_video, remote_url, task.provider
                    )
                    task.status = TaskStatus.COMPLETED
                    task.video_url = local_url
                    task.local_video_path = local_path
                    task.generation_time_seconds = round(time.monotonic() - started, 2)
                    task.estimated_cost = settings.calculate_cost(task.duration)["cost_cny"]
                    task.completed_at = utc_now_iso()
                    cls._save(task, {"provider_video_url": remote_url})
                    task.status = TaskStatus.QA_PENDING
                    cls._save(task)
                    return
                if provider_status in {"FAILED", "ERROR", "CANCELLED"}:
                    raise RuntimeError(result.get("message") or f"供应商任务状态: {provider_status}")
                interval = min(interval * 1.25, 8.0)
            raise TimeoutError("供应商任务轮询超时")
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error_code = "PROVIDER_GENERATION_FAILED"
            task.error_message = str(exc)
            cls._save(task)
