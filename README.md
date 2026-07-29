# pj — LINE Webhook (Phase 1–4)

Multi-tenant LINE Messaging API backend for the "pj" SAT Math AI assistant.
Built on the official `line-bot-sdk` (v3), FastAPI, SQLModel (SQLAlchemy),
the OpenAI API, `google-api-python-client`, and APScheduler.

**Phase 1** verified webhook signatures and echoed messages back.
**Phase 2** added persistent multi-tenant storage, role-based system
prompts, thread-isolated chat history, and real LLM-generated replies.
**Phase 3** added Google Calendar/Drive tool calling (availability lookup,
material search, slot booking, uploads, roster summary) with strict
role-based tool scoping between students and admins.
**Phase 4** makes it deployable for 24/7 operation: a Dockerfile/`render.yaml`
for one-click hosting, a production Postgres connection, and background
jobs for a daily admin schedule digest and stale-session-request reminders.

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py         # env vars: DATABASE_URL, OPENAI_*, GOOGLE_*, scheduler settings
│   ├── db.py             # async SQLAlchemy engine/session, init_db() + light migration
│   ├── models.py         # Tenant, User, Conversation, ScheduleRequest tables
│   ├── crud.py           # tenant lookup, get_or_create_user, message history, roster
│   ├── tool_registry.py  # role -> tool schemas, and the tool dispatcher
│   ├── llm.py            # OpenAI call + role-based system prompts + tool-call loop
│   ├── scheduler.py      # Phase 4 job bodies + APScheduler wiring
│   └── main.py           # FastAPI app + /webhook + /health
├── tools/
│   ├── __init__.py
│   ├── google_auth.py    # shared Google service-account credentials/clients
│   ├── calendar.py       # check_teacher_availability, update_calendar_slot, list_events_for_date
│   └── drive.py          # search_student_materials, upload_material
├── seed.py                # CLI: init-db / add-tenant / set-admin / list
├── run_job.py              # one-shot job runner, for Render Cron Jobs / external cron
├── worker.py                # always-on job runner, for platforms without native cron
├── Dockerfile
├── .dockerignore
├── render.yaml              # Render Blueprint: web service + Postgres + 2 Cron Jobs
├── Procfile                 # Railway/Heroku-style process types (web, worker)
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Database schema

- **tenants** — one row per LINE Official Account.
  `id` = LINE `destination` ID, `name`, `channel_access_token`,
  `channel_secret` (needed to verify that tenant's webhook signature —
  not in the original spec's field list, but required once more than one
  Official Account shares this service), `folder_path` (placeholder for
  per-tenant course material path), `google_calendar_id`,
  `google_drive_folder_id`.
- **users** — one row per `(tenant_id, line_user_id)`. `role` is
  `student` (default) or `admin`, `display_name` is optional.
- **conversations** — one row per chat message. `role` is `user`,
  `assistant`, or `system`; ordered by `created_at` and scoped to
  `(tenant_id, line_user_id)` so each user's thread is isolated even
  within the same tenant.
- **schedule_requests** (Phase 4) — a student's requested session time,
  logged via the `request_session_time` tool and awaiting admin action via
  `resolve_schedule_request`. This is what the stale-session-reminder job
  scans (`status == pending` and older than `STALE_REQUEST_HOURS`).
  Students can't book the calendar directly (see role scoping below); this
  table is what makes "detect unconfirmed schedule requests" mean
  something concrete rather than being a no-op job.

SQLite is the default (`DATABASE_URL=sqlite+aiosqlite:///./pj.db`) for
local dev. In production, point `DATABASE_URL` at a Postgres instance
(Render Postgres, Supabase, Neon, ...) — `app/config.py` automatically
rewrites the `postgres://`/`postgresql://` URLs those providers hand out
into the `postgresql+asyncpg://` form SQLAlchemy's async engine needs, so
you can paste their connection string in as-is.

There's no Alembic migration chain yet; `init_db()` (run on every startup,
by every `seed.py` command, and by `run_job.py`/`worker.py`) creates any
missing tables via `SQLModel.metadata.create_all`, and separately
`ALTER TABLE`s in `google_calendar_id`/`google_drive_folder_id` on an
existing `tenants` table from before Phase 3. Safe to run repeatedly, on
SQLite or Postgres.

