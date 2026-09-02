import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import db
from app.devin_client import CreatedSession
from app.main import app, settings


def signed_headers(body: bytes):
    digest = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "issues",
        "Content-Type": "application/json",
    }


def test_api_webhook_metrics_and_dashboard(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "sessions.db"))

    async def create(*args):
        return CreatedSession("session-42", "https://app.devin.ai/sessions/session-42")

    monkeypatch.setattr(
        "app.main.DevinClient.create_remediation_session",
        create,
    )
    payload = {
        "action": "opened",
        "issue": {"number": 42, "title": "Bug", "body": "Details"},
        "repository": {"full_name": "org/repo"},
    }
    body = json.dumps(payload).encode()
    with TestClient(app) as client:
        response = client.post("/webhook/github", content=body, headers=signed_headers(body))
        assert response.status_code == 202
        assert response.json()["session_id"] == "session-42"
        assert client.post("/webhook/github", content=body, headers=signed_headers(body)).json()["duplicate"]
        assert client.get("/metrics").json()["total"] == 1
        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "Superset Devin Automation" in dashboard.text
        assert client.get("/health").json() == {"status": "ok"}
    assert db.get_record_by_issue(str(tmp_path / "sessions.db"), 42).session_id == "session-42"


def test_unsigned_webhook_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "sessions.db"))
    with TestClient(app) as client:
        response = client.post("/webhook/github", content=b"{}", headers={"X-GitHub-Event": "issues"})
    assert response.status_code == 401


def test_trigger_creates_and_deduplicates(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "sessions.db"))

    async def create(*args):
        return CreatedSession("trigger-session", "https://app.devin.ai/sessions/trigger-session")

    monkeypatch.setattr("app.main.DevinClient.create_remediation_session", create)
    payload = {"title": "Manual bug", "body": "Details", "repo_full_name": "org/repo"}
    with TestClient(app) as client:
        response = client.post("/trigger/77", json=payload)
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "issue_number": 77,
            "session_id": "trigger-session",
            "url": "https://app.devin.ai/sessions/trigger-session",
            "duplicate": False,
        }
        duplicate = client.post("/trigger/77", json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["session_id"] == "trigger-session"


def test_trigger_rate_limit_includes_retry_header(monkeypatch, tmp_path):
    path = str(tmp_path / "sessions.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", path)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SESSIONS", 5)

    with TestClient(app) as client:
        for number in range(3):
            db.insert_record(path, number + 1, f"active-{number}", status="active")
        for number in range(2):
            db.insert_record(path, number + 4, f"blocked-{number}", status="blocked")
        response = client.post(
            "/trigger/77",
            json={"title": "Rate limited", "repo_full_name": "org/repo"},
        )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(settings.POLL_INTERVAL_SECONDS)
    assert response.json() == {
        "detail": "Concurrent Devin session limit reached; retry when a session completes",
        "active_sessions": 5,
        "limit": 5,
    }


def test_trigger_token_is_enforced(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(settings, "TRIGGER_TOKEN", "expected")

    async def create(*args):
        return CreatedSession("protected-session", None)

    monkeypatch.setattr("app.main.DevinClient.create_remediation_session", create)
    with TestClient(app) as client:
        payload = {"title": "Protected", "repo_full_name": "org/repo"}
        assert client.post("/trigger/80", json=payload).status_code == 401
        assert client.post(
            "/trigger/80", json=payload, headers={"X-Trigger-Token": "expected"}
        ).status_code == 200
