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
