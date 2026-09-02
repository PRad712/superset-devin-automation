from datetime import datetime, timedelta, timezone

import pytest

from app import db


def test_insert_update_and_metrics(tmp_path):
    path = str(tmp_path / "nested" / "sessions.db")
    db.init_db(path)
    created = "2025-01-01T00:00:00+00:00"
    completed = "2025-01-01T00:01:30+00:00"
    record = db.insert_record(path, 42, "session-1", created_at=created)
    assert record.status == "active"
    assert db.count_active(path) == 1
    db.update_record(path, "session-1", status="completed", completed_at=completed, pr_url="https://github.com/o/r/pull/1")
    assert db.get_record_by_issue(path, 42).pr_url.endswith("/pull/1")
    result = db.metrics(path)
    assert result == {
        "active": 0,
        "completed": 1,
        "failed": 0,
        "total": 1,
        "avg_completion_seconds": timedelta(minutes=1, seconds=30).total_seconds(),
        "success_rate_percent": 100.0,
        "throughput_per_hour": 0.0,
        "completed_last_hour": 0,
        "avg_time_to_pr_seconds": 90.0,
    }


def test_extended_metrics(tmp_path):
    path = str(tmp_path / "sessions.db")
    db.init_db(path)
    now = datetime.now(timezone.utc)
    for number in range(3):
        completed = now - timedelta(minutes=5 + number)
        created = completed - timedelta(seconds=60)
        db.insert_record(
            path,
            number + 1,
            f"completed-{number}",
            status="completed",
            pr_url=f"https://github.com/o/r/pull/{number}" if number < 2 else None,
            created_at=created.isoformat(),
            completed_at=completed.isoformat(),
        )
    db.insert_record(
        path,
        4,
        "failed-1",
        status="failed",
        created_at=(now - timedelta(minutes=4)).isoformat(),
    )
    db.insert_record(
        path,
        5,
        "active-1",
        status="active",
        created_at=(now - timedelta(minutes=3)).isoformat(),
    )

    result = db.metrics(path)

    assert result["success_rate_percent"] == 75.0
    assert result["completed_last_hour"] == 3
    assert isinstance(result["throughput_per_hour"], float)
    assert result["throughput_per_hour"] > 0
    assert result["avg_time_to_pr_seconds"] == pytest.approx(60.0)


def test_empty_metrics(tmp_path):
    path = str(tmp_path / "sessions.db")
    db.init_db(path)

    result = db.metrics(path)

    assert result["success_rate_percent"] is None
    assert result["throughput_per_hour"] is None
    assert result["completed_last_hour"] == 0
    assert result["avg_time_to_pr_seconds"] is None
