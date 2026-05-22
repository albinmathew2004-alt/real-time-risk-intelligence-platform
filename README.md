# Real-Time Risk Intelligence Platform

FastAPI + PostgreSQL/SQLite + Redis + WebSockets + React/Vite demo platform for assessment integrity monitoring.

This branch is focused on Phase 2 demo stabilization:
- complete reviewer workflow
- preserve deterministic scoring and explainability
- keep SDK ingestion unauthenticated
- keep live dashboard and WebSocket updates working

## Current Scope

Included:
- JWT auth
- RBAC with `ADMIN`, `REVIEWER`, `VIEWER`
- browser telemetry ingestion
- deterministic risk engine with LOW/MEDIUM refinement
- live dashboard
- review queue
- investigation report
- reviewer case workflow and immutable action log

Explicitly not in scope for this sprint:
- AWS or cloud deployment
- multi-tenancy
- content-origin/plagiarism detection
- report export
- analytics expansion
- Phase 3 work

## Project Structure

```text
app/                FastAPI app, auth, reviewer workflow routes
engine/             DB models, scoring engine, Redis cache integration
frontend/           React + Vite reviewer console
scripts/            seed helpers and demo simulation
sdk/                browser telemetry SDK
```

## Setup

### 1. Create a virtual environment

```bash
python -m venv venv
```

### 2. Activate it

Windows:

```bash
venv\Scripts\activate
```

Linux/macOS:

```bash
source venv/bin/activate
```

### 3. Install backend dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

Copy `.env.example` into `.env` or export the variables in your shell.

Minimum required variables:

```env
JWT_SECRET_KEY=change-me
DEMO_ADMIN_EMAIL=admin@example.com
DEMO_ADMIN_PASSWORD=change-me-admin-password
DEMO_ADMIN_NAME=Demo Administrator
```

Notes:
- If `DATABASE_URL` is not set, local development falls back to `sqlite:///./risk_intel.db`.
- If `DATABASE_URL` is set to a PostgreSQL URL, the backend uses PostgreSQL as the primary runtime database.
- `REDIS_URL` defaults to `redis://localhost:6379/0` for local development and Docker Compose runs.
- `APP_MODE` defaults to `local-demo` and is surfaced in `/health` and `/health/deep`.
- `ENABLE_DOCS` defaults to `true` only in local/dev-style modes. In hosted demo mode, leave it unset or set it explicitly to `false`.
- `FRONTEND_URL` can be used to add the hosted reviewer console origin to the backend CORS allow-list.
- Local demo schema changes are handled with `create_all` plus a lightweight compatibility patch.
- Alembic is future work and is intentionally not introduced in this sprint.
- `CORS_ALLOW_ORIGINS` is optional and is used when you need to allow a public frontend or SDK origin during a tunnel-based demo.
- In hosted modes, `CORS_ALLOW_ORIGINS` must be explicit and cannot contain `*`.
- `PERSIST_DEMO_DATA=true` keeps enterprise demo seeding append-only unless you explicitly pass a reset flag.

### Runtime modes

#### SQLite mode (quick local development)

Do not set `DATABASE_URL`.

The backend will use:

```env
DATABASE_URL=sqlite:///./risk_intel.db
```

This mode is convenient for local UI iteration and demo data seeding, but it is not the recommended mode for higher candidate counts.

#### PostgreSQL + Redis mode (scalability foundation)

Set:

```env
DATABASE_URL=postgresql://risk_user:risk_password@localhost:5432/risk_db
REDIS_URL=redis://localhost:6379/0
APP_MODE=local-postgres-redis
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_RECYCLE_SECONDS=1800
```

This mode is the recommended local foundation for:
- higher ingest concurrency
- more realistic database behavior
- Redis-backed live attempt state
- load testing

## Start The Platform

### Backend

```bash
uvicorn app.main:app --reload
```

API docs:

```text
http://127.0.0.1:8000/docs
```

Health endpoints:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/health/deep
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Optional frontend env override:

```bash
copy .env.example .env
```

Set:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Frontend URL:

```text
http://127.0.0.1:5173
```

## Docker Compose

Docker Compose includes:
- `db` (PostgreSQL 16)
- `redis` (Redis 7)
- `api` (FastAPI backend)
- `frontend` (Vite dev server)

Start the local scalable stack:

```bash
docker compose up --build
```

Default local ports:
- Backend: `http://127.0.0.1:8000`
- Frontend: `http://127.0.0.1:5173`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

