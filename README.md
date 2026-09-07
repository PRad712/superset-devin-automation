# superset-devin-automation

A small, Dockerized FastAPI service that turns GitHub issues into Devin
remediation sessions, tracks each session to completion, and reports system
health on a dashboard that a non-engineer can read.

## Problem statement

Apache Superset (like most large open-source projects) accumulates well-scoped
bug reports faster than maintainers can pick them up. Each one costs an
engineer the same fixed overhead — read the report, reproduce it, find the code,
fix, test, open a PR — even when the fix itself is small. This service
delegates that work to Devin while keeping review and merge with a human: when
an issue is opened or labelled `devin-remediate`, it verifies the GitHub webhook, hands the issue to a Devin
session with a remediation prompt, enforces a concurrency cap so cost stays
bounded, polls Devin until the session finishes, records the resulting pull
request, and exposes throughput / success-rate metrics so a lead can answer
"is this working?" without reading code.

## Architecture

```mermaid
flowchart LR
    GH[GitHub Issues<br/>webhook] -->|POST /webhook/github<br/>X-Hub-Signature-256| API
    OP[Operator / demo script] -->|POST /trigger/:issue| API

    subgraph Service["superset-devin-automation (FastAPI, one container)"]
        API[HTTP layer<br/>app/main.py] -->|verify HMAC, filter action/label| WH[app/webhook.py]
        API -->|dedupe + concurrency check| DB[(SQLite<br/>session_records)]
        API -->|create_remediation_session| DC[DevinClient<br/>app/devin_client.py]
        POLL[Async poller<br/>app/poller.py<br/>every POLL_INTERVAL_SECONDS] -->|list_sessions tag=superset-remediation| DC
        POLL -->|status + PR URL| DB
        API -->|/metrics /sessions /dashboard| DB
    end

    DC -->|POST /v1/sessions<br/>GET /v1/sessions| DEVIN[Devin API]
    DEVIN -->|clones repo, fixes, opens PR| PR[Pull request<br/>on target repo]
    DASH[Dashboard viewer] -->|GET /dashboard| API
```

Data flow for one issue:

1. GitHub (or the manual trigger) delivers an issue. The signature is checked
   with `GITHUB_WEBHOOK_SECRET`; only `opened` events and `labeled` events
   carrying `devin-remediate` continue.
2. The issue is rejected as a duplicate if a record already exists, or with
   HTTP 429 if `MAX_CONCURRENT_SESSIONS` active/blocked sessions are running.
3. `DevinClient.create_remediation_session` posts a prompt to the Devin API,
   tagged `superset-remediation` and `issue-<n>`, and a `session_records` row
   is inserted with `status=active`.
4. The poller lists Devin sessions by tag, maps Devin's `status_enum` to
   `active | blocked | completed | failed`, extracts `pull_request.url`, and
   logs every status transition as a structured JSON event.
5. `/metrics` and `/dashboard` read the table; nothing else is stateful.

Design choices worth knowing for review:

* **Single process, plain `sqlite3`.** No ORM, no broker, no external DB. The
  poller is an `asyncio` task inside the web process; a Devin API outage is
  logged and retried on the next tick rather than crashing the service.
* **All triggers share one code path** (`start_remediation` in `app/main.py`):
  dedupe, concurrency limit, session creation, and persistence behave
  identically for webhooks and manual triggers.
* **Structured JSON logs** (`app/logging_config.py`) — one object per line with
  `event`, `issue_number`, `session_id`, `old_status`/`new_status` fields, so
  the pipeline is greppable and ships straight into any log aggregator.
* **Devin API v1** is used (`POST/GET /v1/sessions`). Field names
  (`session_id`, `url`, `status_enum`, `pull_request.url`, `tags`) were
  verified against the published OpenAPI spec and a live session. Devin marks
  v1 as legacy; migrating to the v3 organisation-scoped API is a contained
  change in `app/devin_client.py`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI app, lifespan (DB init, poller task), all HTTP endpoints |
