import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./pj.db")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Convenience defaults used only by seed.py when creating the first tenant
# from environment variables. Runtime webhook handling always uses the
# per-tenant channel_secret / channel_access_token stored in the database.
LINE_DEFAULT_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_DEFAULT_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