The compose file uses sane local defaults and environment-variable overrides for:
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET_KEY`
- `ENABLE_DOCS`
- `FRONTEND_URL`
- `CORS_ALLOW_ORIGINS`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `API_PORT`
- `FRONTEND_PORT`
- `POSTGRES_PORT`
- `REDIS_PORT`

## Seed Demo Users

All seed scripts are idempotent.

### Seed admin

```bash
python scripts/seed_admin.py
```

### Seed reviewer

```bash
python scripts/seed_reviewer.py
```

### Seed viewer

```bash
python scripts/seed_viewer.py
```

## Seed Persistent Enterprise Demo Data

Generate an operational demo dataset with realistic LOW / MEDIUM / HIGH attempts:

```bash
python scripts/seed_demo_dataset.py
```

Useful options:

```bash
python scripts/seed_demo_dataset.py --count 100 --seed 42
python scripts/seed_demo_dataset.py --reset --count 100 --seed 42
```

Behavior:
- uses deterministic scoring and existing telemetry profiles
- keeps data persistent across restarts when `PERSIST_DEMO_DATA=true`
- skips existing dataset attempt IDs safely on repeat runs
- only deletes dataset-prefixed attempts when `--reset` is explicitly provided

## Local Demo Credentials

- Admin: `admin@example.com` / `AdminPass123!`
- Reviewer: `reviewer@example.com` / `ReviewerPass123!`
- Viewer: `viewer@example.com` / `ViewerPass123!`

If you change the environment variables, use those values instead.

For controlled hosted demos, prefer seeding only the admin account with:
- `DEMO_ADMIN_EMAIL`
- `DEMO_ADMIN_PASSWORD`
- `DEMO_ADMIN_NAME`

## Reviewer Workflow APIs

Protected for `ADMIN` and `REVIEWER` only:
- `GET /v1/cases`
- `GET /v1/cases/{case_id}`
- `POST /v1/cases/{case_id}/assign`
- `POST /v1/cases/{case_id}/transition`
- `POST /v1/cases/{case_id}/notes`

Protected for logged-in reviewers and admins:
- `GET /v1/logs`
- `GET /v1/live-risk/{attempt_id}`
- `GET /v1/events/{attempt_id}`
- `GET /v1/risk-history/{attempt_id}`
- `GET /v1/reports/{attempt_id}`
- `GET /v1/dashboard/summary`
- `GET /v1/review-queue`

Intentionally unauthenticated:
- `POST /v1/events/ingest`
- `GET /`
- `GET /health`
- `GET /health/deep`
- `WS /ws/risk`

## Ingest runtime notes

`POST /v1/events/ingest` now:
- validates required fields and timestamp shape through the request model
- writes the raw event and risk snapshot in a single database transaction
- uses Redis-backed live event state when available
- falls back safely when Redis is unavailable

This is still a synchronous request path. Background workers and queue-based ingest are future work.

## Load testing ingest

Use the new script to simulate candidate traffic against `/v1/events/ingest`.

Examples:

Run the default progression:

```bash
python scripts/load_test_ingest.py --base-url http://127.0.0.1:8000
```

Run a single scenario:

```bash
python scripts/load_test_ingest.py --base-url http://127.0.0.1:8000 --candidates 500 --events-per-candidate 12 --concurrency 40
```

Supported options:
- `--base-url`
- `--candidates`
- `--events-per-candidate`
- `--concurrency`
- `--timeout`

The script reports:
- total events
- success count
- failure count
- events per second
- average latency
- p95 latency

Expected capacity note:
- This branch is now prepared for PostgreSQL + Redis-backed local load testing and small-scale concurrency experiments.
- It is not yet a horizontally scaled production architecture.
- Background workers, queueing, observability, and deployment segmentation remain future work.

## Case Lifecycle

- `NEW`
- `TRIAGED`
- `UNDER_INVESTIGATION`
- `ESCALATED`
- `CLEARED`
- `CONFIRMED_RISK`
- `FALSE_POSITIVE`
- `CLOSED`

Notes:
- cases are lazily created from existing attempts in raw events, risk history, and legacy attempt logs
- reviewer decisions are stored separately from system risk via `final_decision`
- reviewer actions are append-only
- closed cases are immutable in this branch

## 15-Minute Enterprise Demo Script

### Local demo API/URL model

- `localhost` and `127.0.0.1` only work on your machine.
- For a normal single-device demo, keep backend at `http://127.0.0.1:8000`.
- For the React console, set `frontend/.env` `VITE_API_BASE_URL` to the backend URL you want the browser to call.
- For the SDK example page, set the Base URL input to the same backend URL.

### 1. Start backend

