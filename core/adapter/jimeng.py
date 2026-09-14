"""Persistent Ark Seedance task orchestration.

The historic class name stays for API compatibility. Real tasks use the
official Volcano Ark create/query endpoints, while Mock remains an explicit
provider selected by the caller.
"""

import asyncio
import hashlib
import json
import time
import uuid
from typing import Dict, Optional

from core.ark_client import ArkAPIError, ArkClient
from core.config import settings
from core.database import database
from core.errors import QuotaExceededError
from core.schemas import PublicVirtualActor, TaskStatus, VideoTaskRecord, utc_now_iso
from core.storage import StorageManager


class ProviderTerminalError(ArkAPIError):
    """The provider itself reported a terminal task failure."""


class ProviderMediaError(ArkAPIError):
    """The downloaded provider artifact cannot safely enter the workflow."""


class JimengAdapter:
    _tasks: Dict[str, VideoTaskRecord] = {}
    _running_tasks: set[str] = set()
    _ASSET_SERVICE_NOT_ACTIVATED_MARKER = "account has not activated the asset service"

    @classmethod
    def _save(cls, task: VideoTaskRecord, detail: Optional[dict] = None) -> None:
        task.updated_at = utc_now_iso()
        cls._tasks[task.internal_task_id] = task
        database.upsert_task(task, detail)

    @staticmethod
    async def _sync_real_terminal_best_effort(task: VideoTaskRecord) -> None:
        """Mirror background terminal states even when no browser is polling.

        The import stays local so the provider adapter does not participate in
        the Feishu/database module import graph.  SQLite is already committed by
        the caller; mirror or network failures must not alter the task outcome.
        """
        if task.execution_mode != "real" or task.status not in {
            TaskStatus.QA_PENDING,
            TaskStatus.FAILED,
        }:
            return
        try:
            from core.feishu_sync import FeishuBitableSync

            await asyncio.to_thread(FeishuBitableSync.sync_task, task.model_copy(deep=True))
        except Exception:
            pass

    @classmethod
    def get_task(cls, internal_task_id: str) -> Optional[VideoTaskRecord]:
        task = cls._tasks.get(internal_task_id) or database.get_task(internal_task_id)
        if task:
            cls._tasks[internal_task_id] = task
        return task

    @classmethod
    def list_tasks(cls, product_id: Optional[str] = None) -> list[VideoTaskRecord]:
        return database.list_tasks(product_id)

    @classmethod
    def resume_task_if_needed(cls, internal_task_id: str) -> None:
        task = cls.get_task(internal_task_id)
        if task and task.execution_mode == "real" and task.status in {
            TaskStatus.SUBMITTED,
            TaskStatus.PROCESSING,
        }:
            cls._schedule(internal_task_id)

    @staticmethod
    def _fingerprint(**values: object) -> str:
        encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _schedule(cls, internal_task_id: str, product_name: str = "测试商品") -> None:
        if internal_task_id in cls._running_tasks:
            return
        task = cls.get_task(internal_task_id)
        if not task:
            return
        cls._running_tasks.add(internal_task_id)
        if task.execution_mode == "mock":
            coroutine = cls._process_mock_task(internal_task_id, product_name)
        else:
            coroutine = cls._process_real_api_task(internal_task_id)
        asyncio.create_task(coroutine)

    @classmethod
    async def resume_pending_tasks(cls) -> int:
        """Resume polling persisted Ark tasks after a local service restart."""
        resumed = 0
        for task in database.list_tasks():
            if task.execution_mode != "real" or task.status not in {
                TaskStatus.SUBMITTED,
                TaskStatus.PROCESSING,
            }:
                continue
            if not task.provider_task_id:
                # Re-submitting an uncertain paid request can duplicate spend.
                task.status = TaskStatus.FAILED
                task.error_code = "RESUME_REQUIRES_REVIEW"
                task.error_message = "重启前未保存供应商任务 ID；为避免重复扣费，未自动重新提交"
                cls._save(task)
                await cls._sync_real_terminal_best_effort(task)
                continue
            cls._schedule(task.internal_task_id)
            resumed += 1
        return resumed

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
        idempotency_key: Optional[str] = None,
        revision_id: Optional[str] = None,
        attempt_no: Optional[int] = None,
        generation_kind: str = "initial",
        root_task_id: Optional[str] = None,
        virtual_actor: Optional[PublicVirtualActor] = None,
    ) -> VideoTaskRecord:
        provider = provider.strip().lower()
        execution_mode = "mock" if provider == "mock" else "real"

        if not revision_id:
            change_type = "initial"
            if parent_task_id:
                change_type = "repair" if generation_kind == "repair" else "manual_edit"
            elif generation_kind == "reroll":
                change_type = "initial"
            rev = database.create_or_get_prompt_revision(
                product_id=product_id,
                shot_id=shot_id,
                prompt_text=prompt,
                negative_prompt=negative_prompt,
                display_version=prompt_version,
                change_type=change_type,
            )
            revision_id = rev.revision_id
            prompt_version = rev.display_version
        else:
            rev = database.get_prompt_revision(revision_id)
            if rev:
                prompt_version = rev.display_version

        if not root_task_id and parent_task_id:
            parent = cls.get_task(parent_task_id)
            if parent:
                root_task_id = parent.root_task_id or parent.internal_task_id

        fingerprint = cls._fingerprint(
            idempotency_key=idempotency_key or "",
            product_id=product_id,
            shot_id=shot_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_url=image_url,
            provider=provider,
            model=model.strip(),
            prompt_version=prompt_version,
            duration=duration,
            aspect_ratio=aspect_ratio,
            parent_task_id=parent_task_id or "",
            variant_id=variant_id or "",
            revision_id=revision_id,
            generation_kind=generation_kind,
            virtual_actor=(virtual_actor.model_dump(mode="json") if virtual_actor else None),
        )
        cached = database.find_task_by_fingerprint(fingerprint)
        if cached:
            cls._tasks[cached.internal_task_id] = cached
            if cached.status in {TaskStatus.SUBMITTED, TaskStatus.PROCESSING}:
                cls._schedule(cached.internal_task_id, product_name)
            return cached

        if execution_mode == "real":
            if provider not in {"ark", "volcengine", "seedance", "jimeng"}:
                raise ValueError(f"暂不支持真实供应商: {provider}")
            if not ArkClient.configured():
                raise ArkAPIError("未配置 ARK_API_KEY")
            if not image_url:
                raise ValueError("真实图生视频任务必须提供首帧图片")
            if virtual_actor:
                if "seedance-2-0" not in model.lower():
                    raise ValueError("公共虚拟人当前仅允许用于 Seedance 2.0")
                ArkClient.validate_virtual_actor_reference(virtual_actor.asset_uri)

        if attempt_no is None or attempt_no <= 0:
            attempt_no = 0

        internal_task_id = f"TASK_{uuid.uuid4().hex[:10].upper()}"
        if not root_task_id:
            root_task_id = internal_task_id

        task = VideoTaskRecord(
            internal_task_id=internal_task_id,
            product_id=product_id,
            shot_id=shot_id,
            provider=provider,
            model=model.strip(),
            execution_mode=execution_mode,
            prompt_version=prompt_version,
            revision_id=revision_id,
            attempt_no=attempt_no,
            generation_kind=generation_kind,
            root_task_id=root_task_id,
            variant_id=variant_id,
            request_fingerprint=fingerprint,
            prompt_text=prompt,
            negative_prompt=negative_prompt,
            source_image=image_url,
            virtual_actor=virtual_actor.model_copy(deep=True) if virtual_actor else None,
            duration=duration,
            aspect_ratio=aspect_ratio,
            parent_task_id=parent_task_id,
            status=TaskStatus.CREATED,
        )

        if execution_mode == "real":
            reservation, stored = database.reserve_real_video_task(
                task, settings.MAX_REAL_VIDEO_TASKS_PER_DAY
            )
            if reservation == "quota":
                raise QuotaExceededError(
                    f"已达到每日真实视频任务上限 {settings.MAX_REAL_VIDEO_TASKS_PER_DAY}，停止新提交"
                )
            if stored is None:
                raise RuntimeError("真实视频任务预约未返回持久化记录")
            task = stored
            cls._tasks[task.internal_task_id] = task
            if reservation == "existing":
                if task.status in {TaskStatus.SUBMITTED, TaskStatus.PROCESSING}:
                    cls._schedule(task.internal_task_id, product_name)
                return task
            cls._schedule(task.internal_task_id, product_name)
            return task

        reservation, stored = database.reserve_mock_video_task(task)
        task = stored
        cls._tasks[task.internal_task_id] = task
        if reservation == "existing":
            if task.status in {TaskStatus.SUBMITTED, TaskStatus.PROCESSING}:
                cls._schedule(task.internal_task_id, product_name)
            return task
        task.status = TaskStatus.SUBMITTED
        cls._save(task)
        cls._schedule(task.internal_task_id, product_name)
        return task

    @classmethod
    async def _process_mock_task(cls, internal_task_id: str, product_name: str) -> None:
        task = cls.get_task(internal_task_id)
        if not task:
            cls._running_tasks.discard(internal_task_id)
            return
        started = time.monotonic()
        try:
            task.status = TaskStatus.PROCESSING
            task.provider_task_id = task.provider_task_id or f"mock_{uuid.uuid4().hex[:10]}"
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
            task.error_message = str(exc)[:1500]
            cls._save(task)
        finally:
            cls._running_tasks.discard(internal_task_id)

    @staticmethod
    def _provider_status(payload: dict) -> str:
        return str(payload.get("status") or payload.get("state") or "").lower()

    @staticmethod
    def _provider_video_url(payload: dict) -> str:
        content = payload.get("content")
        if isinstance(content, dict):
            return str(content.get("video_url") or content.get("url") or "")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and (item.get("video_url") or item.get("url")):
                    return str(item.get("video_url") or item.get("url"))
        result = payload.get("result")
        if isinstance(result, dict):
            return str(result.get("video_url") or result.get("url") or "")
        return str(payload.get("video_url") or payload.get("output_url") or "")

    @staticmethod
    def _is_transient_ark_error(exc: ArkAPIError) -> bool:
        return exc.status_code == 0 or exc.status_code in {408, 409, 425, 429} or exc.status_code >= 500

    @classmethod
    def _ark_terminal_error_code(cls, exc: Exception) -> Optional[str]:
        """Map actionable Ark failures without changing provider fallback policy."""
        if (
            isinstance(exc, ArkAPIError)
            and cls._ASSET_SERVICE_NOT_ACTIVATED_MARKER in str(exc).casefold()
        ):
            return "ARK_ASSET_SERVICE_NOT_ACTIVATED"
        return None

    @staticmethod
    def _validate_media(task: VideoTaskRecord, media: dict) -> None:
        duration = float(media.get("duration") or 0)
        tolerance = max(1.0, task.duration * 0.25)
        if not duration or abs(duration - task.duration) > tolerance:
            raise ArkAPIError(
                f"供应商视频时长异常: 期望 {task.duration}s，实际 {duration}s", status_code=422
            )
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        ratios = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}
        expected = ratios.get(task.aspect_ratio)
        if not width or not height or (expected and abs(width / height - expected) > 0.04):
            raise ArkAPIError(
                f"供应商视频画幅异常: 期望 {task.aspect_ratio}，实际 {width}x{height}",
                status_code=422,
            )

    @classmethod
    async def _process_real_api_task(cls, internal_task_id: str) -> None:
        task = cls.get_task(internal_task_id)
        if not task:
            cls._running_tasks.discard(internal_task_id)
            return
        started = time.monotonic()
        terminal_failure = False
        try:
            if not task.provider_task_id:
                created = await asyncio.to_thread(
                    ArkClient.create_video_task,
                    model=task.model,
                    prompt=f"{task.prompt_text}\n负向约束：{task.negative_prompt}",
                    image_reference=task.source_image,
                    virtual_actor_reference=(task.virtual_actor.asset_uri if task.virtual_actor else ""),
                    duration=task.duration,
                    aspect_ratio=task.aspect_ratio,
                )
                provider_task_id = created.get("id") or created.get("task_id")
                if not provider_task_id:
                    raise ArkAPIError("方舟提交响应缺少 id/task_id")
                task.provider_task_id = str(provider_task_id)
                task.status = TaskStatus.PROCESSING
                cls._save(task, {"provider_status": cls._provider_status(created)})

            interval = 3.0
            for _ in range(180):
                try:
                    result = await asyncio.to_thread(ArkClient.get_video_task, task.provider_task_id)
                except ArkAPIError as exc:
                    if not cls._is_transient_ark_error(exc):
                        raise
                    task.status = TaskStatus.PROCESSING
                    task.error_code = "ARK_POLL_RETRYING"
                    task.error_message = str(exc)[:1500]
                    cls._save(task, {"transient_poll_error": task.error_message})
                    await asyncio.sleep(interval)
                    interval = min(interval * 1.15, 10.0)
                    continue
                status = cls._provider_status(result)
                if status in {"succeeded", "success", "completed", "done"}:
                    remote_url = cls._provider_video_url(result)
                    if not remote_url:
                        raise ArkAPIError("方舟完成响应缺少 video_url")
                    try:
                        local_path, local_url = await asyncio.to_thread(
                            StorageManager.download_remote_video,
                            remote_url,
                            "seedance",
                            task.provider_task_id,
                        )
                    except Exception as exc:
                        task.status = TaskStatus.PROCESSING
                        task.error_code = "ARK_DOWNLOAD_RETRYING"
                        task.error_message = str(exc)[:1500]
                        cls._save(task, {"transient_download_error": task.error_message})
                        await asyncio.sleep(interval)
                        interval = min(interval * 1.15, 10.0)
                        continue
                    try:
                        if not settings.VIDEO_GENERATE_AUDIO:
                            media_before = await asyncio.to_thread(
                                StorageManager.inspect_video, local_path
                            )
                            if media_before.get("audio_codec"):
                                await asyncio.to_thread(StorageManager.remove_audio_track, local_path)
                        media = await asyncio.to_thread(StorageManager.inspect_video, local_path)
                        cls._validate_media(task, media)
                    except Exception as exc:
                        try:
                            await asyncio.to_thread(StorageManager.discard_output_file, local_path)
                        except Exception:
                            pass
                        raise ProviderMediaError(
                            f"方舟视频已下载但本地媒体校验失败，损坏归档已清理: {exc}",
                            status_code=422,
                            error_code="ARK_VIDEO_MEDIA_INVALID",
                        ) from exc
                    task.status = TaskStatus.COMPLETED
                    task.video_url = local_url
                    task.local_video_path = local_path
                    task.generation_time_seconds = round(time.monotonic() - started, 2)
                    task.output_width = media["width"]
                    task.output_height = media["height"]
                    task.output_fps = media["fps"]
                    task.output_duration_seconds = media["duration"]
                    task.output_audio_codec = media["audio_codec"] or None
                    task.estimated_cost = settings.calculate_cost(task.duration)["cost_cny"]
                    task.completed_at = utc_now_iso()
                    task.error_code = None
                    task.error_message = None
                    cls._save(task, {
                        "provider_status": status,
                        "provider_video_url": remote_url,
                        "media": media,
                    })
                    task.status = TaskStatus.QA_PENDING
                    cls._save(task)
                    await cls._sync_real_terminal_best_effort(task)
                    return
                if status in {"failed", "error", "cancelled", "canceled", "expired"}:
                    detail = result.get("error") or result.get("message") or status
                    terminal_failure = True
                    raise ProviderTerminalError(f"方舟视频任务失败: {detail}")
                task.status = TaskStatus.PROCESSING
                task.error_code = None
                task.error_message = None
                cls._save(task, {"provider_status": status or "queued"})
                await asyncio.sleep(interval)
                interval = min(interval * 1.15, 10.0)
            task.status = TaskStatus.PROCESSING
            task.error_code = "ARK_POLLING_PAUSED"
            task.error_message = "本轮轮询达到 25 分钟上限；读取任务状态时会继续查询同一供应商任务，不会重新提交"
            cls._save(task)
            return
        except Exception as exc:
            recoverable = bool(task.provider_task_id) and not terminal_failure and (
                not isinstance(exc, ArkAPIError) or cls._is_transient_ark_error(exc)
            )
            ark_terminal_error_code = cls._ark_terminal_error_code(exc)
            task.status = TaskStatus.PROCESSING if recoverable else TaskStatus.FAILED
            if recoverable:
                task.error_code = "ARK_PROCESSING_RETRYABLE"
            elif not task.provider_task_id and isinstance(exc, ArkAPIError) and exc.status_code == 0:
                task.error_code = "ARK_SUBMISSION_UNCERTAIN"
            elif ark_terminal_error_code:
                task.error_code = ark_terminal_error_code
            elif isinstance(exc, ProviderMediaError):
                task.error_code = exc.error_code or "ARK_VIDEO_MEDIA_INVALID"
            else:
                task.error_code = "ARK_VIDEO_GENERATION_FAILED"
            task.error_message = str(exc)[:1500]
            cls._save(task)
            if task.status == TaskStatus.FAILED:
                await cls._sync_real_terminal_best_effort(task)
        finally:
            cls._running_tasks.discard(internal_task_id)
