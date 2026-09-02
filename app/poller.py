import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import db

logger = logging.getLogger(__name__)
PR_URL_PATTERN = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


def extract_pr_url(session: dict[str, Any]) -> str | None:
    pull_request = session.get("pull_request")
    if isinstance(pull_request, dict) and isinstance(pull_request.get("url"), str):
        return pull_request["url"]
    structured = session.get("structured_output")
    if isinstance(structured, str):
        match = PR_URL_PATTERN.search(structured)
        if match:
            return match.group(0)
    elif isinstance(structured, dict):
        for value in structured.values():
            if isinstance(value, str):
                match = PR_URL_PATTERN.search(value)
                if match:
                    return match.group(0)
    match = PR_URL_PATTERN.search(json.dumps(session, default=str))
    return match.group(0) if match else None


async def poll_once(client: Any, session_tag: str, database_path: str = "data/sessions.db") -> None:
    sessions = await client.list_sessions(session_tag)
    by_id = {session.get("session_id"): session for session in sessions}
    for record in db.list_records(database_path):
        if record.status in {"completed", "failed"}:
            continue
        session = by_id.get(record.session_id)
        if not session:
            continue
        status_enum = str(session.get("status_enum", "")).lower()
        status = {
            "finished": "completed",
            "expired": "failed",
            "blocked": "blocked",
        }.get(status_enum, "active")
        completed_at = (
            datetime.now(timezone.utc).isoformat()
            if status == "completed" and record.completed_at is None
            else None
        )
        pr_url = extract_pr_url(session)
        raw_result = json.dumps(session, default=str)[:4000]
        db.update_record(
            database_path,
            record.session_id,
            status=status,
            completed_at=completed_at,
            pr_url=pr_url,
            result=raw_result,
        )


async def run_poller(
    client: Any, session_tag: str, interval: int, stop_event: asyncio.Event,
    database_path: str = "data/sessions.db",
) -> None:
    while not stop_event.is_set():
        try:
            await poll_once(client, session_tag, database_path)
        except Exception:
            logger.exception("Polling Devin sessions failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
