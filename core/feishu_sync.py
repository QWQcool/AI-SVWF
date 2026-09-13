"""
AI-SVWF 飞书多维表格 (Bitable) 资产同步中枢 (FeishuBitableSync)
严格对齐《AIGC内容资产中心.jpg》与《AIGC内容资产中心实现目标.txt》

核心能力：
1. 对应《00_管理表》四大板块：
   - 01_商品资料库 (档案/可信度/合规审查)
   - 视频创作任务库 (S01/S02/S03/提示词版本/视频URL)
   - 检查层 (QA打分/Failure Code/Repair记录)
   - 交接层 (15秒成品视频资产归档)
2. 双模驱动：支持真实飞书开放平台 REST API 双写，同时具备本地 JSON 镜像备份机制，离线/无网络也不会丢失任何资产。
"""

import json
import time
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List
from core.config import settings
from core.schemas import ProductAnalysis, VideoTaskRecord, QARecordInput, StitchResult


class FeishuBitableSync:
    LOCAL_MIRROR_FILE: Path = settings.OUTPUT_DIR / "bitable_local_mirror.json"
    _tenant_token: Optional[str] = None
    _token_expire_time: float = 0

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
    def _write_local_mirror(cls, data: Dict[str, List[Any]]):
        """写入本地镜像数据库"""
        with open(cls.LOCAL_MIRROR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def sync_product(cls, product: ProductAnalysis) -> Dict[str, Any]:
        """同步商品资料到《01_商品资料库》"""
        record = {
            "product_id": product.product_id,
            "product_name": product.product_name,
            "brand": product.brand,
            "category": product.category,
            "confirmed_selling_points": "\n".join(product.confirmed_information),
            "possible_selling_points": "\n".join(product.possible_information),
            "confidence_score": product.information_confidence,
            "risk_information": "\n".join(product.risk_information) if product.risk_information else "无风险",
            "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 1. 本地双写镜像
        mirror = cls._read_local_mirror()
        mirror["products"] = [p for p in mirror["products"] if p.get("product_id") != product.product_id]
        mirror["products"].append(record)
        cls._write_local_mirror(mirror)

        # 2. 若配置了飞书，调用 API 写入
        token = cls._get_tenant_access_token()
        remote_synced = False
        if token and settings.FEISHU_BITABLE_APP_TOKEN and settings.FEISHU_TABLE_PRODUCTS:
            try:
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}/tables/{settings.FEISHU_TABLE_PRODUCTS}/records"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                resp = requests.post(url, headers=headers, json={"fields": record}, timeout=5)
                remote_synced = resp.json().get("code") == 0
            except Exception:
                pass

        return {"local_synced": True, "remote_synced": remote_synced, "record": record}

    @classmethod
    def sync_task(cls, task: VideoTaskRecord) -> Dict[str, Any]:
        """同步视频生成任务到《视频创作任务库》"""
        record = {
            "internal_task_id": task.internal_task_id,
            "product_id": task.product_id,
            "shot_id": task.shot_id,
            "prompt_version": task.prompt_version,
            "model": f"{task.provider}:{task.model}",
            "status": task.status.value,
            "video_url": task.video_url or "",
            "generation_time_s": task.generation_time_seconds or 0,
            "estimated_cost": task.estimated_cost or 0,
            "updated_at": task.updated_at,
        }

        mirror = cls._read_local_mirror()
        mirror["tasks"] = [t for t in mirror["tasks"] if t.get("internal_task_id") != task.internal_task_id]
        mirror["tasks"].append(record)
        cls._write_local_mirror(mirror)

        token = cls._get_tenant_access_token()
        remote_synced = False
        if token and settings.FEISHU_BITABLE_APP_TOKEN and settings.FEISHU_TABLE_TASKS:
            try:
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}/tables/{settings.FEISHU_TABLE_TASKS}/records"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                resp = requests.post(url, headers=headers, json={"fields": record}, timeout=5)
                remote_synced = resp.json().get("code") == 0
            except Exception:
                pass

        return {"local_synced": True, "remote_synced": remote_synced, "record": record}

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
            "repair_actions": ", ".join(repair_actions) if repair_actions else "NONE",
            "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        mirror = cls._read_local_mirror()
        mirror["qa_records"].append(record)
        cls._write_local_mirror(mirror)

        token = cls._get_tenant_access_token()
        remote_synced = False
        if token and settings.FEISHU_BITABLE_APP_TOKEN and settings.FEISHU_TABLE_QA:
            try:
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}/tables/{settings.FEISHU_TABLE_QA}/records"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                resp = requests.post(url, headers=headers, json={"fields": record}, timeout=5)
                remote_synced = resp.json().get("code") == 0
            except Exception:
                pass

        return {"local_synced": True, "remote_synced": remote_synced, "record": record}

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

        mirror = cls._read_local_mirror()
        mirror["deliveries"].append(record)
        cls._write_local_mirror(mirror)

        token = cls._get_tenant_access_token()
        remote_synced = False
        if token and settings.FEISHU_BITABLE_APP_TOKEN and settings.FEISHU_TABLE_DELIVERY:
            try:
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{settings.FEISHU_BITABLE_APP_TOKEN}/tables/{settings.FEISHU_TABLE_DELIVERY}/records"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                resp = requests.post(url, headers=headers, json={"fields": record}, timeout=5)
                remote_synced = resp.json().get("code") == 0
            except Exception:
                pass

        return {"local_synced": True, "remote_synced": remote_synced, "record": record}

    @classmethod
    def get_mirror_summary(cls) -> Dict[str, Any]:
        """获取本地资产同步镜像汇总"""
        data = cls._read_local_mirror()
        return {
            "products_count": len(data["products"]),
            "tasks_count": len(data["tasks"]),
            "qa_records_count": len(data["qa_records"]),
            "deliveries_count": len(data["deliveries"]),
            "is_feishu_connected": bool(cls._get_tenant_access_token()),
            "last_synced": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
