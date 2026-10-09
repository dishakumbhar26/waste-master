# WasteMaster 2.0

Construction waste marketplace: builders post pickups, admin-approved recyclers accept and complete them, and every completed job feeds impact analytics.

**Stack:** FastAPI · SQLModel/SQLAlchemy · Jinja2 · SQLite (dev) / PostgreSQL (prod) · ReportLab · qrcode · pytest

## Implemented
- Roles (builder / provider / admin), provider approval, session auth with PBKDF2 hashing, **CSRF protection on every POST**
- **State machine** (`services/workflow.py`): Open → Scheduled → Picked up → Completed; one function decides who may move a request and writes history, notifications and audit entries
- **Notifications** (in-app, unread badge, mark read / all, deduplicated by event key)
- **Audit log** (admin only): registration, approvals, request creation, accept, status changes, AI classification, manifests
- **QR tracking**: each request gets `WM-YYYY-000001`; `/track/{id}` is public and shows no private data; `/qr/{id}.png`
- **PDF waste manifests** for completed requests (participants and admin only)
- **AI waste classification** (`services/ai_classifier.py`): photo → Anthropic API → JSON validated with Pydantic. Works without a key; the user can always choose manually. Images are sent to the API and never stored.
- **Environmental factors** table + `services/impact_calculator.py`. Seeded values are **unverified placeholders** (empty `source`); the UI says "methodology under review" until a source is entered.
- **Analytics** (`/analytics`): SQL aggregation, filters by material, status and date range
- Swagger at `/docs`, ReDoc at `/redoc`; Dockerfile, docker-compose, GitHub Actions

## Run locally (SQLite)
```
pip install -r requirements.txt
cp .env.example .env        # works as-is for local development
uvicorn app.main:app --reload
pytest                      # SQLite;  TEST_DATABASE_URL=postgresql://... pytest  runs it on PostgreSQL
```
A blank or missing `DATABASE_URL` falls back to `sqlite:///wastemaster.db` locally. In production a blank `DATABASE_URL` or `SECRET_KEY` stops startup with a clear error.

## Admin account
- On startup, if `ADMIN_EMAIL` + `ADMIN_PASSWORD` (8+ chars) are set and no user has that email, an admin is created (email stored lowercase).
- If that admin already exists its password is **left unchanged**. To set a new password without touching data: set `ADMIN_PASSWORD` to the new value plus `ADMIN_RESET_PASSWORD=1` for one start, then remove `ADMIN_RESET_PASSWORD`. Or run `python -m app.manage make-admin you@example.com` (prompts for the password; also promotes an existing user).
- If the email belongs to a non-admin user, nobody is promoted automatically (a warning is logged).
- In production, startup fails if no admin exists and the variables are missing. Once an admin exists they are optional.

## AI classification setup
1. Put `ANTHROPIC_API_KEY=...` in `.env` (never commit it) and restart the server.
2. On *Request a pickup*, upload a JPEG/PNG/WebP (max 5 MB) and click *Classify photo*, then review the result and choose to use it or not.
3. Without a key, with a bad key, on timeout or on a malformed reply you get a specific message and manual selection still works. Confidence is shown only when the model supplies one and is not calibrated.
Status: tested with a mocked API; **not yet verified against the live API.**

## Database migrations (existing data is preserved)
- Existing `wastemaster.db` (created before migrations): just start the app. It is detected, adopted at revision `0001`, then upgraded. No data is dropped.
- **Back up first:** `cp wastemaster.db wastemaster.db.bak` (PostgreSQL: `pg_dump`).
- Manual: `alembic upgrade head` · rollback one step: `alembic downgrade -1` (restore the backup if a downgrade would lose data) · new change: edit `app/models.py`, then `alembic revision --autogenerate -m "describe"`.
- `0002` makes `wasteclassification.confidence` nullable (AI may give no confidence).

## Deploy to Render
Data lives in PostgreSQL, **not** on the web service's disk (which is ephemeral), so never run SQLite on Render.

**Blueprint (recommended):** push to GitHub → Render → *New → Blueprint* → pick the repo (uses `render.yaml`) → enter `ADMIN_EMAIL`, `ADMIN_PASSWORD` (and optionally `ANTHROPIC_API_KEY`) when prompted → deploy. `SECRET_KEY` is generated and `DATABASE_URL` is linked automatically.

**Manual:** create a PostgreSQL database, copy its *Internal* URL; create a Web Service (Python 3.11):
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`
- Health check path: `/healthz`
- Env: `ENVIRONMENT=production`, `DATABASE_URL`, `SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`), `ADMIN_EMAIL`, `ADMIN_PASSWORD`, optional `ANTHROPIC_API_KEY`. Use `?sslmode=require` if you connect with an *External* URL.

Migrations run automatically at startup (`alembic upgrade head`); run one web instance. Back up with `pg_dump` before upgrades. Check Render's current free-tier database limits before using it for real data. Existing local SQLite data is not copied to PostgreSQL by this release.

## Verification status
- **Tested here:** 37 tests on SQLite and on a local PostgreSQL 16 (full suite); migration `0001`→`0002` on PostgreSQL with existing rows preserved; production-mode server start on PostgreSQL; refusal to start without `SECRET_KEY`.
- **Not tested:** Render itself and `render.yaml` (not run on Render), the Docker image, Python 3.11 with the pinned versions (tested on 3.12; CI runs 3.11), HTTPS cookies behind Render's proxy, the live Anthropic API.

## Not built yet
Map and provider matching (needs coordinates), material-exchange upgrades, request/user search filters, provider performance page, admin user / factor management screens, provider rejection with reason, login rate limiting. PostgreSQL and Docker are configured but have not been run.
