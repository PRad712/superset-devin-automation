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

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/webhook/github` | Validate and process GitHub Issues events |
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
  "poll_interval_seconds": 30
}
```

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
