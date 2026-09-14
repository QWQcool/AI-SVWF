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
from core.schemas import ProductAnalysis, PromptSchemaV1, PromptVariant, StitchResult, VideoTaskRecord, utc_now_iso


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
                   qa_score,qa_status,parent_task_id,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(internal_task_id) DO UPDATE SET status=excluded.status,
                   qa_score=excluded.qa_score, qa_status=excluded.qa_status,
                   payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (task.internal_task_id, task.product_id, task.shot_id, task.provider, task.model,
                 task.prompt_version, task.status.value, task.qa_score,
                 task.qa_status.value if task.qa_status else None, task.parent_task_id,
                 self._json(task), task.created_at, task.updated_at),
            )
            if previous_status != task.status.value:
                conn.execute(
                    "INSERT INTO task_events(internal_task_id,status,detail_json,created_at) VALUES(?,?,?,?)",
                    (task.internal_task_id, task.status.value, self._json(event_detail or {}), utc_now_iso()),
                )

    def get_task(self, internal_task_id: str) -> Optional[VideoTaskRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM video_tasks WHERE internal_task_id=?", (internal_task_id,)
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

    def stats(self, product_id: Optional[str] = None) -> Dict[str, Any]:
        totals_sql = """SELECT COUNT(*) AS generation_count,
                    COALESCE(SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END),0) AS pass_count,
                    COALESCE(SUM(CASE WHEN status IN ('FAILED','REJECTED') THEN 1 ELSE 0 END),0) AS fail_count,
                    ROUND(AVG(qa_score),2) AS avg_qa_score,
                    COALESCE(ROUND(SUM(COALESCE(json_extract(payload_json,'$.estimated_cost'),0)),4),0) AS total_cost,
                    ROUND(AVG(json_extract(payload_json,'$.generation_time_seconds')),2) AS avg_generation_seconds
                    FROM video_tasks"""
        groups_sql = """SELECT prompt_version,provider,model,COUNT(*) AS generation_count,
                    SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END) AS pass_count,
                    ROUND(AVG(qa_score),2) AS avg_qa_score
                    FROM video_tasks"""
        params: tuple[Any, ...] = ()
        if product_id:
            totals_sql += " WHERE product_id=?"
            groups_sql += " WHERE product_id=?"
            params = (product_id,)
        groups_sql += " GROUP BY prompt_version,provider,model ORDER BY prompt_version,provider,model"
        with self._connect() as conn:
            totals = conn.execute(totals_sql, params).fetchone()
            groups = conn.execute(groups_sql, params).fetchall()
            counts = {
                "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "video_tasks": conn.execute("SELECT COUNT(*) FROM video_tasks").fetchone()[0],
                "qa_records": conn.execute("SELECT COUNT(*) FROM qa_records").fetchone()[0],
                "deliveries": conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0],
                "prompt_variants": conn.execute("SELECT COUNT(*) FROM prompt_variants").fetchone()[0],
            }
            pending = conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status='PENDING'").fetchone()[0]
        summary = dict(totals)
        summary["pass_rate"] = round((summary["pass_count"] or 0) / summary["generation_count"] * 100, 2) if summary["generation_count"] else 0
        return {"summary": summary, "by_prompt_model": [dict(row) for row in groups], "counts": counts, "pending_feishu_sync": pending}


database = WorkflowDatabase()
