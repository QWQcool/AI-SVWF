"""SQLite persistence for workflow facts and Feishu sync outbox.

SQLite is the local transactional source of truth. Feishu remains the
collaboration mirror; failed remote writes stay in ``sync_outbox`` for retry.
"""

import json
import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings
from core.schemas import (
    AssetRecord,
    ImageGenerationRecord,
    ProductAnalysis,
    PromptSchemaV1,
    PromptVariant,
    StitchResult,
    TaskStatus,
    VideoTaskRecord,
    utc_now_iso,
)


class WorkflowDatabase:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or settings.DATABASE_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @staticmethod
    def _json(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            confidence REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vision_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            model TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL UNIQUE,
            asset_ids_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'COMPLETED',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prompt_schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(product_id, prompt_version)
        );
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(product_id, prompt_version, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS prompt_variants (
            variant_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            shot_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(product_id, shot_id, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS video_tasks (
            internal_task_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            shot_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL,
            qa_score INTEGER,
            qa_status TEXT,
            parent_task_id TEXT,
            execution_mode TEXT NOT NULL DEFAULT 'mock',
            request_fingerprint TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_video_tasks_product ON video_tasks(product_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_video_tasks_matrix ON video_tasks(product_id, shot_id, prompt_version, model);
        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            internal_task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qa_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            internal_task_id TEXT NOT NULL,
            shot_id TEXT NOT NULL,
            qa_score INTEGER NOT NULL,
            qa_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS image_generations (
            image_task_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            shot_id TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            remote_record_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_type, entity_id)
        );
        """
        with self._lock, self._connect() as conn:
            conn.executescript(schema)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_outbox)").fetchall()}
            if "remote_record_id" not in columns:
                conn.execute("ALTER TABLE sync_outbox ADD COLUMN remote_record_id TEXT")
            task_columns = {row[1] for row in conn.execute("PRAGMA table_info(video_tasks)").fetchall()}
            if "execution_mode" not in task_columns:
                conn.execute("ALTER TABLE video_tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'mock'")
            if "request_fingerprint" not in task_columns:
                conn.execute("ALTER TABLE video_tasks ADD COLUMN request_fingerprint TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_video_tasks_fingerprint "
                "ON video_tasks(request_fingerprint) WHERE request_fingerprint IS NOT NULL AND request_fingerprint != ''"
            )
            vision_columns = {row[1] for row in conn.execute("PRAGMA table_info(vision_analyses)").fetchall()}
            if "request_fingerprint" not in vision_columns:
                conn.execute("ALTER TABLE vision_analyses ADD COLUMN request_fingerprint TEXT")
            if "status" not in vision_columns:
                conn.execute("ALTER TABLE vision_analyses ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_vision_fingerprint "
                "ON vision_analyses(request_fingerprint) WHERE request_fingerprint IS NOT NULL"
            )

    def save_asset(self, asset: AssetRecord) -> AssetRecord:
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT payload_json FROM assets WHERE sha256=?", (asset.sha256,)).fetchone()
            if existing:
                return AssetRecord.model_validate_json(existing["payload_json"])
            conn.execute(
                "INSERT INTO assets(asset_id,sha256,payload_json,created_at) VALUES(?,?,?,?)",
                (asset.asset_id, asset.sha256, self._json(asset), asset.created_at),
            )
        return asset

    def get_asset(self, asset_id: str) -> Optional[AssetRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return AssetRecord.model_validate_json(row["payload_json"]) if row else None

    def update_asset(self, asset: AssetRecord) -> None:
        """Update sanitized display metadata without changing content identity or storage."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE assets SET payload_json=? WHERE asset_id=? AND sha256=?",
                (self._json(asset), asset.asset_id, asset.sha256),
            )

    def get_asset_by_sha256(self, sha256: str) -> Optional[AssetRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM assets WHERE sha256=?", (sha256,)).fetchone()
        return AssetRecord.model_validate_json(row["payload_json"]) if row else None

    def get_assets(self, asset_ids: List[str]) -> List[AssetRecord]:
        assets = [self.get_asset(asset_id) for asset_id in asset_ids]
        return [asset for asset in assets if asset is not None]

    def save_vision_analysis(
        self, product_id: str, model: str, fingerprint: str, asset_ids: List[str], payload: Dict[str, Any]
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO vision_analyses(
                   product_id,model,request_fingerprint,asset_ids_json,status,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(request_fingerprint) DO UPDATE SET product_id=excluded.product_id,
                   model=excluded.model,asset_ids_json=excluded.asset_ids_json,status='COMPLETED',
                   payload_json=excluded.payload_json""",
                (product_id, model, fingerprint, self._json(asset_ids), "COMPLETED",
                 self._json(payload), utc_now_iso()),
            )

    def reserve_vision_analysis(
        self,
        product_id: str,
        model: str,
        fingerprint: str,
        asset_ids: List[str],
        daily_limit: int,
    ) -> Dict[str, Any]:
        """Atomically reserve one billed vision attempt and enforce its daily cap."""
        today = utc_now_iso()[:10]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT product_id,status,payload_json FROM vision_analyses WHERE request_fingerprint=?",
                (fingerprint,),
            ).fetchone()
            if existing:
                return {"result": "existing", **dict(existing)}
            count = conn.execute(
                "SELECT COUNT(*) FROM vision_analyses WHERE substr(created_at,1,10)=?", (today,)
            ).fetchone()[0]
            if count >= daily_limit:
                return {"result": "quota", "count": count}
            conn.execute(
                """INSERT INTO vision_analyses(
                   product_id,model,request_fingerprint,asset_ids_json,status,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (product_id, model, fingerprint, self._json(asset_ids), "SUBMITTED",
                 self._json({"status": "SUBMITTED"}), utc_now_iso()),
            )
        return {"result": "reserved", "product_id": product_id, "status": "SUBMITTED"}

    def fail_vision_analysis(
        self,
        fingerprint: str,
        message: str,
        *,
        error_code: str = "ARK_VISION_ANALYSIS_FAILED",
        status: str = "FAILED",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE vision_analyses SET status=?,payload_json=? WHERE request_fingerprint=?",
                (
                    status,
                    self._json({
                        "status": status,
                        "error_code": error_code,
                        "error_message": message[:1000],
                    }),
                    fingerprint,
                ),
            )

    def mark_pending_vision_for_review(self) -> int:
        """Quarantine vision calls whose billed result became unknown after a crash.

        Ark vision analysis is a synchronous paid request.  If the process exits
        after reserving the idempotency key but before persisting the response,
        replaying that request automatically could charge the account twice.
        Keep the reservation and make the retry an explicit, new user attempt.
        """
        changed = 0
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id,payload_json FROM vision_analyses WHERE status='SUBMITTED'"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                payload.update({
                    "status": "RESUME_REQUIRES_REVIEW",
                    "error_code": "RESUME_REQUIRES_REVIEW",
                    "error_message": "服务重启前识图调用结果未知；为避免重复扣费，未自动重放",
                    "review_marked_at": utc_now_iso(),
                })
                conn.execute(
                    "UPDATE vision_analyses SET status=?,payload_json=? WHERE id=?",
                    ("RESUME_REQUIRES_REVIEW", self._json(payload), row["id"]),
                )
                changed += 1
        return changed

    def find_vision_product(self, fingerprint: str) -> Optional[ProductAnalysis]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT product_id FROM vision_analyses WHERE request_fingerprint=? AND status='COMPLETED'",
                (fingerprint,),
            ).fetchone()
        return self.get_product(row["product_id"]) if row else None

    def upsert_product(self, product: ProductAnalysis) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO products(product_id, product_name, confidence, payload_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET product_name=excluded.product_name,
                   confidence=excluded.confidence, payload_json=excluded.payload_json,
                   updated_at=excluded.updated_at""",
                (product.product_id, product.product_name, product.information_confidence,
                 self._json(product), product.created_at, product.updated_at),
            )

    def get_product(self, product_id: str) -> Optional[ProductAnalysis]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM products WHERE product_id=?", (product_id,)).fetchone()
        return ProductAnalysis.model_validate_json(row["payload_json"]) if row else None

    def save_prompt_schema(self, product_id: str, version: str, schema: PromptSchemaV1) -> None:
        now = utc_now_iso()
        payload = self._json(schema)
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO prompt_versions(
                   product_id,prompt_version,fingerprint,payload_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (product_id, version, fingerprint, payload, now),
            )

    def save_variant(self, variant: PromptVariant) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO prompt_variants(
                   variant_id,product_id,shot_id,prompt_version,fingerprint,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (variant.variant_id, variant.product_id, variant.shot_id, variant.prompt_version,
                 variant.fingerprint, self._json(variant), variant.created_at),
            )
        return cursor.rowcount == 1

    def list_variants(self, product_id: str, shot_id: Optional[str] = None) -> List[PromptVariant]:
        sql = "SELECT payload_json FROM prompt_variants WHERE product_id=?"
        params: List[Any] = [product_id]
        if shot_id:
            sql += " AND shot_id=?"
            params.append(shot_id)
        sql += " ORDER BY created_at, variant_id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [PromptVariant.model_validate_json(row["payload_json"]) for row in rows]

    def upsert_task(self, task: VideoTaskRecord, event_detail: Optional[Dict[str, Any]] = None) -> None:
        previous_status = None
        with self._lock, self._connect() as conn:
            previous = conn.execute(
                "SELECT status FROM video_tasks WHERE internal_task_id=?", (task.internal_task_id,)
            ).fetchone()
            previous_status = previous["status"] if previous else None
            conn.execute(
                """INSERT INTO video_tasks(
                   internal_task_id,product_id,shot_id,provider,model,prompt_version,status,
                   qa_score,qa_status,parent_task_id,execution_mode,request_fingerprint,
                   payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(internal_task_id) DO UPDATE SET status=excluded.status,
                   qa_score=excluded.qa_score, qa_status=excluded.qa_status,
                   payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (task.internal_task_id, task.product_id, task.shot_id, task.provider, task.model,
                 task.prompt_version, task.status.value, task.qa_score,
                 task.qa_status.value if task.qa_status else None, task.parent_task_id,
                 task.execution_mode, task.request_fingerprint or None,
                 self._json(task), task.created_at, task.updated_at),
            )
            if previous_status != task.status.value:
                conn.execute(
                    "INSERT INTO task_events(internal_task_id,status,detail_json,created_at) VALUES(?,?,?,?)",
                    (task.internal_task_id, task.status.value, self._json(event_detail or {}), utc_now_iso()),
                )

    def reserve_real_video_task(
        self, task: VideoTaskRecord, daily_limit: int
    ) -> tuple[str, Optional[VideoTaskRecord]]:
        """Atomically enforce real-video idempotency/quota and persist the task.

        A Round1 batch can submit multiple HTTP requests concurrently.  The
        immediate transaction ensures separate threads or database connections
        cannot all observe the same pre-insert count and exceed the paid cap.
        """
        if task.execution_mode != "real":
            raise ValueError("reserve_real_video_task only accepts real tasks")
        if not task.request_fingerprint:
            raise ValueError("real tasks require request_fingerprint")

        today = utc_now_iso()[:10]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_json FROM video_tasks WHERE request_fingerprint=?",
                (task.request_fingerprint,),
            ).fetchone()
            if existing:
                return "existing", VideoTaskRecord.model_validate_json(existing["payload_json"])
            count = conn.execute(
                """SELECT COUNT(*) FROM video_tasks
                   WHERE execution_mode='real' AND substr(created_at,1,10)=?""",
                (today,),
            ).fetchone()[0]
            if count >= daily_limit:
                return "quota", None

            reserved = task.model_copy(deep=True)
            reserved.status = TaskStatus.SUBMITTED
            reserved.updated_at = utc_now_iso()
            conn.execute(
                """INSERT INTO video_tasks(
                   internal_task_id,product_id,shot_id,provider,model,prompt_version,status,
                   qa_score,qa_status,parent_task_id,execution_mode,request_fingerprint,
                   payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (reserved.internal_task_id, reserved.product_id, reserved.shot_id,
                 reserved.provider, reserved.model, reserved.prompt_version,
                 reserved.status.value, reserved.qa_score,
                 reserved.qa_status.value if reserved.qa_status else None,
                 reserved.parent_task_id, reserved.execution_mode,
                 reserved.request_fingerprint, self._json(reserved),
                 reserved.created_at, reserved.updated_at),
            )
            event_time = utc_now_iso()
            conn.executemany(
                "INSERT INTO task_events(internal_task_id,status,detail_json,created_at) VALUES(?,?,?,?)",
                [
                    (reserved.internal_task_id, TaskStatus.CREATED.value, self._json({}), event_time),
                    (reserved.internal_task_id, TaskStatus.SUBMITTED.value, self._json({}), event_time),
                ],
            )
        return "reserved", reserved

    def get_task(self, internal_task_id: str) -> Optional[VideoTaskRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM video_tasks WHERE internal_task_id=?", (internal_task_id,)
            ).fetchone()
        return VideoTaskRecord.model_validate_json(row["payload_json"]) if row else None

    def find_task_by_fingerprint(self, fingerprint: str) -> Optional[VideoTaskRecord]:
        if not fingerprint:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM video_tasks WHERE request_fingerprint=?", (fingerprint,)
            ).fetchone()
        return VideoTaskRecord.model_validate_json(row["payload_json"]) if row else None

    def list_tasks(self, product_id: Optional[str] = None) -> List[VideoTaskRecord]:
        sql = "SELECT payload_json FROM video_tasks"
        params: List[Any] = []
        if product_id:
            sql += " WHERE product_id=?"
            params.append(product_id)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [VideoTaskRecord.model_validate_json(row["payload_json"]) for row in rows]

    def task_events(self, internal_task_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT status,detail_json,created_at FROM task_events
                   WHERE internal_task_id=? ORDER BY id""",
                (internal_task_id,),
            ).fetchall()
        return [
            {"status": row["status"], "detail": json.loads(row["detail_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def save_qa(self, task: VideoTaskRecord, qa_payload: Dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO qa_records(internal_task_id,shot_id,qa_score,qa_status,payload_json,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (task.internal_task_id, task.shot_id, task.qa_score or 0,
                 task.qa_status.value if task.qa_status else "FAIL", self._json(qa_payload), utc_now_iso()),
            )

    def save_delivery(self, result: StitchResult) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO deliveries(product_id,payload_json,created_at) VALUES(?,?,?)",
                (result.product_id, self._json(result), result.stitched_at),
            )

    def upsert_image_generation(self, record: ImageGenerationRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO image_generations(
                   image_task_id,product_id,shot_id,model,status,request_fingerprint,
                   payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(image_task_id) DO UPDATE SET status=excluded.status,
                   payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (
                    record.image_task_id,
                    record.product_id,
                    record.shot_id,
                    record.model,
                    record.status,
                    record.request_fingerprint,
                    self._json(record),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def reserve_image_generation(
        self, record: ImageGenerationRecord, daily_limit: int
    ) -> tuple[str, Optional[ImageGenerationRecord]]:
        """Atomically combine image idempotency lookup, quota check and reservation."""
        today = utc_now_iso()[:10]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_json FROM image_generations WHERE request_fingerprint=?",
                (record.request_fingerprint,),
            ).fetchone()
            if existing:
                return "existing", ImageGenerationRecord.model_validate_json(existing["payload_json"])
            count = conn.execute(
                "SELECT COUNT(*) FROM image_generations WHERE substr(created_at,1,10)=?", (today,)
            ).fetchone()[0]
            if count >= daily_limit:
                return "quota", None
            conn.execute(
                """INSERT INTO image_generations(
                   image_task_id,product_id,shot_id,model,status,request_fingerprint,
                   payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (record.image_task_id, record.product_id, record.shot_id, record.model,
                 record.status, record.request_fingerprint, self._json(record),
                 record.created_at, record.updated_at),
            )
        return "reserved", record

    def mark_pending_images_for_review(self) -> int:
        """A synchronous Seedream call cannot be safely replayed after process loss."""
        changed = 0
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT image_task_id,payload_json FROM image_generations WHERE status='SUBMITTED'"
            ).fetchall()
            for row in rows:
                record = ImageGenerationRecord.model_validate_json(row["payload_json"])
                # FAILED is part of the public schema; the error code preserves
                # the more specific recovery state without creating unreadable
                # payloads after restart.
                record.status = "FAILED"
                record.error_code = "RESUME_REQUIRES_REVIEW"
                record.error_message = "服务重启前首帧调用结果未知；为避免重复扣费，未自动重放"
                record.updated_at = utc_now_iso()
                conn.execute(
                    "UPDATE image_generations SET status=?,payload_json=?,updated_at=? WHERE image_task_id=?",
                    (record.status, self._json(record), record.updated_at, record.image_task_id),
                )
                changed += 1
        return changed

    def find_image_generation(self, fingerprint: str) -> Optional[ImageGenerationRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM image_generations WHERE request_fingerprint=?", (fingerprint,)
            ).fetchone()
        return ImageGenerationRecord.model_validate_json(row["payload_json"]) if row else None

    def get_image_generation(self, image_task_id: str) -> Optional[ImageGenerationRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM image_generations WHERE image_task_id=?", (image_task_id,)
            ).fetchone()
        return ImageGenerationRecord.model_validate_json(row["payload_json"]) if row else None

    def list_image_generations(self, product_id: Optional[str] = None) -> List[ImageGenerationRecord]:
        sql = "SELECT payload_json FROM image_generations"
        params: tuple[Any, ...] = ()
        if product_id:
            sql += " WHERE product_id=?"
            params = (product_id,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ImageGenerationRecord.model_validate_json(row["payload_json"]) for row in rows]

    def daily_real_task_counts(self, utc_date: str) -> Dict[str, int]:
        with self._connect() as conn:
            video_count = conn.execute(
                """SELECT COUNT(*) FROM video_tasks
                   WHERE execution_mode='real' AND substr(created_at,1,10)=?""",
                (utc_date,),
            ).fetchone()[0]
            image_count = conn.execute(
                "SELECT COUNT(*) FROM image_generations WHERE substr(created_at,1,10)=?",
                (utc_date,),
            ).fetchone()[0]
            vision_count = conn.execute(
                "SELECT COUNT(*) FROM vision_analyses WHERE substr(created_at,1,10)=?",
                (utc_date,),
            ).fetchone()[0]
        return {"video": int(video_count), "image": int(image_count), "vision": int(vision_count)}

    def enqueue_sync(self, entity_type: str, entity_id: str, payload: Dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO sync_outbox(entity_type,entity_id,payload_json,status,created_at,updated_at)
                   VALUES(?,?,?,'PENDING',?,?) ON CONFLICT(entity_type,entity_id)
                   DO UPDATE SET payload_json=excluded.payload_json,status='PENDING',updated_at=excluded.updated_at""",
                (entity_type, entity_id, self._json(payload), now, now),
            )

    def mark_sync(
        self,
        entity_type: str,
        entity_id: str,
        ok: bool,
        error: str = "",
        remote_record_id: Optional[str] = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE sync_outbox SET status=?, attempts=attempts+1,last_error=?,
                   remote_record_id=COALESCE(?,remote_record_id),updated_at=?
                   WHERE entity_type=? AND entity_id=?""",
                ("SYNCED" if ok else "PENDING", error[:1000], remote_record_id,
                 utc_now_iso(), entity_type, entity_id),
            )

    def pending_sync(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT entity_type,entity_id,payload_json,attempts,last_error,remote_record_id
                   FROM sync_outbox WHERE status='PENDING' ORDER BY created_at LIMIT ?""",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def stats(
        self, product_id: Optional[str] = None, execution_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        totals_sql = """SELECT COUNT(*) AS generation_count,
                    COALESCE(SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END),0) AS pass_count,
                    COALESCE(SUM(CASE WHEN status IN ('FAILED','REJECTED') THEN 1 ELSE 0 END),0) AS fail_count,
                    ROUND(AVG(qa_score),2) AS avg_qa_score,
                    COALESCE(ROUND(SUM(COALESCE(json_extract(payload_json,'$.estimated_cost'),0)),4),0) AS total_cost,
                    ROUND(AVG(json_extract(payload_json,'$.generation_time_seconds')),2) AS avg_generation_seconds
                    FROM video_tasks"""
        groups_sql = """SELECT prompt_version,provider,model,execution_mode,COUNT(*) AS generation_count,
                    SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) AS pass_count,
                    ROUND(AVG(qa_score),2) AS avg_qa_score
                    FROM video_tasks"""
        conditions: list[str] = []
        params_list: list[Any] = []
        if product_id:
            conditions.append("product_id=?")
            params_list.append(product_id)
        if execution_mode:
            conditions.append("execution_mode=?")
            params_list.append(execution_mode)
        if conditions:
            where = " WHERE " + " AND ".join(conditions)
            totals_sql += where
            groups_sql += where
        params = tuple(params_list)
        groups_sql += " GROUP BY prompt_version,provider,model,execution_mode ORDER BY prompt_version,provider,model,execution_mode"
        with self._connect() as conn:
            totals = conn.execute(totals_sql, params).fetchone()
            groups = conn.execute(groups_sql, params).fetchall()
            counts = {
                "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "video_tasks": conn.execute("SELECT COUNT(*) FROM video_tasks").fetchone()[0],
                "qa_records": conn.execute("SELECT COUNT(*) FROM qa_records").fetchone()[0],
                "deliveries": conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0],
                "prompt_variants": conn.execute("SELECT COUNT(*) FROM prompt_variants").fetchone()[0],
                "assets": conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                "image_generations": conn.execute("SELECT COUNT(*) FROM image_generations").fetchone()[0],
            }
            pending = conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status='PENDING'").fetchone()[0]
        summary = dict(totals)
        summary["pass_rate"] = round((summary["pass_count"] or 0) / summary["generation_count"] * 100, 2) if summary["generation_count"] else 0
        summary["cost_basis"] = "local_estimate_not_provider_bill"
        summary["execution_mode_filter"] = execution_mode or "mixed"
        return {"summary": summary, "by_prompt_model": [dict(row) for row in groups], "counts": counts, "pending_feishu_sync": pending}


database = WorkflowDatabase()
