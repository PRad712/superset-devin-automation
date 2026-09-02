import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import db
from .config import get_settings
from .devin_client import DevinAPIError, DevinClient
from .logging_config import configure_logging, log_event
from .poller import run_poller
from .webhook import IssueInfo, extract_remediation_issue, verify_signature

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


class TriggerRequest(BaseModel):
    title: str
    body: str = ""
    repo_full_name: str | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    db.init_db(settings.DATABASE_PATH)
    client = DevinClient(
        settings.DEVIN_API_KEY,
        settings.DEVIN_API_BASE_URL,
        session_tag=settings.SESSION_TAG,
    )
    application.state.devin_client = client
    application.state.stop_event = asyncio.Event()
    application.state.poller_task = None
    if not settings.DISABLE_POLLER:
        application.state.poller_task = asyncio.create_task(
            run_poller(
                client,
                settings.SESSION_TAG,
                settings.POLL_INTERVAL_SECONDS,
                application.state.stop_event,
                settings.DATABASE_PATH,
            )
        )
    yield
    application.state.stop_event.set()
    if application.state.poller_task:
        application.state.poller_task.cancel()
        try:
            await application.state.poller_task
        except asyncio.CancelledError:
            pass
    await client.aclose()


app = FastAPI(title="superset-devin-automation", lifespan=lifespan)


async def start_remediation(application: FastAPI, issue: IssueInfo, source: str) -> JSONResponse:
    existing = db.get_record_by_issue(settings.DATABASE_PATH, issue.number)
    if existing:
        log_event(
            logger,
            "session_duplicate",
            issue_number=issue.number,
            session_id=existing.session_id,
        )
        return JSONResponse(
            {
                "ok": True,
                "issue_number": issue.number,
                "session_id": existing.session_id,
                "duplicate": True,
            }
        )
    active_sessions = db.count_active(settings.DATABASE_PATH)
    if active_sessions >= settings.MAX_CONCURRENT_SESSIONS:
        log_event(
            logger,
            "webhook_rejected",
            source=source,
            reason="rate_limited",
            issue_number=issue.number,
        )
        return JSONResponse(
            {
                "detail": "Concurrent Devin session limit reached; retry when a session completes",
                "active_sessions": active_sessions,
                "limit": settings.MAX_CONCURRENT_SESSIONS,
            },
            status_code=429,
            headers={"Retry-After": str(settings.POLL_INTERVAL_SECONDS)},
        )
    repo = issue.repo_full_name or settings.SUPERSET_REPO
    if not repo:
        log_event(
            logger,
            "webhook_rejected",
            source=source,
            reason="missing_repo",
            issue_number=issue.number,
        )
        return JSONResponse({"detail": "Repository full name is required"}, status_code=400)
    try:
        created = await application.state.devin_client.create_remediation_session(
            issue.number, issue.title, issue.body, repo
        )
    except DevinAPIError as exc:
        log_event(
            logger,
            "webhook_rejected",
            source=source,
            reason="devin_api_error",
            issue_number=issue.number,
        )
        return JSONResponse({"detail": str(exc)}, status_code=502)
    db.insert_record(settings.DATABASE_PATH, issue.number, created.session_id, status="active")
    log_event(
        logger,
        "session_created",
        source=source,
        issue_number=issue.number,
        session_id=created.session_id,
        url=created.url,
        repo=repo,
    )
    return JSONResponse(
        {
            "ok": True,
            "issue_number": issue.number,
            "session_id": created.session_id,
            "url": created.url,
            "duplicate": False,
        },
        status_code=200 if source == "manual" else 202,
    )


@app.post("/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()
    if not verify_signature(
        settings.GITHUB_WEBHOOK_SECRET,
        body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        log_event(logger, "webhook_rejected", source="github", reason="invalid_signature")
        return JSONResponse({"detail": "Invalid webhook signature"}, status_code=401)
    event = request.headers.get("X-GitHub-Event", "")
    delivery = request.headers.get("X-GitHub-Delivery", "-")
    log_event(
        logger,
        "webhook_received",
        source="github",
        github_event=event or "-",
        delivery=delivery,
    )
    if event == "ping":
        return {"ok": True, "event": "ping"}
    if event != "issues":
        return {"ok": True, "ignored": event}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log_event(logger, "webhook_rejected", source="github", reason="invalid_json")
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    issue = extract_remediation_issue(payload, settings.REMEDIATION_LABEL)
    if issue is None:
        action = payload.get("action", "unknown")
        event_label = (payload.get("label") or {}).get("name", "")
        log_event(
            logger,
            "webhook_ignored",
            source="github",
            action=action,
            label=event_label or "-",
        )
        return {"ok": True, "ignored": f"{action}/{event_label}"}
    log_event(
        logger,
        "webhook_received",
        source="github",
        github_event=event,
        delivery=delivery,
        issue_number=issue.number,
    )
    return await start_remediation(request.app, issue, "github")


@app.post("/trigger/{issue_number}")
async def trigger_remediation(
    issue_number: int,
    trigger: TriggerRequest,
    http_request: Request,
    x_trigger_token: str | None = Header(default=None),
):
    if settings.TRIGGER_TOKEN is not None and x_trigger_token != settings.TRIGGER_TOKEN:
        return JSONResponse({"detail": "Invalid trigger token"}, status_code=401)
    repo = trigger.repo_full_name or settings.SUPERSET_REPO
    issue = IssueInfo(issue_number, trigger.title, trigger.body, repo)
    log_event(
        logger,
        "webhook_received",
        source="manual",
        github_event="trigger",
        delivery="-",
        issue_number=issue_number,
    )
    return await start_remediation(http_request.app, issue, "manual")


@app.get("/metrics")
async def get_metrics():
    result = db.metrics(settings.DATABASE_PATH)
    result["poll_interval_seconds"] = settings.POLL_INTERVAL_SECONDS
    return result


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/sessions")
async def get_sessions():
    return [record.to_dict() for record in db.list_records(settings.DATABASE_PATH)]


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    template = Path(__file__).parent / "templates" / "dashboard.html"
    return HTMLResponse(template.read_text(encoding="utf-8"))
