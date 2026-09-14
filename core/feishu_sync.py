"""
AI-SVWF 飞书多维表格 (Bitable) 资产同步中枢 (FeishuBitableSync)
严格对齐《AIGC内容资产中心.jpg》与《AIGC内容资产中心实现目标.txt》

核心能力：
1. 对应《00_管理表》四大板块：
   - 01_商品资料库 (档案/证据充分度/合规审查)
   - 视频创作任务库 (S01/S02/S03/提示词版本/视频URL)
   - 检查层 (QA打分/Failure Code/Repair记录)
   - 交接层 (15秒成品视频资产归档)
2. 双模驱动：支持真实飞书开放平台 REST API 双写，同时具备本地 JSON 镜像备份机制，离线/无网络也不会丢失任何资产。
"""

import json
import os
import time
import requests
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from uuid import uuid4
from core.config import settings
from core.schemas import ProductAnalysis, VideoTaskRecord, QARecordInput, StitchResult
from core.database import database


class FeishuBitableSync:
    LOCAL_MIRROR_FILE: Path = settings.OUTPUT_DIR / "bitable_local_mirror.json"
    _tenant_token: Optional[str] = None
    _token_expire_time: float = 0
    _mirror_lock = threading.RLock()

    @staticmethod
    def _remote_enabled() -> bool:
        return settings.FEISHU_SYNC_MODE in {"dual", "cloud"}

    @staticmethod
    def configured() -> bool:
        """All credentials and four destination table IDs are required."""
        return all((
            settings.FEISHU_APP_ID,
            settings.FEISHU_APP_SECRET,
            settings.FEISHU_BITABLE_APP_TOKEN,
            settings.FEISHU_TABLE_PRODUCTS,
            settings.FEISHU_TABLE_TASKS,
            settings.FEISHU_TABLE_QA,
            settings.FEISHU_TABLE_DELIVERY,
        ))

    @classmethod
    def _get_tenant_access_token(cls) -> Optional[str]:
        """获取飞书自建应用 tenant_access_token"""
        if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
            return None

        # 检查缓存是否有效
        if cls._tenant_token and time.time() < cls._token_expire_time:
            return cls._tenant_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": settings.FEISHU_APP_ID,
            "app_secret": settings.FEISHU_APP_SECRET,
        }
        try:
            resp = requests.post(url, json=payload, timeout=8)
            data = resp.json()
            if data.get("code") == 0:
                cls._tenant_token = data.get("tenant_access_token")
                cls._token_expire_time = time.time() + data.get("expire", 7100) - 100
                return cls._tenant_token
        except Exception:
            pass
        return None

    @classmethod
    def _read_local_mirror(cls) -> Dict[str, List[Any]]:
        """读取本地镜像数据库"""
        with cls._mirror_lock:
            if not cls.LOCAL_MIRROR_FILE.exists():
                return {
                    "products": [],
                    "tasks": [],
                    "qa_records": [],
                    "deliveries": [],
                }
            try:
                with open(cls.LOCAL_MIRROR_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {"products": [], "tasks": [], "qa_records": [], "deliveries": []}

    @classmethod
    def _write_local_mirror(cls, data: Dict[str, List[Any]]) -> bool:
        """尽力写入 JSON 镜像；SQLite 才是本地权威数据源。

        Windows 不允许替换一个正被其他进程短暂读取的文件。测试进程、
        Web 服务和桌面预览可能并行运行，因此每次写入使用唯一临时文件，
        并对原子替换做有限重试。镜像失败不能反向中断视频主流程。
        """
        with cls._mirror_lock:
            temporary = cls.LOCAL_MIRROR_FILE.with_name(
                f"{cls.LOCAL_MIRROR_FILE.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
            )
            try:
                with open(temporary, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                for attempt in range(20):
                    try:
                        os.replace(temporary, cls.LOCAL_MIRROR_FILE)
                        return True
                    except PermissionError:
                        if attempt == 19:
                            return False
                        time.sleep(0.025)
                return False
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _sync_remote(cls, entity_type: str, entity_id: str, table_id: str, record: Dict[str, Any]) -> bool:
        """Create once, then update the same Feishu record on later task states."""
        if not cls._remote_enabled() or not table_id or not settings.FEISHU_BITABLE_APP_TOKEN:
            return False
        token = cls._get_tenant_access_token()
        if not token:
            return False
        outbox = next(
            (item for item in database.pending_sync(500) if item["entity_type"] == entity_type and item["entity_id"] == entity_id),
            None,
        )
        remote_record_id = outbox.get("remote_record_id") if outbox else None
        base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}/tables/{table_id}/records"
        url = f"{base_url}/{remote_record_id}" if remote_record_id else base_url
        try:
            response = requests.put(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"fields": record}, timeout=8) if remote_record_id else requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"fields": record},
                timeout=8,
            )
            data = response.json()
            ok = response.ok and data.get("code") == 0
            created_id = remote_record_id or data.get("data", {}).get("record", {}).get("record_id")
            database.mark_sync(entity_type, entity_id, ok, "" if ok else str(data), created_id)
            return ok
        except Exception as exc:
            database.mark_sync(entity_type, entity_id, False, str(exc))
            return False

    @classmethod
    def sync_product(cls, product: ProductAnalysis) -> Dict[str, Any]:
        """同步商品资料到《01_商品资料库》"""
        claims_summary = "\n".join([
            f"[{c.claim_id}] ({','.join([p.source_type for p in c.provenance])}) {c.text} -> {c.classification}"
            for c in product.claims
        ])
        record = {
            "product_id": product.product_id,
            "product_name": product.product_name,
            "brand": product.brand,
            "category": product.category,
            "confirmed_selling_points": "\n".join(product.confirmed_information),
            "possible_selling_points": "\n".join(product.possible_information),
            "confidence_score": product.information_confidence,
            "evidence_sufficiency": product.evidence_sufficiency,
            "evidence_status": product.evidence_status,
            "claims_summary": claims_summary,
            "risk_information": "\n".join(product.risk_information) if product.risk_information else "无风险",
            "source_images": "\n".join(product.source_images),
            "virtual_actor_group_id": product.virtual_actor_group_id or "",
            "virtual_actor_asset_uri": product.virtual_actor.asset_uri if product.virtual_actor else "",
            "virtual_actor_role": product.virtual_actor.role if product.virtual_actor else "",
            "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if cls._remote_enabled():
            database.enqueue_sync("product", product.product_id, record)

        # 1. 本地双写镜像
        with cls._mirror_lock:
            mirror = cls._read_local_mirror()
            mirror["products"] = [p for p in mirror["products"] if p.get("product_id") != product.product_id]
            mirror["products"].append(record)
            local_synced = cls._write_local_mirror(mirror)

        remote_synced = cls._sync_remote("product", product.product_id, settings.FEISHU_TABLE_PRODUCTS, record)

        return {"local_synced": local_synced, "remote_synced": remote_synced, "record": record}

    @classmethod
    def sync_task(cls, task: VideoTaskRecord) -> Dict[str, Any]:
        """同步视频生成任务到《视频创作任务库》"""
        record = {
            "internal_task_id": task.internal_task_id,
            "product_id": task.product_id,
            "shot_id": task.shot_id,
            "prompt_version": task.prompt_version,
            "revision_id": task.revision_id or "",
            "attempt_no": task.attempt_no,
            "generation_kind": task.generation_kind,
            "root_task_id": task.root_task_id or "",
            "provider": task.provider,
            "model": task.model,
            "execution_mode": task.execution_mode,
            "provider_task_id": task.provider_task_id,
            "parent_task_id": task.parent_task_id or "",
            "variant_id": task.variant_id or "",
            "prompt_text": task.prompt_text,
            "negative_prompt": task.negative_prompt,
            "source_image": task.source_image,
            "virtual_actor_group_id": task.virtual_actor.group_id if task.virtual_actor else "",
            "virtual_actor_asset_uri": task.virtual_actor.asset_uri if task.virtual_actor else "",
            "virtual_actor_role": task.virtual_actor.role if task.virtual_actor else "",
            "duration": task.duration,
            "aspect_ratio": task.aspect_ratio,
            "status": task.status.value,
            "video_url": task.video_url or "",
            "generation_time_s": task.generation_time_seconds or 0,
            "estimated_cost": task.estimated_cost or 0,
            "qa_score": task.qa_score if task.qa_score is not None else "",
            "qa_status": task.qa_status.value if task.qa_status else "",
            "failure_codes": ", ".join(task.failure_codes),
            "failure_notes": "\n".join(task.failure_notes),
            "failure_occurrences_json": json.dumps(
                [item.model_dump(mode="json") for item in task.failure_occurrences],
                ensure_ascii=False,
            ),
            "repair_actions": ", ".join(task.repair_actions),
            "error_code": task.error_code or "",
            "error_message": task.error_message or "",
            "updated_at": task.updated_at,
        }
        with cls._mirror_lock:
            mirror = cls._read_local_mirror()
            previous = next((item for item in mirror["tasks"] if item.get("internal_task_id") == task.internal_task_id), None)
            if previous == record:
                return {"local_synced": True, "remote_synced": False, "unchanged": True, "record": record}
            mirror["tasks"] = [t for t in mirror["tasks"] if t.get("internal_task_id") != task.internal_task_id]
            mirror["tasks"].append(record)
            local_synced = cls._write_local_mirror(mirror)

        if cls._remote_enabled():
            database.enqueue_sync("task", task.internal_task_id, record)

        remote_synced = cls._sync_remote("task", task.internal_task_id, settings.FEISHU_TABLE_TASKS, record)

        return {"local_synced": local_synced, "remote_synced": remote_synced, "record": record}

    @classmethod
    def sync_qa_record(cls, qa_input: QARecordInput, total_score: int, status: str, repair_actions: List[str]) -> Dict[str, Any]:
        """同步 QA 打分与 Failure Code 到《检查层 (QA质检表)》"""
        record = {
            "internal_task_id": qa_input.internal_task_id,
            "shot_id": qa_input.shot_id,
            "qa_score": total_score,
            "qa_status": status,
            "failure_codes": ", ".join(qa_input.failure_codes) if qa_input.failure_codes else "NONE",
            "failure_notes": "\n".join(qa_input.failure_notes),
            "failure_occurrences_json": json.dumps(
                [item.model_dump(mode="json") for item in qa_input.failure_occurrences],
                ensure_ascii=False,
            ),
            "repair_actions": ", ".join(repair_actions) if repair_actions else "NONE",
            "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        qa_entity_id = f"{qa_input.internal_task_id}:{int(time.time() * 1000)}"
        if cls._remote_enabled():
            database.enqueue_sync("qa", qa_entity_id, record)

        with cls._mirror_lock:
            mirror = cls._read_local_mirror()
            mirror["qa_records"].append(record)
            local_synced = cls._write_local_mirror(mirror)

        remote_synced = cls._sync_remote("qa", qa_entity_id, settings.FEISHU_TABLE_QA, record)

        return {"local_synced": local_synced, "remote_synced": remote_synced, "record": record}

    @classmethod
    def sync_delivery(cls, stitch_result: StitchResult) -> Dict[str, Any]:
        """同步最终 15 秒成片交付物到《交接层》"""
        record = {
            "product_id": stitch_result.product_id,
            "final_15s_video_url": stitch_result.final_video_url,
            "total_duration": stitch_result.total_duration,
            "shots_included": ", ".join(stitch_result.shots),
            "stitched_at": stitch_result.stitched_at,
        }
        delivery_id = f"{stitch_result.product_id}:{stitch_result.stitched_at}"
        if cls._remote_enabled():
            database.enqueue_sync("delivery", delivery_id, record)

        with cls._mirror_lock:
            mirror = cls._read_local_mirror()
            mirror["deliveries"].append(record)
            local_synced = cls._write_local_mirror(mirror)

        remote_synced = cls._sync_remote("delivery", delivery_id, settings.FEISHU_TABLE_DELIVERY, record)

        return {"local_synced": local_synced, "remote_synced": remote_synced, "record": record}

    @classmethod
    def get_mirror_summary(cls) -> Dict[str, Any]:
        """获取本地资产同步镜像汇总"""
        data = cls._read_local_mirror()
        sqlite_stats = database.stats()
        return {
            "products_count": len(data["products"]),
            "tasks_count": len(data["tasks"]),
            "qa_records_count": len(data["qa_records"]),
            "deliveries_count": len(data["deliveries"]),
            "is_feishu_connected": cls.configured() and bool(cls._get_tenant_access_token()),
            "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sqlite": sqlite_stats["counts"],
            "pending_feishu_sync": sqlite_stats["pending_feishu_sync"],
        }

    @classmethod
    def retry_pending(cls, limit: int = 100) -> Dict[str, Any]:
        """Retry SQLite outbox records after Feishu credentials/network recover."""
        pending = database.pending_sync(limit)
        if not cls._remote_enabled():
            return {"attempted": 0, "synced": 0, "remaining": len(pending), "reason": "remote sync disabled"}
        if not cls._get_tenant_access_token() or not settings.FEISHU_BITABLE_APP_TOKEN:
            return {"attempted": 0, "synced": 0, "remaining": len(pending), "reason": "Feishu credentials incomplete"}
        table_map = {
            "product": settings.FEISHU_TABLE_PRODUCTS,
            "task": settings.FEISHU_TABLE_TASKS,
            "qa": settings.FEISHU_TABLE_QA,
            "delivery": settings.FEISHU_TABLE_DELIVERY,
        }
        synced = 0
        attempted = 0
        for item in pending:
            table_id = table_map.get(item["entity_type"], "")
            if not table_id:
                continue
            attempted += 1
            ok = cls._sync_remote(item["entity_type"], item["entity_id"], table_id, item["payload"])
            synced += int(ok)
        remaining = database.stats()["pending_feishu_sync"]
        return {"attempted": attempted, "synced": synced, "remaining": remaining}
