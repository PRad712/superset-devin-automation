"""Small in-memory Devin API substitute for local demos."""

import argparse
import json
import re
import secrets
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


sessions: dict[str, dict[str, Any]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_summary(session: dict[str, Any]) -> dict[str, Any]:
    age = time.time() - session["created_epoch"]
    if age < 20:
        status_enum = "working"
        pull_request = None
        structured_output = None
    elif age < 40:
        status_enum = "blocked"
        pull_request = None
        structured_output = None
    else:
        status_enum = "finished"
        issue_match = re.search(r"issue-(\d+)", " ".join(session["tags"]))
        issue_number = issue_match.group(1) if issue_match else "0"
        pull_request = {"url": f"https://github.com/{session['repo']}/pull/{issue_number}"}
        structured_output = f"Pull request: {pull_request['url']}"
    return {
        "session_id": session["session_id"],
        "status": status_enum,
        "status_enum": status_enum,
        "tags": session["tags"],
        "title": session["title"],
        "created_at": session["created_at"],
        "updated_at": now_iso(),
        "pull_request": pull_request,
        "structured_output": structured_output,
    }


class MockHandler(BaseHTTPRequestHandler):
    def authorized(self) -> bool:
        return bool(self.headers.get("Authorization", "").removeprefix("Bearer ").strip())

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(401, {"detail": "Bearer token required"})
            return
        if self.path != "/v1/sessions":
            self.send_json(404, {"detail": "Not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        session_id = f"devin-mock-{secrets.token_hex(8)}"
        tags = payload.get("tags", [])
        prompt = payload.get("prompt", "")
        repo_match = re.search(
            r"repository ([A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)",
            prompt,
        )
        sessions[session_id] = {
            "session_id": session_id,
            "title": payload.get("title", ""),
            "tags": tags,
            "prompt": prompt,
            "repo": repo_match.group(1) if repo_match else "mock/repo",
            "created_at": now_iso(),
            "created_epoch": time.time(),
        }
        token = session_id.removeprefix("devin-mock-")
        self.send_json(
            200,
            {
                "session_id": session_id,
                "url": f"https://app.devin.ai/sessions/{token}",
                "is_new_session": True,
            },
        )

    def do_GET(self) -> None:
        if not self.authorized():
            self.send_json(401, {"detail": "Bearer token required"})
            return
        parsed = urlparse(self.path)
        if parsed.path != "/v1/sessions":
            self.send_json(404, {"detail": "Not found"})
            return
        requested_tags = parse_qs(parsed.query).get("tags", [])
        matching = [
            session_summary(session)
            for session in sessions.values()
            if not requested_tags or any(tag in session["tags"] for tag in requested_tags)
        ]
        self.send_json(200, {"sessions": matching})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    HTTPServer(("0.0.0.0", args.port), MockHandler).serve_forever()


if __name__ == "__main__":
    main()