| `app/webhook.py` | HMAC verification and issue-event filtering |
| `app/devin_client.py` | Async Devin API client and remediation prompt |
| `app/poller.py` | Background poll loop, status mapping, PR-URL extraction |
| `app/db.py` | SQLite schema, record CRUD, metrics aggregation |
| `app/logging_config.py` | JSON log formatter and `log_event` helper |
| `app/templates/dashboard.html` | Framework-free dashboard (HTML + vanilla JS) |
| `scripts/simulate_events.py` | Posts `scripts/sample_issues.json` to `/trigger` |
| `scripts/mock_devin_api.py` | Optional stand-in Devin API for no-cost local testing (`demo` compose profile) |
| `scripts/send_webhook.py` | Signs and sends an example webhook payload |
| `examples/*.json` | Sample GitHub `issues` webhook payloads |
| `tests/` | pytest suite (webhook auth, trigger, limits, poller, metrics, logging) |

## Setup

### Prerequisites

* Docker with Compose v2 (or Python 3.11+ for a local run).
* A Devin API key (Devin → Settings → API keys).
* A GitHub repository you can add a webhook to.

### 1. Configure

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
```

Set at minimum:

```dotenv
DEVIN_API_KEY=<your Devin API key>
GITHUB_WEBHOOK_SECRET=<random string; reuse it when registering the webhook>
SUPERSET_REPO=<org>/<repo>    # repo Devin should fix when the trigger omits one
```

All variables:

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DEVIN_API_KEY` | yes | - | Devin API bearer token |
| `GITHUB_WEBHOOK_SECRET` | yes | - | Secret used for HMAC webhook validation |
| `DEVIN_API_BASE_URL` | no | `https://api.devin.ai/v1` | Devin API base URL (point at the mock for the optional local test profile) |
| `DATABASE_PATH` | no | `data/sessions.db` | SQLite database path |
| `POLL_INTERVAL_SECONDS` | no | `30` | Devin polling interval |
| `REMEDIATION_LABEL` | no | `devin-remediate` | Label that triggers remediation |
| `SESSION_TAG` | no | `superset-remediation` | Tag used to find Devin sessions |
| `MAX_CONCURRENT_SESSIONS` | no | `5` | Active + blocked session limit (HTTP 429 above it) |
| `SUPERSET_REPO` | no | empty | Repository fallback as `org/repo` |
| `TRIGGER_TOKEN` | no | empty | If set, `/trigger` requires a matching `X-Trigger-Token` header |
| `DISABLE_POLLER` | no | `false` | Disable polling (used by the test suite) |

### 2. Run

```bash
docker compose up --build -d
curl http://localhost:8000/health        # {"status":"ok"}
open http://localhost:8000/dashboard
```

SQLite data persists in `./data` (mounted at `/app/data`). Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Register the GitHub webhook

In the target repository open **Settings → Webhooks → Add webhook**:

* Payload URL: `https://<public-host>/webhook/github`
* Content type: `application/json`
* Secret: the value of `GITHUB_WEBHOOK_SECRET`
* Events: **Let me select individual events → Issues**

For a laptop, expose port 8000 first (`ngrok http 8000`) and use the ngrok URL.
GitHub's "Recent Deliveries" tab shows the response; `202` means a session was
created, `200` with `"ignored"` means the event did not match (wrong action or
label), `401` means the secret does not match.

Behaviour: an `opened` issue always triggers remediation; an existing issue
triggers when `devin-remediate` is added. Everything else is ignored.

### 4. Run the simulation script

