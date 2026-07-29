"""Google service-account auth shared by the Calendar and Drive tool handlers.

One service account backs every tenant. Its email
(`client_email` in the key JSON) must be shared as an editor/collaborator
on each tenant's Google Calendar and Drive materials folder individually --
that per-resource share is what actually scopes access to that tenant's
data, not anything in this module.
"""

from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build

from app.config import GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SERVICE_ACCOUNT_JSON

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]


def _load_credentials() -> service_account.Credentials:
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        import json

        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    if GOOGLE_SERVICE_ACCOUNT_FILE:
        return service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )

    raise RuntimeError(
        "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON to use "
        "Calendar/Drive tools."
    )


@lru_cache(maxsize=1)
def get_calendar_service() -> Resource:
    return build("calendar", "v3", credentials=_load_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def get_drive_service() -> Resource:
    return build("drive", "v3", credentials=_load_credentials(), cache_discovery=False)
