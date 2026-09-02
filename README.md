# superset-devin-automation

Dockerized FastAPI service that turns selected GitHub issue events into Devin
remediation sessions, tracks their progress, and exposes a small dashboard.

## Architecture

```text
GitHub Issues webhook
          |
          v
  FastAPI /webhook/github -- HMAC validation --> SQLite session_records
          |                                             ^
          v                                             |
     Devin API session                            async poller
          |                                             |
          +-------------------- status/PR URL ----------+
                                |
                  /health /metrics /sessions /dashboard
```

The service uses plain `sqlite3`; no external database or SQL model layer is
needed. The poller is intentionally best-effort and logs failures so a
temporary Devin API problem does not stop the web process.

## Components

* `app/config.py` - environment-backed settings.
* `app/webhook.py` - GitHub signature and issue-event handling.
* `app/devin_client.py` - async Devin API client.
* `app/poller.py` - session status and pull-request discovery.
* `app/db.py` - SQLite schema and session metrics.
* `app/main.py` - FastAPI lifecycle and HTTP endpoints.
* `app/templates/dashboard.html` - framework-free dashboard.

## Setup

### Local

Python 3.11 is the target runtime (Python 3.12 also works locally):

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env           # Windows: Copy-Item .env.example .env
# Edit .env with real credentials and repository settings.
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker Compose

```bash
cp .env.example .env
# Edit .env
docker compose up --build
```

SQLite data is persisted in `./data`, mounted at `/app/data` in the container.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DEVIN_API_KEY` | yes | - | Devin API bearer token |
| `GITHUB_WEBHOOK_SECRET` | yes | - | Secret used for HMAC webhook validation |
| `DEVIN_API_BASE_URL` | no | `https://api.devin.ai/v1` | Devin API base URL |
| `DATABASE_PATH` | no | `data/sessions.db` | SQLite database path |
| `POLL_INTERVAL_SECONDS` | no | `30` | Devin polling interval |
| `REMEDIATION_LABEL` | no | `devin-remediate` | Label that triggers remediation |
| `SESSION_TAG` | no | `superset-remediation` | Tag used to find Devin sessions |
| `MAX_CONCURRENT_SESSIONS` | no | `5` | Active/blocked session limit |
| `SUPERSET_REPO` | no | empty | Repository fallback as `org/repo` |
| `TRIGGER_TOKEN` | no | empty | Optional token required by the manual trigger endpoint |
| `DISABLE_POLLER` | no | `false` | Disable polling for tests or maintenance |

## Register the GitHub webhook

In the repository, open **Settings → Webhooks → Add webhook**:

* Payload URL: `https://<host>/webhook/github`
* Content type: `application/json`
* Secret: the value of `GITHUB_WEBHOOK_SECRET`
* Events: select **Issues**

For local development, expose port 8000 with a tunnel such as ngrok:
`ngrok http 8000`.

An issue opened event always triggers remediation. Existing issues trigger
remediation when the `devin-remediate` label (or configured label) is added.
Pull request events and unrelated issue actions are ignored.

## Manual trigger endpoint

`POST /trigger/{issue_number}` accepts an issue title, body, and optional
repository override. It uses `SUPERSET_REPO` when `repo_full_name` is omitted:

```bash
curl -X POST http://localhost:8000/trigger/43420 \
  -H 'Content-Type: application/json' \
  -H 'X-Trigger-Token: optional-token' \
  -d '{"title":"Investigate dashboard bug","body":"Please investigate.","repo_full_name":"PRad712/superset"}'
```

When `TRIGGER_TOKEN` is configured, the matching `X-Trigger-Token` header is
required. New manual triggers return `200`; duplicates also return `200` with
`"duplicate": true`. Both manual triggers and GitHub webhooks share the
duplicate check, concurrency limit, Devin session creation, and database
recording path. When the limit is reached, the endpoint returns `429` with
`active_sessions`, `limit`, and a `Retry-After` header equal to
`POLL_INTERVAL_SECONDS`. Both `active` and `blocked` sessions count toward the
limit.

## Simulate a webhook

With the service running, a pure bash example is:

```bash
secret='testsecret'
payload='examples/issue_opened.json'
signature=$(openssl dgst -sha256 -hmac "$secret" "$payload" | sed 's/^.* //')
curl -i -X POST http://localhost:8000/webhook/github \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: issues' \
  -H "X-Hub-Signature-256: sha256=$signature" \
  --data-binary @"$payload"
```

The standard-library alternative is:

```bash
python scripts/send_webhook.py examples/issue_opened.json --secret testsecret
```

## Structured logs and demo mode

The API emits one JSON object per log line, including timestamps, levels,
logger names, event names, and event fields:

```json
{"ts":"2026-01-01T00:00:00+00:00","level":"INFO","logger":"app.main","event":"session_created","message":"session_created","source":"manual","issue_number":42,"session_id":"devin-123","url":null,"repo":"org/repo"}
```

For a local demo without spending Devin ACUs, set these values in `.env`:

```dotenv
DEVIN_API_BASE_URL=http://mock-devin:9000/v1
POLL_INTERVAL_SECONDS=10
MAX_CONCURRENT_SESSIONS=5
```

Then start the API and mock Devin service:

```bash
docker compose --profile demo up --build -d
python scripts/simulate_events.py --delay 2
```

The simulator reads `scripts/sample_issues.json`, ignores unknown fields such
as `source_url`, and submits each issue to the manual trigger endpoint. The
mock session transitions from `working` to `blocked` to `finished` over about
40 seconds, at which point the poller records a mock pull request URL.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/webhook/github` | Validate and process GitHub Issues events |
| `POST` | `/trigger/{issue_number}` | Manually start remediation for an issue |
| `GET` | `/health` | Liveness check |
| `GET` | `/metrics` | Counts and average completion duration |
| `GET` | `/sessions` | Session records for the dashboard |
| `GET` | `/dashboard` | Responsive HTML dashboard |

The dashboard refreshes sessions and metrics every 10 seconds and ticks elapsed
durations every second. Devin session links are derived from the session ID;
pull-request links are shown when discovered.

Example metrics response:

```json
{
  "active": 1,
  "completed": 4,
  "failed": 1,
  "total": 6,
  "avg_completion_seconds": 1842.5,
  "success_rate_percent": 80.0,
  "throughput_per_hour": 0.12,
  "completed_last_hour": 2,
  "avg_time_to_pr_seconds": 1750.0,
  "poll_interval_seconds": 30
}
```

`success_rate_percent` is completed sessions divided by completed plus failed
sessions. `throughput_per_hour` is completed sessions per hour since the
earliest record was created. `completed_last_hour` counts completed sessions
finished in the last 60 minutes. `avg_time_to_pr_seconds` is the average
completion time for completed sessions with a discovered pull request.

Devin statuses map as follows: `finished` to `completed`, `expired` to
`failed`, `blocked` to `blocked`, and all other statuses (including `working`,
`running`, and `resumed`) to `active`.

## Tests and lint

```bash
ruff check .
pytest -q
```

Tests use temporary SQLite databases and set `DISABLE_POLLER=1`.

## Notes and limitations

The poller assumes Devin API fields named `status_enum` and `pull_request`,
based on the API documentation. If those fields differ in the deployed API,
adjust `poller.extract_pr_url` and the status mapping. PR URLs are also
searched for in structured output and serialized session data as a fallback.