```bash
uvicorn app.main:app --reload
```

### 2. Start frontend

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

For local demo, `frontend/.env` should contain:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 3. Seed admin

```bash
python scripts/seed_admin.py
```

### 4. Seed viewer

```bash
python scripts/seed_viewer.py
```

Optional but recommended for the exact reviewer role flow:

```bash
python scripts/seed_reviewer.py
```

### 5. Login credentials for local demo

- Admin: `admin@example.com` / `AdminPass123!`
- Reviewer: `reviewer@example.com` / `ReviewerPass123!`
- Viewer: `viewer@example.com` / `ViewerPass123!`

### 6. Run demo simulation script

Predictable four-profile run:

```bash
python scripts/simulate_live_exam.py --demo-pack --sleep-min 0.15 --sleep-max 0.35 --seed 42
```

This sends telemetry to `POST /v1/events/ingest` for:
- `NormalThoughtfulCandidate`
- `BorderlineCandidate`
- `AggressiveCheater`
- `TemplateCopier`

### 7. Open dashboard

Open:

```text
http://127.0.0.1:5173
```

Login as `ADMIN` or `REVIEWER`.

### 8. Open review queue

In the left navigation, open `Review Queue`.

Expected behavior:
- attempts appear after ingest
- case status is visible per row
- risk, score, latest activity, and event counts remain visible

### 9. Open investigation report

Select one of the higher-risk attempts, ideally:
- `demo_cheater_1003`
- `demo_template_1004`

Expected behavior:
- evidence remains visible
- risk history remains visible in its own report section
- case workflow card appears

### 10. Assign case

In the `Case Workflow` card, click `Assign to me`.

Expected behavior:
- case moves from `NEW` to `TRIAGED` if not already assigned
- assigned reviewer is shown
- immutable action history gets an `ASSIGN` entry

### 11. Add note

Enter a non-empty note and click `Add note`.

Expected behavior:
- empty notes are blocked
- note is appended to immutable action history
- case may move into `UNDER_INVESTIGATION`

### 12. Transition case

Use one of:
- `Escalate`
- `Clear`
- `Confirm risk`
- `Mark false positive`

Expected behavior:
- invalid transitions return clear `400` errors
- terminal reviewer decisions are stored separately from system risk
- action history shows previous and new state

### 13. Verify viewer cannot access reviewer workflow

Login as `viewer@example.com`.

Verify:
- `GET /v1/cases` returns `403`
- the frontend shows a clear workflow access restriction message

### 14. Verify ingest remains unauthenticated

Without any token, call:

```bash
curl -X POST http://127.0.0.1:8000/v1/events/ingest -H "Content-Type: application/json" -d "{\"attempt_id\":\"unauth_demo_1\",\"candidate_name\":\"Unauth Demo\",\"candidate_email\":\"unauth@example.com\",\"assessment_id\":\"assessment_python_01\",\"assessment_name\":\"Python Coding Assessment\",\"event_type\":\"exam_started\",\"payload\":{},\"occurred_at\":\"2026-01-01T10:00:00Z\"}"
```

Expected result:
- request succeeds without auth
- scoring and live updates continue to work

## Multi-Device Demo Support With A Tunnel

Use this when another person needs to open the assessment or console from a different device.

Important safety rules:
- `localhost` only works on your machine and cannot be shared directly.
- Use a temporary tunnel such as `ngrok` or `Cloudflare Tunnel`.
- The frontend URL must be shared with the reviewer/admin user.
- The backend API URL must be reachable by the browser SDK and by the frontend.
- Do not expose secrets publicly.
- Use demo/local-only credentials, not real enterprise credentials.
- Stop the tunnel as soon as the demo ends.

### Option A: ngrok

1. Run backend locally on port `8000`.
2. Run frontend locally on port `5173`.
3. Create a backend tunnel, for example:

```bash
ngrok http 8000
```

4. Create a frontend tunnel, for example:

```bash
ngrok http 5173
```

5. Set backend CORS to allow the public frontend and SDK origins:

```env
CORS_ALLOW_ORIGINS=https://your-frontend.ngrok-free.app,https://your-sdk-origin.example
```

6. In `frontend/.env`, point the console to the backend tunnel:

```env
VITE_API_BASE_URL=https://your-backend.ngrok-free.app
```

7. Restart the frontend after changing `frontend/.env`.
8. Share the frontend tunnel URL with the reviewer/admin demo user.
9. In `sdk/example-assessment.html`, set the Base URL input to the backend tunnel URL.

### Option B: Cloudflare Tunnel