## Webhook flow (`/webhook`)

1. Read `destination` out of the raw JSON body (before signature
   verification, since we need it to know *which* tenant's secret to
   verify against).
2. Look up the tenant by `destination`. If it's not a known tenant, log a
   warning and return `200 OK` without further processing (LINE resends
   messages that don't get a fast 200 response).
3. Verify `X-Line-Signature` using that tenant's `channel_secret`.
4. For each text message event: look up or auto-register the
   `(tenant_id, line_user_id)` user (defaults to role `student`), save the
   incoming message, fetch that user's last 10 messages, and call
   `llm.generate_reply()` with a system prompt selected by role.
5. `generate_reply()` runs a multi-turn tool-calling loop against OpenAI:
   the model is offered only the tools allowed for the caller's role (see
   below); if it calls one, we execute it server-side against that
   tenant's `google_calendar_id`/`google_drive_folder_id`, feed the JSON
   result back as a `tool` message, and let the model produce pj's final
   natural-language reply (capped at 4 model↔tool round trips).
6. The reply is saved to `conversations` and sent back via `reply_message`
   using the tenant's own `channel_access_token`.

## Role-based tool scoping

| Tool | Student | Admin | Backing |
|---|---|---|---|
| `check_teacher_availability` | ✅ (read-only) | ✅ | Calendar `freebusy.query` |
| `search_student_materials` | ✅ (read-only) | ✅ | Drive `files.list` |
| `request_session_time` | ✅ | — | Local DB (`schedule_requests`, status `pending`) |
| `update_calendar_slot` (book/cancel) | ❌ | ✅ | Calendar `events.insert`/`delete` |
| `upload_material` | ❌ | ✅ | Drive `files.create` |
| `get_student_summary` | ❌ | ✅ | Local DB roster |
| `list_pending_requests` | ❌ | ✅ | Local DB (`schedule_requests`) |
| `resolve_schedule_request` | ❌ | ✅ | Local DB, marks confirmed/cancelled |

Enforced twice: `tool_registry.tools_for_role()` only *offers* a user's
allowed tools to the model in the first place, and
`tool_registry.execute_tool()` independently re-checks role before running
anything — so even a model that hallucinates a call to a tool it wasn't
given is rejected server-side rather than executed.

`calendar_id`/`folder_id` are never LLM-supplied arguments; `execute_tool`
injects them from the calling tenant's own row, so a model can't be
tricked into touching another tenant's calendar or Drive folder.

## Background jobs (Phase 4)

Two jobs, defined once in `app/scheduler.py` and runnable two ways:

- **Daily admin digest** (`send_daily_digest`) — 8:00 AM (`DIGEST_TIMEZONE`):
  for every tenant with a `google_calendar_id`, lists that day's calendar
  events and pushes a summary to every `role == admin` user for that
  tenant via LINE's push-message API (not reply — there's no reply token
  in a background job).
- **Stale session reminder** (`check_stale_requests`) — hourly: for every
  tenant, finds `schedule_requests` still `pending` after
  `STALE_REQUEST_HOURS` (default 24) and pushes a nudge listing them to
  that tenant's admins.

**Two ways to run them, pick one per deployment:**

1. **One-shot via `run_job.py`** (recommended on Render) — each invocation
   runs a single job and exits; the platform's own scheduler (Render's
   native Cron Job service type) owns timing. No always-on process, no
   risk of duplicate pushes even if the web service scales to multiple
   instances, since nothing about the job's execution is tied to a
   particular web replica.
   ```bash
   python run_job.py daily-digest
   python run_job.py stale-reminder
   ```
2. **Always-on via `worker.py`** — for platforms without a native
   cron-job primitive (Railway, Fly.io, a bare VPS): starts an
   `APScheduler` `AsyncIOScheduler` and blocks forever, firing the same
   two job functions on the same schedule. **Run this in exactly one
   process at a time** — two copies means every admin gets duplicate
   pushes. It can also be embedded directly into the web process by
   setting `ENABLE_SCHEDULER=true` (see `app/main.py`), which is fine
   *only* if that web service is a single, unscaled instance.