`scripts/simulate_events.py` reads `scripts/sample_issues.json` (five real
upstream `apache/superset` issues) and POSTs each to `/trigger/{issue_number}`,
exercising the manual trigger path, the concurrency cap, persistence, polling
and the dashboard. Against the real Devin API (the default `.env`) it creates
real sessions and spends ACUs — that is how the supporting run described under
[Recorded webhook remediation results](#recorded-webhook-remediation-results)
was produced:

```bash
docker compose up --build -d
python scripts/simulate_events.py --delay 2
```

Options: `--url`, `--issues` (path to the JSON file), `--delay`, `--token`
(when `TRIGGER_TOKEN` is set).

**Optional no-cost local profile.** For a functional check without GitHub or
Devin spend, the `demo` compose profile adds a mock Devin API that walks each
session through `working → blocked → finished` in ~40 s and returns a
placeholder PR URL. This profile is a local test fixture only; none of the
results reported in this README came from it.

```bash
# .env additions for the mock profile
# DEVIN_API_BASE_URL=http://mock-devin:9000/v1
# POLL_INTERVAL_SECONDS=10

docker compose --profile demo up --build -d
python scripts/simulate_events.py --delay 2
```

Expected result: five `200` responses, five rows on the dashboard, a sixth
trigger returns `429`, and within a minute the health banner reads "Healthy —
fixes are being delivered".

### Simulate a raw GitHub webhook

```bash
python scripts/send_webhook.py examples/issue_opened.json --secret testsecret
```

or with `openssl` + `curl`:

```bash
secret='testsecret'; payload='examples/issue_opened.json'
signature=$(openssl dgst -sha256 -hmac "$secret" "$payload" | sed 's/^.* //')
curl -i -X POST http://localhost:8000/webhook/github \
  -H 'Content-Type: application/json' -H 'X-GitHub-Event: issues' \
  -H "X-Hub-Signature-256: sha256=$signature" --data-binary @"$payload"
```

## Recorded webhook remediation results

The submitted demonstration used the real GitHub Issues webhook path end to
end: an issue in the fork [PRad712/superset](https://github.com/PRad712/superset)
was labelled `devin-remediate` → GitHub delivered the webhook → this service
verified the HMAC signature → a real Devin API session was created →
the poller tracked its status → Devin opened a PR for review. Five issues were
created in the fork for this purpose; all five resulted in merged PRs
(#10–#14). Investigation, implementation, test creation and PR preparation
were delegated to Devin; issue selection, review and merge stayed with a
human as the control point.

| Issue | Source / rationale | Outcome |
| --- | --- | --- |
| [#1](https://github.com/PRad712/superset/issues/1) SQL injection risk in query-cancellation logic (`postgres.py`, `redshift.py`) | Bandit B608 / CWE-89 on this fork: f-string SQL in the Postgres and Redshift cancel-query paths | [PR #14](https://github.com/PRad712/superset/pull/14), merged. Both DBAPI queries parameterised, strict cancel-query-ID validation kept as defence in depth, other engine-spec cancel paths audited, tests prove values such as `1; DROP TABLE users; --` never reach `cursor.execute` |
| [#2](https://github.com/PRad712/superset/issues/2) Resource ownership authorisation test gap | Modelled on a documented Superset vulnerability class (not a new finding) | [PR #12](https://github.com/PRad712/superset/pull/12), merged. Integration tests across dashboard, chart and dataset `PUT` endpoints prove a non-owner viewer cannot reassign owners; validated by temporarily removing the check and watching the tests fail |
| [#3](https://github.com/PRad712/superset/issues/3) `/explore` datasource metadata authorisation check | Modelled on a documented Superset vulnerability class (not a new finding) | [PR #11](https://github.com/PRad712/superset/pull/11), merged. IDOR-style gap closed: a `form_data` datasource override was checked only against the chart's original datasource; access is now verified against the datasource actually returned, with regression tests |
| [#4](https://github.com/PRad712/superset/issues/4) Vulnerable pinned dependencies: `flask`, `paramiko` | `pip-audit` on this fork: Flask CVE-2026-27205, Paramiko SHA-1 RSA CVE-2026-44405 | [PR #13](https://github.com/PRad712/superset/pull/13), merged. Flask 2.3.3 → 3.1.3 with compatibility fixes; configurable SSH-algorithm blocklist for Paramiko (no fixed upstream release available). 2,062 passed / 1 skipped in the touched suite |
| [#5](https://github.com/PRad712/superset/issues/5) Unsafe pickle deserialisation + weak MD5 hashing (`key_value` module) | Bandit on this fork | [PR #10](https://github.com/PRad712/superset/pull/10), merged. `RestrictedUnpickler` allowlist with audit logging; test proves an `os.system` pickle payload is rejected without executing. MD5 call sites audited and classified as non-security (cache keys, fingerprints) |

A note on session status: a Devin session can remain `blocked` after it has
opened its PR while it waits for further instructions. The PR link surfaced
on the dashboard is the observable remediation output; `blocked` is not a
failure state.

### Supporting simulation run

Before the live webhook demo, `scripts/simulate_events.py` was run against the
real Devin API with the five upstream `apache/superset` issues in
`scripts/sample_issues.json`, to exercise the manual `/trigger` path,
concurrency limiting, persistence, polling and dashboard metrics. That run
also produced real activity in the fork:

* [PR #6](https://github.com/PRad712/superset/pull/6), merged — fix for
  upstream [#39951](https://github.com/apache/superset/issues/39951)
  (string-encoded saved metrics with verbose names not coerced to numeric).
* [PR #7](https://github.com/PRad712/superset/pull/7), merged — fix for
  upstream [#38936](https://github.com/apache/superset/issues/38936) (stale
  chart-data error blocking the native time-range filter default-value picker).
* [PR #8](https://github.com/PRad712/superset/pull/8), merged — fix for
  upstream [#40704](https://github.com/apache/superset/issues/40704)
  (native-filter scopes not re-synchronising on return to a multi-tab
  dashboard).
* [PR #9](https://github.com/PRad712/superset/pull/9) — an additional
  `SKILL.md` documentation PR, rejected by the repository's licence guard
  because the new file lacked the required ASF header. Not a remediation.
* Upstream [#40419](https://github.com/apache/superset/issues/40419) and
  [#43420](https://github.com/apache/superset/issues/43420) were found by
  Devin to be already fixed on `master`; no redundant PRs were opened.

Because `session_records` persisted across both runs, a dashboard screenshot
from that period can show nine historical records — five from this supporting
run and four webhook-triggered issues (#2–#5) — while issue #1 was recorded
separately on a clean database during the live label-triggered workflow.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/webhook/github` | Validate and process GitHub Issues events |
| `POST` | `/trigger/{issue_number}` | Manually start remediation (`{"title","body","repo_full_name"?}`) |
| `GET` | `/health` | Liveness check |
| `GET` | `/metrics` | Counts, success rate, throughput, average time-to-PR |
| `GET` | `/sessions` | Session records for the dashboard |
| `GET` | `/dashboard` | Auto-refreshing HTML dashboard |

Example `/metrics`:

```json
{
  "active": 1, "completed": 4, "failed": 1, "total": 6,
  "avg_completion_seconds": 1842.5,
  "success_rate_percent": 80.0,
  "throughput_per_hour": 0.12,
  "completed_last_hour": 2,
  "avg_time_to_pr_seconds": 1750.0,
  "poll_interval_seconds": 30,
  "max_concurrent_sessions": 5
}
```

* `success_rate_percent` — completed ÷ (completed + failed); `null` until a
  session finishes.
* `throughput_per_hour` — completed ÷ hours since the earliest record (floored
  at one hour).
* `avg_time_to_pr_seconds` — mean created→completed for sessions that produced
  a PR.
* Status mapping from Devin: `finished → completed`, `expired → failed`,
  `blocked → blocked`, anything else → `active`.

The dashboard leads with a colour-coded health banner (green ≥ 80 % success,
amber ≥ 50 %, red below) and four cards — issues processed, success rate,
average time to PR, sessions in progress — followed by the full session table.
It refreshes every 10 seconds.

## Structured logs

One JSON object per line, e.g.

```json
{"ts":"2026-09-02T17:11:22+00:00","level":"INFO","logger":"app.main","event":"session_created","message":"session_created","source":"manual","issue_number":43420,"session_id":"devin-…","repo":"PRad712/superset"}
{"ts":"2026-09-02T17:12:05+00:00","level":"INFO","logger":"app.poller","event":"session_status_transition","message":"session_status_transition","issue_number":43420,"session_id":"devin-…","old_status":"active","new_status":"completed","pr_url":"https://github.com/…/pull/1"}
```

Event names: `webhook_received`, `webhook_ignored`, `webhook_rejected`,
`session_created`, `session_duplicate`, `rate_limited` (concurrency limit),
`session_status_transition`, `poll_failed`.

## Tests and lint

```bash
ruff check .
pytest -q
```

CI (`.github/workflows/ci.yml`) runs both on every push and PR. Tests use
temporary SQLite databases, a stubbed Devin client, and `DISABLE_POLLER=1`.
Without a local Python toolchain:

```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim \
  sh -c "pip install -q -r requirements.txt -r requirements-dev.txt && ruff check . && pytest -q"
```

## Extending this for production

The current service is intentionally a single-repo, single-tenant proof of
concept. The following are the changes we would make first.

### Multi-repo support

* Replace the `SUPERSET_REPO` fallback with a **repository registry**
  (`repos` table or YAML: `full_name`, `default_branch`, `label`,
  `session_tag`, `max_concurrent`, `enabled`). The webhook handler already
  receives `repository.full_name`; look it up and reject unknown repos.
* Tag sessions with `repo:<owner>/<name>` in addition to the issue tag so the
  poller can list per repo and the dashboard can filter/group by repository.
* Add `repo_full_name` to `session_records` (today only the issue number is
  stored, so two repos sharing an issue number would collide in the dedupe
  check).
* Register one **GitHub App** instead of per-repo webhooks: a single
  installation covers every repository in the org, delivers the same `issues`
  events, and gives Devin an installation token scoped to the repos it may push
  to.
* Per-repo prompt templates (`build_remediation_prompt` is already a pure
  function) for stacks with different test/lint commands.

### Slack and Jira triggers

The HTTP layer already funnels every trigger through `start_remediation`, so
new sources are thin adapters:

* **Slack** — a slash command (`/devin-fix owner/repo#1234`) or an
  `app_mention` handler, verified with Slack's signing secret exactly as the
  GitHub HMAC is today, that fetches the issue title/body from the GitHub API
  and calls `start_remediation(source="slack")`. Post status transitions back
  to the originating thread from the poller (`session_status_transition`
  events already carry everything needed).
* **Jira** — a Jira Automation webhook on "issue transitioned to *Ready for
  Devin*" hitting `/webhook/jira`; map `issue.key` → a GitHub issue (via a
  custom field or by having Devin open the GitHub issue itself) and store the
  Jira key in `result` so the poller can transition the Jira ticket when the
  PR appears.
* Add a `source` column to `session_records` (currently only logged) so
  metrics can be split by trigger origin.

### Cost governance via `max_acu_limit`

Devin sessions are billed in ACUs. Today the only guard is
`MAX_CONCURRENT_SESSIONS`, which caps parallelism but not spend per session.

* Pass `max_acu_limit` on `POST /v1/sessions` (`create_remediation_session`
  already builds the payload; add the field from a new `MAX_ACU_PER_SESSION`
  setting, e.g. `10`). Devin stops the session when the cap is reached, so a
  runaway remediation cannot exceed a known cost.
* Track ACU consumption: the session object returned by `GET /v1/sessions`
  can be extended with usage data; persist it per record and expose
  `total_acus`, `avg_acus_per_pr`, and `cost_per_successful_pr` on `/metrics`
  and as a dashboard card.
* Add a **daily/weekly ACU budget** (`ACU_BUDGET_PER_DAY`): `start_remediation`
  returns `429` with a `budget_exhausted` reason once the rolling sum of
  completed-session ACUs plus in-flight caps exceeds it.
* Per-repo limits in the registry above, and an alert (`budget_warning`
  structured event → Slack) at 80 % of budget.
* Use Devin's `idempotent: true` flag (already sent) so webhook redeliveries
  never create duplicate billable sessions.

### Other hardening

* Persist to Postgres (the SQL is standard; `app/db.py` is the only touch
  point) and run the poller as a separate process for horizontal scaling.
* Authenticate `/dashboard`, `/metrics`, and `/sessions` (SSO proxy or bearer
  token) — they are unauthenticated today.
* Migrate `DevinClient` to the v3 organisation-scoped API before v1 is retired.
* Wire `poll_failed` and `rate_limited` events to alerting.

## License

MIT — see [LICENSE](LICENSE).
