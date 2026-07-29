# pj — LINE Webhook (Phase 1 + Phase 2)

Multi-tenant LINE Messaging API backend for the "pj" SAT Math AI assistant.
Built on the official `line-bot-sdk` (v3), FastAPI, SQLModel (SQLAlchemy),
and the OpenAI API.

**Phase 1** verified webhook signatures and echoed messages back.
**Phase 2** adds persistent multi-tenant storage, role-based system
prompts, thread-isolated chat history, and real LLM-generated replies.

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py      # env vars: DATABASE_URL, OPENAI_*, seed defaults
│   ├── db.py           # async SQLAlchemy engine/session, init_db()
│   ├── models.py       # Tenant, User, Conversation SQLModel tables
│   ├── crud.py         # tenant lookup, get_or_create_user, message history
│   ├── llm.py           # OpenAI call + role-based system prompts
│   └── main.py          # FastAPI app + /webhook endpoint
├── seed.py             # CLI: init-db / add-tenant / set-admin / list
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
  per-tenant course material).
- **users** — one row per `(tenant_id, line_user_id)`. `role` is
  `student` (default) or `admin`, `display_name` is optional.
- **conversations** — one row per chat message. `role` is `user`,
  `assistant`, or `system`; ordered by `created_at` and scoped to
  `(tenant_id, line_user_id)` so each user's thread is isolated even
  within the same tenant.

SQLite is the default (`DATABASE_URL=sqlite+aiosqlite:///./pj.db`); set
`DATABASE_URL` to a `postgresql+asyncpg://...` URL to use Postgres instead
— the SQLModel schema works unchanged against either.

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
   incoming message, fetch that user's last 10 messages, call the LLM with
   a system prompt selected by role, save the assistant's reply, and send
   it back via `reply_message` using the tenant's own
   `channel_access_token`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in OPENAI_API_KEY, and (optionally) LINE_CHANNEL_SECRET /
# LINE_CHANNEL_ACCESS_TOKEN as a convenience default for your first tenant
```

## Seed a tenant and (optionally) an admin user

```bash
# Create tables
python seed.py init-db

# Register a tenant — id must be the LINE `destination` value for that
# Official Account (found in webhook payloads, or the OA's basic ID/user ID)
python seed.py add-tenant \
  --id Udestination000000000000000000 \
  --name "Friend 1 Math Group" \
  --channel-secret <that OA's channel secret> \
  --channel-access-token <that OA's channel access token> \
  --folder-path ./materials/friend1

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
   `destination` ID first — messages from unregistered tenants are logged
   and dropped with a 200 response, not answered.

5. Add the bot as a friend and send it a text message. pj should reply
   using the system prompt for your role (student by default; use
   `seed.py set-admin` to test the admin prompt). Server logs show:

   ```
   INFO:pj.webhook:Received message: tenant=U... user=U... role=student text='...'
   ```

## Notes for later phases

- `folder_path` on `tenants` is a placeholder for per-tenant course
  material lookup (e.g. RAG over a tenant's own SAT Math materials) —
  not yet wired into `llm.py`.
- Signature verification uses `WebhookParser` from `line-bot-sdk`, keyed
  per-tenant by `channel_secret`, validating `X-Line-Signature` via
  HMAC-SHA256 per the official LINE spec.
