import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class SessionRecord:
    issue_number: int
    session_id: str
    status: str
    pr_url: str | None
    created_at: str
    completed_at: str | None
    result: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _record(row: sqlite3.Row | None) -> SessionRecord | None:
    return SessionRecord(**dict(row)) if row else None


def init_db(path: str) -> None:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_records (
                issue_number INTEGER PRIMARY KEY,
                session_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                pr_url TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                result TEXT
            )
            """
        )


def insert_record(
    path: str,
    issue_number: int,
    session_id: str,
    status: str = "active",
    pr_url: str | None = None,
    created_at: str | None = None,
    completed_at: str | None = None,
    result: str | None = None,
) -> SessionRecord:
    created_at = created_at or utc_now()
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO session_records
                (issue_number, session_id, status, pr_url, created_at, completed_at, result)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (issue_number, session_id, status, pr_url, created_at, completed_at, result),
        )
    return SessionRecord(issue_number, session_id, status, pr_url, created_at, completed_at, result)


def get_record_by_issue(path: str, issue_number: int) -> SessionRecord | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM session_records WHERE issue_number = ?", (issue_number,)
        ).fetchone()
    return _record(row)


def list_records(path: str) -> list[SessionRecord]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM session_records ORDER BY created_at DESC"
        ).fetchall()
    return [SessionRecord(**dict(row)) for row in rows]


def update_record(
    path: str,
    session_id: str,
    *,
    status: str | None = None,
    pr_url: str | None = None,
    completed_at: str | None = None,
    result: str | None = None,
) -> None:
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if pr_url is not None:
        updates["pr_url"] = pr_url
    if completed_at is not None:
        updates["completed_at"] = completed_at
    if result is not None:
        updates["result"] = result
    if not updates:
        return
    assignments = ", ".join(f"{column} = ?" for column in updates)
    with _connect(path) as connection:
        connection.execute(
            f"UPDATE session_records SET {assignments} WHERE session_id = ?",
            [*updates.values(), session_id],
        )


def count_active(path: str) -> int:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM session_records WHERE status IN ('active', 'blocked')"
        ).fetchone()
    return int(row["count"])


def metrics(path: str) -> dict[str, Any]:
    with _connect(path) as connection:
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('active', 'blocked') THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                COUNT(*) AS total,
                MIN(created_at) AS earliest_created_at
            FROM session_records
            """
        ).fetchone()
        rows = connection.execute(
            "SELECT created_at, completed_at, pr_url FROM session_records "
            "WHERE status = 'completed' AND completed_at IS NOT NULL"
        ).fetchall()
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(minutes=60)
    durations = []
    pr_durations = []
    completed_last_hour = 0
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        completed = datetime.fromisoformat(row["completed_at"])
        duration = (completed - created).total_seconds()
        durations.append(duration)
        if row["pr_url"] is not None:
            pr_durations.append(duration)
        if recent_cutoff <= completed <= now:
            completed_last_hour += 1
    active = int(counts["active"] or 0)
    completed_count = int(counts["completed"] or 0)
    failed = int(counts["failed"] or 0)
    total = int(counts["total"] or 0)
    resolved = completed_count + failed
    success_rate = round(completed_count / resolved * 100, 1) if resolved else None
    if total:
        earliest = datetime.fromisoformat(counts["earliest_created_at"])
        hours = max((now - earliest).total_seconds() / 3600, 1.0)
        throughput = round(completed_count / hours, 2)
    else:
        throughput = None
    return {
        "active": active,
        "completed": completed_count,
        "failed": failed,
        "total": total,
        "avg_completion_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "success_rate_percent": success_rate,
        "throughput_per_hour": throughput,
        "completed_last_hour": completed_last_hour,
        "avg_time_to_pr_seconds": (
            round(sum(pr_durations) / len(pr_durations), 1) if pr_durations else None
        ),
    }