`render.yaml` uses option 1. `Procfile`'s `worker` line uses option 2 for
Railway-style platforms.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in OPENAI_API_KEY, GOOGLE_SERVICE_ACCOUNT_FILE (see below), and
# (optionally) LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN as a
# convenience default for your first tenant
```

### Google service account setup

The Calendar/Drive tools authenticate as a single shared **Google service
account** — no per-user OAuth flow, since pj acts on the tutor's calendar
and shared materials folder, not an individual student's own Google
account.

1. In [Google Cloud Console](https://console.cloud.google.com/), create (or
   pick) a project, then enable the **Google Calendar API** and
   **Google Drive API**.
2. Create a service account (IAM & Admin -> Service Accounts), then create
   and download a JSON key for it.
3. Locally, save the key as e.g. `secrets/google-service-account.json`
   (already covered by `.gitignore`) and set in `.env`:
   ```
   GOOGLE_SERVICE_ACCOUNT_FILE=./secrets/google-service-account.json
   ```
   In production (Render, etc.) there's no persistent local disk to put a
   key file on, so instead paste the **entire key JSON** as a single-line
   string into the `GOOGLE_SERVICE_ACCOUNT_JSON` environment variable —
   set only one of the two, `_JSON` wins if both happen to be set.
4. Note the service account's `client_email` (looks like
   `pj-bot@your-project.iam.gserviceaccount.com`).
5. **Per tenant**, share that tenant's tutor Google Calendar and Drive
   materials folder with the service account's email:
   - Calendar: Google Calendar -> Settings -> that calendar -> "Share with
     specific people" -> add the service account email, "Make changes to
     events" (needed for `update_calendar_slot`).
   - Drive folder: right-click the folder -> Share -> add the service
     account email as **Editor** (needed for `upload_material`).
6. Seed the tenant's IDs (see below) — the Calendar ID is usually the
   tutor's Gmail address (or find it under that calendar's Settings ->
   "Integrate calendar" -> Calendar ID); the Drive folder ID is the
   `.../folders/<this-part>` segment of the folder's URL.

## Seed a tenant and (optionally) an admin user

```bash
# Create/upgrade tables
python seed.py init-db

# Register a tenant — id must be the LINE `destination` value for that
# Official Account (found in webhook payloads, or the OA's basic ID/user ID)
python seed.py add-tenant \
  --id Udestination000000000000000000 \
  --name "Friend 1 Math Group" \
  --channel-secret <that OA's channel secret> \
  --channel-access-token <that OA's channel access token> \
  --folder-path ./materials/friend1 \
  --google-calendar-id teacher@gmail.com \
  --google-drive-folder-id 1AbCdEfGhIjKlMnOpQrStUvWxYz

# ...or reuse LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN from .env:
python seed.py add-tenant --id Udestination000000000000000000 \
  --name "Friend 1 Math Group" --from-env

# Promote an existing (or not-yet-seen) LINE user to admin for that tenant
python seed.py set-admin \
  --tenant-id Udestination000000000000000000 \
  --line-user-id Uxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Inspect what's in the database
python seed.py list
```

Re-running `add-tenant` for an existing tenant only overwrites
`google_calendar_id`/`google_drive_folder_id`/`folder_path` if you pass
those flags again — omitting them leaves the previously-set values alone.

## Run locally

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health` -> `{"status": "ok"}`
(also pings the DB, so it fails with 503 if the database is unreachable —
useful for uptime monitors to catch that failure mode specifically).

## Expose locally with ngrok and register the webhook

1. Start the server (above), then in another terminal:

   ```bash
   ngrok http 8000
   ```

2. Copy the `https://<random>.ngrok-free.app` URL ngrok prints.

3. In the LINE Developers Console, open your channel -> **Messaging API**
   tab -> **Webhook settings**:
   - Webhook URL: `https://<random>.ngrok-free.app/webhook`
   - Click **Verify**
   - Turn **Use webhook** ON
   - Turn OFF **Auto-reply messages** and **Greeting messages** in
     Response settings so they don't interfere with pj's replies

