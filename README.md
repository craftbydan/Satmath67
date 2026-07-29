# pj — LINE Webhook (Phase 1 + Phase 2 + Phase 3)

Multi-tenant LINE Messaging API backend for the "pj" SAT Math AI assistant.
Built on the official `line-bot-sdk` (v3), FastAPI, SQLModel (SQLAlchemy),
the OpenAI API, and `google-api-python-client`.

**Phase 1** verified webhook signatures and echoed messages back.
**Phase 2** added persistent multi-tenant storage, role-based system
prompts, thread-isolated chat history, and real LLM-generated replies.
**Phase 3** adds Google Calendar/Drive tool calling (availability lookup,
material search, slot booking, uploads, roster summary) with strict
role-based tool scoping between students and admins.

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py         # env vars: DATABASE_URL, OPENAI_*, GOOGLE_*, seed defaults
│   ├── db.py             # async SQLAlchemy engine/session, init_db() + light migration
│   ├── models.py         # Tenant, User, Conversation SQLModel tables
│   ├── crud.py           # tenant lookup, get_or_create_user, message history, roster
│   ├── tool_registry.py  # role -> tool schemas, and the tool dispatcher
│   ├── llm.py            # OpenAI call + role-based system prompts + tool-call loop
│   └── main.py           # FastAPI app + /webhook endpoint
├── tools/
│   ├── __init__.py
│   ├── google_auth.py    # shared Google service-account credentials/clients
│   ├── calendar.py       # check_teacher_availability, update_calendar_slot
│   └── drive.py          # search_student_materials, upload_material
├── seed.py                # CLI: init-db / add-tenant / set-admin / list
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
  per-tenant course material path), `google_calendar_id` (Phase 3),
  `google_drive_folder_id` (Phase 3).
- **users** — one row per `(tenant_id, line_user_id)`. `role` is
  `student` (default) or `admin`, `display_name` is optional.
- **conversations** — one row per chat message. `role` is `user`,
  `assistant`, or `system`; ordered by `created_at` and scoped to
  `(tenant_id, line_user_id)` so each user's thread is isolated even
  within the same tenant.

SQLite is the default (`DATABASE_URL=sqlite+aiosqlite:///./pj.db`); set
`DATABASE_URL` to a `postgresql+asyncpg://...` URL to use Postgres instead
— the SQLModel schema works unchanged against either.

There's no Alembic migration chain yet; `init_db()` (run on every startup
and by every `seed.py` command) creates any missing tables via
`SQLModel.metadata.create_all`, and separately `ALTER TABLE`s in
`google_calendar_id`/`google_drive_folder_id` on an existing `tenants`
table that predates Phase 3. Safe to run repeatedly.

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
| `update_calendar_slot` (book/cancel) | ❌ | ✅ | Calendar `events.insert`/`delete` |
| `upload_material` | ❌ | ✅ | Drive `files.create` |
| `get_student_summary` | ❌ | ✅ | Local DB roster, not Google |

Enforced twice: `tool_registry.tools_for_role()` only *offers* a user's
allowed tools to the model in the first place, and
`tool_registry.execute_tool()` independently re-checks role before running
anything — so even a model that hallucinates a call to a tool it wasn't
given is rejected server-side rather than executed.

`calendar_id`/`folder_id` are never LLM-supplied arguments; `execute_tool`
injects them from the calling tenant's own row, so a model can't be
tricked into touching another tenant's calendar or Drive folder.

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
3. Save the key locally, e.g. `secrets/google-service-account.json`
   (already covered by `.gitignore`), and set in `.env`:
   ```
   GOOGLE_SERVICE_ACCOUNT_FILE=./secrets/google-service-account.json
   ```
   Alternatively, for containerized deploys, paste the whole key JSON into
   `GOOGLE_SERVICE_ACCOUNT_JSON` as a single-line string instead — set only
   one of the two.
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
   *"What SAT Math slots are open this week?"* or *"Do you have a Practice
   Test 1 PDF?"*. Promote yourself with `seed.py set-admin` and try:
   *"Book a slot tomorrow at 10am"* or *"Give me a student summary"*.
   Server logs show each tool call:

   ```
   INFO:pj.webhook:Received message: tenant=U... user=U... role=student text='...'
   INFO:pj.llm:Tool call: tenant=U... role=student tool=search_student_materials args={'keywords': '...'}
   ```

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
