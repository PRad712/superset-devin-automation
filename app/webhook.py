import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


@dataclass
class IssueInfo:
    number: int
    title: str
    body: str
    repo_full_name: str | None


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


def extract_remediation_issue(payload: dict[str, Any], label: str) -> IssueInfo | None:
    issue = payload.get("issue")
    if not isinstance(issue, dict) or "pull_request" in payload or "pull_request" in issue:
        return None
    action = payload.get("action")
    if action == "opened":
        pass
    elif action == "labeled" and payload.get("label", {}).get("name") == label:
        pass
    else:
        return None
    repository = payload.get("repository") or {}
    return IssueInfo(
        number=int(issue["number"]),
        title=issue.get("title", ""),
        body=issue.get("body") or "",
        repo_full_name=repository.get("full_name"),
    )