4. Make sure you've run `seed.py add-tenant` for that channel's
   `destination` ID first (with the Google Calendar/Drive IDs set, if you
   want to test tool calling) — messages from unregistered tenants are
   logged and dropped with a 200 response, not answered.

5. Add the bot as a friend and send it a text message. Try, as a student:
   *"What SAT Math slots are open this week?"*, *"Do you have a Practice
   Test 1 PDF?"*, or *"I'd like a session Monday at 3pm"* (logs a pending
   request). Promote yourself with `seed.py set-admin` and try: *"Book a
   slot tomorrow at 10am"*, *"Give me a student summary"*, or *"Any
   pending session requests?"*. Server logs show each tool call:

   ```
   INFO:pj.webhook:Received message: tenant=U... user=U... role=student text='...'
   INFO:pj.llm:Tool call: tenant=U... role=student tool=search_student_materials args={'keywords': '...'}
   ```

---

## Deploying to production (Render)

This is the "one-click"-ish path: `render.yaml` is a
[Render Blueprint](https://render.com/docs/blueprint-spec) that provisions
everything below in one apply.

### 1. Set up the Render account and connect the repo

1. Create a account at [render.com](https://render.com) and connect your
   GitHub/GitLab account.
2. Push this repo to your own GitHub/GitLab remote (Render deploys from a
   connected repo, not a local `git push`).
3. In the Render dashboard: **New -> Blueprint**, pick this repo. Render
   reads `render.yaml` and shows you the resources it's about to create:
   - `pj-postgres` — a managed Postgres database (free plan by default;
     bump `plan:` in `render.yaml` for production traffic/retention).
   - `pj-web` — the FastAPI service, built from `Dockerfile`, with
     `healthCheckPath: /health` wired in so Render restarts it on
     repeated health-check failures.
   - `pj-digest` / `pj-stale` — native Render Cron Jobs running
     `run_job.py daily-digest` / `run_job.py stale-reminder` on the
     schedules in `render.yaml` (`0 8 * * *` and `0 * * * *`, both UTC —
     see the timezone note below).
4. Click **Apply**. Render provisions Postgres first, then builds and
   deploys `pj-web` and the two cron jobs.

### 2. Fill in secrets

`render.yaml` marks the following `sync: false`, meaning Render creates
the env var slots but leaves them blank for you to fill in via the
dashboard (Service -> Environment) rather than committing them to the
repo:

| Variable | Where to get it |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full service-account key JSON, single-line (see "Google service account setup" above) |
| `LINE_CHANNEL_SECRET` / `LINE_CHANNEL_ACCESS_TOKEN` | Only needed here as a convenience default for `seed.py add-tenant --from-env` if you run it against production from your machine; not required at webhook-serving runtime, which reads per-tenant values from the DB |

`DATABASE_URL` is wired automatically via `fromDatabase: pj-postgres` —
you don't set it by hand.

Set `DIGEST_TIMEZONE` (e.g. `Asia/Bangkok`) on **both** `pj-web` and
`pj-digest` if you want the digest's *content* (which day's events count
as "today") to reflect the tutor's local day; the cron *trigger time*
itself (`schedule: "0 8 * * *"`) is evaluated by Render in UTC, so adjust
that cron expression to the UTC-equivalent of your desired 8:00 AM local
time (e.g. `"0 1 * * *"` for 8:00 AM ICT/UTC+7).

### 3. Seed your first tenant against the production database

From your machine, point `seed.py` at the production `DATABASE_URL` (copy
it from the Render Postgres dashboard's "External Connection String" —
note it may need `?ssl=require` appended, or use `PGSSLMODE=require`):

```bash
DATABASE_URL="postgresql://...render-external-connection-string..." \
  python seed.py add-tenant \
    --id Udestination000000000000000000 \
    --name "Friend 1 Math Group" \
    --channel-secret <...> --channel-access-token <...> \
    --google-calendar-id teacher@gmail.com \
    --google-drive-folder-id 1AbCdEfGhIjKlMnOpQrStUvWxYz

DATABASE_URL="postgresql://...:" python seed.py set-admin \
  --tenant-id Udestination000000000000000000 --line-user-id Uxxxx...
```

### 4. Point the LINE webhook at your live URL

In the LINE Developers Console, for that channel -> **Messaging API** ->
**Webhook settings**:

- Webhook URL: `https://<your-render-service>.onrender.com/webhook`
  (Render shows the exact `pj-web` URL on its service page)
- Click **Verify** (should succeed once secrets are filled in and the
  service has finished deploying)
- **Use webhook**: ON
- Turn OFF **Auto-reply messages** / **Greeting messages** in Response
  settings

Repeat steps 3-4 per additional tenant (Official Account) — the webhook
URL is the same for all tenants; `destination` in each payload routes to
the right `tenants` row.

### 5. Keep it awake / monitor it

Render's paid plans don't sleep; if you're on a free-tier equivalent that
does spin down on idle, point an uptime monitor at `GET /health` on an
interval shorter than the platform's idle timeout:

- [UptimeRobot](https://uptimerobot.com) or
  [Better Stack](https://betterstack.com/uptime) — add an HTTPS monitor
  for `https://<your-app>.onrender.com/health`, checked every 5 minutes,
  alerting on non-200 (the health check itself fails with 503 if the DB
  is unreachable, so this also catches DB outages).

### Production deployment checklist

- [ ] Repo pushed to GitHub/GitLab and connected in Render
- [ ] Blueprint applied (`pj-postgres`, `pj-web`, `pj-digest`, `pj-stale`
      all show as deployed/succeeded in the Render dashboard)
- [ ] `OPENAI_API_KEY` set on `pj-web` (and `pj-digest`/`pj-stale` if they
      ever call the LLM — currently they don't, only Calendar/LINE)
- [ ] `GOOGLE_SERVICE_ACCOUNT_JSON` set on `pj-web`, `pj-digest`, `pj-stale`
- [ ] Google service account's calendar/Drive folder sharing done per tenant
- [ ] `GET https://<app>.onrender.com/health` returns `200 {"status":"ok"}`
- [ ] At least one tenant seeded against the production `DATABASE_URL`
      (`seed.py list` against it shows it)
- [ ] LINE webhook URL updated to the production `/webhook` URL and
      **Verify** succeeds in the LINE console
- [ ] Sent a real LINE message end-to-end and got pj's reply
- [ ] Confirmed `pj-digest`/`pj-stale` Cron Jobs show successful recent
      runs in the Render dashboard (Logs tab) — or trigger one manually
      via Render's "Trigger Run" button to check sooner than the schedule
- [ ] Uptime monitor configured against `/health`
- [ ] `ENABLE_SCHEDULER` is `false` on `pj-web` (Cron Jobs own scheduling
      on Render — don't double up)

## Deploying elsewhere (Railway / other Docker hosts)

The same `Dockerfile` works anywhere that runs Docker images. Without a
native cron-job primitive:

1. Deploy the image as your `web` service (Railway/Heroku-style
   `Procfile`'s `web:` line, or the Dockerfile's default `CMD`).
2. Deploy a **second** service from the same image/repo, overriding its
   start command to `python worker.py` (`Procfile`'s `worker:` line) —
   this is the always-on APScheduler process from "Background jobs"
   above. Run exactly one instance of it.
3. Point `DATABASE_URL` at a Postgres add-on (Railway Postgres, Neon,
   Supabase, ...) on both services; `app/config.py` normalizes whichever
   connection-string scheme they hand you.
4. Same LINE webhook / Google service account steps as the Render guide
   above, using that platform's public URL instead.

## Notes for later phases

- `search_student_materials`/`upload_material` only search/write by
  filename match and plain text content respectively — no OCR or PDF
  content indexing yet.
- Calendar availability assumes a fixed 9am-6pm UTC business-hours window
  (`tools/calendar.py`); making that per-tenant/timezone-aware is future
  work.
- Signature verification uses `WebhookParser` from `line-bot-sdk`, keyed
  per-tenant by `channel_secret`, validating `X-Line-Signature` via
  HMAC-SHA256 per the official LINE spec.
- No Alembic migration chain (see "Database schema" above) -- fine at this
  scale, but worth adopting before the schema churns much more.