1. Run backend locally on port `8000`.
2. Run frontend locally on port `5173`.
3. Create a backend tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

4. Create a frontend tunnel:

```bash
cloudflared tunnel --url http://localhost:5173
```

5. Add the public frontend origin to `CORS_ALLOW_ORIGINS`.
6. Set `frontend/.env` `VITE_API_BASE_URL` to the public backend tunnel URL.
7. Use the public backend tunnel URL in the SDK example page.

### Multi-device run checklist

1. Backend is running locally.
2. Frontend is running locally.
3. Backend tunnel is live.
4. Frontend tunnel is live.
5. `CORS_ALLOW_ORIGINS` includes the shared frontend or SDK origin.
6. `VITE_API_BASE_URL` points to the backend tunnel URL.
7. Reviewers open the frontend tunnel URL.
8. Candidates use an assessment page whose SDK `baseUrl` points to the backend tunnel URL.
9. After the demo, shut down both tunnels.

## Scaling Path To 1000 Simultaneous Candidates

This is the architecture path only. It is not fully implemented in this sprint.

- Run FastAPI as a deployed multi-instance service behind a load balancer instead of a single local process.
- Keep PostgreSQL as the durable system of record and add indexes for high-volume lookups such as `attempt_id`, `received_at`, `timestamp`, and case workflow fields.
- Continue using Redis for live state and hot-path event aggregation.
- Move WebSocket fan-out behind a shared pub/sub or broker-backed scaling layer so multiple app instances can broadcast consistent updates.
- Add background workers for non-request-path tasks such as reconciliation, cleanup, enrichment, and async notification workflows.
- Introduce realistic load testing for ingest, dashboard refresh, WebSocket fan-out, and reviewer workflow traffic before claiming concurrency targets.
- Add observability across API latency, queue depth, Redis health, PostgreSQL slow queries, WebSocket connection counts, and ingest error rates.
- Scale horizontally by separating ingest/API workers, WebSocket workers, and background workers as independent deployable units.

## Phase 2 Manual Verification Checklist

### Backend

1. `python -m compileall app scripts`
2. Start backend successfully with `uvicorn app.main:app --reload`
3. `POST /v1/auth/login` returns an access token
4. `GET /v1/auth/me` returns the logged-in user
5. `GET /v1/cases` returns data for `ADMIN`
6. `GET /v1/cases` returns data for `REVIEWER`
7. `GET /v1/cases` returns `403` for `VIEWER`
8. `GET /v1/cases` returns `401` without auth
9. `POST /v1/events/ingest` still works without auth
10. `POST /v1/cases/{id}/assign` updates assignment and logs action
11. `POST /v1/cases/{id}/notes` appends a note
12. empty notes are rejected
13. `POST /v1/cases/{id}/transition` supports `ESCALATED`, `CLEARED`, `CONFIRMED_RISK`, `FALSE_POSITIVE`
14. case detail returns reviewer action history

### Frontend

1. `cd frontend && npm install`
2. `cd frontend && npm run build`
3. login screen still works
4. dashboard loads
5. review queue loads
6. investigation report loads
7. case workflow card does not crash
8. action history renders when available
9. `401` expires the session cleanly
10. `403` shows a clear workflow restriction message

## Known Limitations

- This sprint intentionally keeps the local `create_all` approach for demo speed; Alembic migrations are still future work.
- Reviewer actions are immutable, but there is no admin-only reopen flow yet.
- WebSocket access remains unauthenticated to avoid breaking the current live demo path.
- The frontend is optimized for a controlled demo, not full multi-user case collaboration.
- Local SQLite is supported for demo convenience; PostgreSQL remains the preferred path outside the demo setup.

## Controlled Demo Hosting Guide

This platform is ready for a controlled online demo or pilot. It is not yet a public SaaS product.

### Intended hosting shape

- hosted frontend
- hosted FastAPI backend
- PostgreSQL database
- Redis if available
- HTTPS on both frontend and backend
- seeded admin-only demo login
- persistent demo dataset
- candidate assessment page using the SDK example or a thin custom wrapper

### Required environment variables

Backend:

```env
DATABASE_URL=postgresql://risk_user:risk_password@db-host:5432/risk_db
REDIS_URL=redis://redis-host:6379/0
JWT_SECRET_KEY=replace-with-a-strong-secret
CORS_ALLOW_ORIGINS=https://your-frontend.example.com,https://your-sdk-origin.example.com
FRONTEND_URL=https://your-frontend.example.com
APP_MODE=controlled-demo
ENABLE_DOCS=false
PERSIST_DEMO_DATA=true
DEMO_ADMIN_EMAIL=admin@example.com
DEMO_ADMIN_PASSWORD=replace-with-a-strong-password
DEMO_ADMIN_NAME=Demo Administrator
```

