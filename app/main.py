import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import db
from .config import get_settings
from .devin_client import DevinAPIError, DevinClient
from .poller import run_poller
from .webhook import extract_remediation_issue, verify_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()


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


@app.post("/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()
    if not verify_signature(
        settings.GITHUB_WEBHOOK_SECRET,
        body,
        request.headers.get("X-Hub-Signature-256"),
    ):
        return JSONResponse({"detail": "Invalid webhook signature"}, status_code=401)
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"ok": True, "event": "ping"}
    if event != "issues":
        return {"ok": True, "ignored": event}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    issue = extract_remediation_issue(payload, settings.REMEDIATION_LABEL)
    if issue is None:
        action = payload.get("action", "unknown")
        event_label = (payload.get("label") or {}).get("name", "")
        return {"ok": True, "ignored": f"{action}/{event_label}"}
    existing = db.get_record_by_issue(settings.DATABASE_PATH, issue.number)
    if existing:
        return {"ok": True, "duplicate": True, "session_id": existing.session_id}
    if db.count_active(settings.DATABASE_PATH) >= settings.MAX_CONCURRENT_SESSIONS:
        return JSONResponse({"detail": "Maximum concurrent sessions reached"}, status_code=429)
    repo = issue.repo_full_name or settings.SUPERSET_REPO
    if not repo:
        return JSONResponse({"detail": "Repository full name is required"}, status_code=400)
    try:
        created = await request.app.state.devin_client.create_remediation_session(
            issue.number, issue.title, issue.body, repo
        )
    except DevinAPIError as exc:
        logger.warning("Devin API request failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=502)
    db.insert_record(
        settings.DATABASE_PATH,
        issue.number,
        created.session_id,
        status="active",
    )
    return JSONResponse(
        {
            "ok": True,
            "issue_number": issue.number,
            "session_id": created.session_id,
            "url": created.url,
        },
        status_code=202,
    )


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
