import pytest

from app import db
from app.poller import extract_pr_url, poll_once


def test_extract_pr_url_variants():
    url = "https://github.com/org/repo/pull/12"
    assert extract_pr_url({"pull_request": {"url": url}}) == url
    assert extract_pr_url({"structured_output": {"pr": f"Opened {url}"}}) == url
    assert extract_pr_url({"structured_output": f"PR: {url}"}) == url
    assert extract_pr_url({"message": f"See {url}"}) == url
    assert extract_pr_url({"message": "No PR"}) is None


@pytest.mark.asyncio
async def test_poll_once_updates_completed_record(tmp_path):
    path = str(tmp_path / "sessions.db")
    db.init_db(path)
    db.insert_record(path, 42, "session-1")

    class FakeClient:
        async def list_sessions(self, tag):
            assert tag == "superset-remediation"
            return [{
                "session_id": "session-1",
                "status_enum": "finished",
                "pull_request": {"url": "https://github.com/org/repo/pull/42"},
                "answer": "done",
            }]

    await poll_once(FakeClient(), "superset-remediation", path)
    record = db.get_record_by_issue(path, 42)
    assert record.status == "completed"
    assert record.pr_url.endswith("/pull/42")
    assert '"answer": "done"' in record.result
    assert record.completed_at is not None