Frontend:

```env
VITE_API_BASE_URL=https://your-backend.example.com
```

### Backend deployment

#### Option A: Render / Railway style deployment

1. Provision PostgreSQL.
2. Provision Redis if available.
3. Deploy the repo as a Python web service.
4. Set the backend environment variables listed above.
5. Start the API with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

6. Seed the admin:

```bash
python scripts/seed_admin.py
```

7. Seed the persistent demo dataset:

```bash
python scripts/seed_demo_dataset.py --count 100 --seed 42
```

#### Option B: Docker Compose on a VPS

1. Copy `.env.example` to `.env`.
2. Replace the demo defaults with strong values.
3. Set:

```env
APP_MODE=controlled-demo
ENABLE_DOCS=false
JWT_SECRET_KEY=replace-with-a-strong-secret
FRONTEND_URL=https://your-frontend.example.com
CORS_ALLOW_ORIGINS=https://your-frontend.example.com,https://your-sdk-origin.example.com
VITE_API_BASE_URL=https://your-backend.example.com
```

4. Start:

```bash
docker compose up --build -d
```

5. Seed:

```bash
python scripts/seed_admin.py
python scripts/seed_demo_dataset.py --count 100 --seed 42
```

### Frontend deployment

Build command:

```bash
cd frontend
npm install
npm run build
```

The reviewer console must be configured with:

```env
VITE_API_BASE_URL=https://your-backend.example.com
```

You can host the built `frontend/dist` on any static host that supports HTTPS.

#### Render SPA routing

If you deploy the frontend as a Render Static Site, configure a rewrite so client-side routes are served by `index.html`.

This repo now ships `frontend/public/_redirects` with:

```text
/* /index.html 200
```

For an existing manually configured Render Static Site, also verify the rewrite rule in the Render dashboard:

- Source: `/*`
- Destination: `/index.html`
- Action: `Rewrite`

That ensures direct navigation and refresh both work for SPA paths such as:

- `/`
- `/demo`
- `/review`
- `/cases/123`

### Candidate SDK / assessment link

The SDK and example assessment already support:
- configurable backend base URL
- configurable `attemptId`
- configurable candidate metadata
- configurable `assessmentId`
- configurable `assessmentName`

Example hosted URL:

```text
https://your-sdk-host.example.com/example-assessment.html?baseUrl=https://your-backend.example.com&attemptId=demo_hosted_1001&candidateName=Hosted%20Demo%20Candidate&candidateEmail=hosted.demo@example.com&assessmentId=assessment_python_01&assessmentName=Python%20Coding%20Assessment
```

### Demo startup checklist

1. Set a strong `JWT_SECRET_KEY`.
2. Set `APP_MODE=controlled-demo`.
3. Set `ENABLE_DOCS=false`.
4. Set `FRONTEND_URL`.
5. Set explicit `CORS_ALLOW_ORIGINS`.
6. Seed the admin account.
7. Seed the persistent demo dataset.
8. Verify backend health endpoints.
9. Verify frontend login.
10. Verify the Review Queue and Investigation Report render with seeded data.

### Post-deployment verification checklist

Backend:
- `GET /health`
- `GET /health/deep`
- `POST /v1/auth/login` with the seeded admin

Frontend:
- login works
- Command Center loads
- Review Queue loads
- Investigation Report loads
- PDF export works if used

SDK:
- `POST /v1/events/ingest` accepts telemetry from the hosted assessment page
- new attempts appear in the reviewer console

### Security warnings

- This is not a public self-serve SaaS yet.
- Do not enable public signup.
- Do not expose default demo passwords online.
- Do not use the system for real high-stakes exam decisions yet.
- Show a candidate privacy/consent notice before external pilot use.
- Prefer admin-only hosted demo access unless you explicitly need reviewer/viewer roles.

### Remaining production gaps

- Alembic migrations are still future work.
- WebSocket auth is still not hardened for internet-facing multi-user production use.
- Background workers and durable async ingest are not implemented yet.
- Full observability, alerting, and audit export are still future work.
- This is suitable for controlled demos and pilots, not broad public internet launch.

## Future Work Not Implemented

- Alembic migrations
- admin-only reopen flow for closed cases
- richer reviewer assignment policies and SLA tracking
- audit export/report export
- production deployment and infrastructure hardening
- multi-tenant data isolation
