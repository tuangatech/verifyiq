# agents/orchestrator/db.py
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import VerificationRequest

DB_PATH = __import__("os").environ.get("DB_PATH", "/data/verifyiq.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    """Wraps all SQLite reads and writes for verifyiq.db."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self._db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create all three tables if they don't exist. Called once at lifespan startup."""
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS verification_requests (
                    task_id          TEXT PRIMARY KEY,
                    correlation_id   TEXT NOT NULL,
                    subject_name     TEXT NOT NULL,
                    subject_id       TEXT NOT NULL,
                    use_case         TEXT NOT NULL,
                    has_foreign_addr INTEGER NOT NULL,
                    status           TEXT NOT NULL DEFAULT 'submitted',
                    decision         TEXT,
                    created_at       TEXT NOT NULL,
                    completed_at     TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id                      TEXT PRIMARY KEY,
                    verification_request_id TEXT NOT NULL,
                    correlation_id          TEXT NOT NULL,
                    agent_name              TEXT NOT NULL,
                    skill                   TEXT NOT NULL,
                    status                  TEXT NOT NULL,
                    attempt                 INTEGER NOT NULL DEFAULT 1,
                    input                   TEXT NOT NULL,
                    artifact                TEXT,
                    error                   TEXT,
                    started_at              TEXT NOT NULL,
                    ended_at                TEXT
                );

                CREATE TABLE IF NOT EXISTS sse_events (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    correlation_id TEXT NOT NULL,
                    event_type     TEXT NOT NULL,
                    payload        TEXT NOT NULL,
                    emitted_at     TEXT NOT NULL
                );
            """)

    # ------------------------------------------------------------------
    # verification_requests
    # ------------------------------------------------------------------

    def create_verification_request(
        self,
        task_id: str,
        correlation_id: str,
        req: VerificationRequest,
    ) -> None:
        """Insert a new verification request row with status='submitted'."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO verification_requests
                    (task_id, correlation_id, subject_name, subject_id, use_case,
                     has_foreign_addr, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)
                """,
                (
                    task_id,
                    correlation_id,
                    req.subject_name,
                    req.subject_id,
                    req.use_case,
                    int(req.has_foreign_addr),
                    _now(),
                ),
            )

    def update_request_status(
        self,
        task_id: str,
        status: str,
        decision: str | None = None,
    ) -> None:
        """Update status and optional decision. Sets completed_at for terminal statuses."""
        terminal = {"completed", "failed"}
        completed_at = _now() if status in terminal else None
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE verification_requests
                SET status = ?, decision = ?, completed_at = ?
                WHERE task_id = ?
                """,
                (status, decision, completed_at, task_id),
            )

    def get_verification_request(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM verification_requests WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # agent_tasks
    # ------------------------------------------------------------------

    def create_agent_task(
        self,
        agent_task_id: str,
        verification_request_id: str,
        correlation_id: str,
        agent_name: str,
        skill: str,
        input_dict: dict[str, Any],
        attempt: int,
        started_at: str,
    ) -> None:
        """Insert a new agent_tasks row with status='working'. Called before dispatching
        each A2A task so the row exists even if the agent call never returns."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_tasks
                    (id, verification_request_id, correlation_id, agent_name,
                     skill, status, attempt, input, started_at)
                VALUES (?, ?, ?, ?, ?, 'working', ?, ?, ?)
                """,
                (
                    agent_task_id,
                    verification_request_id,
                    correlation_id,
                    agent_name,
                    skill,
                    attempt,
                    json.dumps(input_dict),
                    started_at,
                ),
            )

    def complete_agent_task(
        self,
        agent_task_id: str,
        status: str,
        artifact: dict | None = None,
        error: dict | None = None,
        ended_at: str | None = None,
    ) -> None:
        """Update a task row once the agent responds. Exactly one of artifact or error
        will be set depending on whether the task completed, failed, or timed out."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status = ?, artifact = ?, error = ?, ended_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(artifact) if artifact is not None else None,
                    json.dumps(error) if error is not None else None,
                    ended_at or _now(),
                    agent_task_id,
                ),
            )

    def get_agent_tasks(self, verification_request_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_tasks WHERE verification_request_id = ?",
                (verification_request_id,),
            ).fetchall()
        return [dict(r) for r in rows]
