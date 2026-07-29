import os

from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(url: str) -> str:
    """Rewrite the plain `postgres://`/`postgresql://` URLs that managed
    Postgres providers (Render, Supabase, Neon, Railway) hand out into the
    `postgresql+asyncpg://` form SQLAlchemy's async engine needs."""
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


DATABASE_URL = _normalize_database_url(
    os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./pj.db")
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Google service account used to call Calendar/Drive on behalf of every
# tenant. Set exactly one of these. The service account's email must be
# individually shared (as an editor) on each tenant's Google Calendar and
# Drive materials folder -- see README "Google service account setup".
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# Convenience defaults used only by seed.py when creating the first tenant
# from environment variables. Runtime webhook handling always uses the
# per-tenant channel_secret / channel_access_token stored in the database.
LINE_DEFAULT_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_DEFAULT_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

# --- Phase 4: background jobs ---------------------------------------------

# IANA timezone the 8:00 AM daily digest fires in, e.g. "Asia/Bangkok".
DIGEST_TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "UTC")

# A pending schedule request is "stale" once it's been unconfirmed this long.
STALE_REQUEST_HOURS = float(os.environ.get("STALE_REQUEST_HOURS", "24"))

# Whether the web process itself should run the APScheduler-based in-process
# scheduler (app/scheduler.py). Leave this off ("false", the default) when
# jobs are instead triggered by Render's native Cron Jobs / an external
# webhook (see run_job.py) or by a separate always-on worker process
# (worker.py) -- running it in more than one process/replica at once means
# every admin gets duplicate push messages.
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "false").lower() == "true"
