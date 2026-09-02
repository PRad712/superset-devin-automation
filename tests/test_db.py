from datetime import timedelta

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
    }
