"""Google Drive tool handlers: material search (read-only) and upload (admin only)."""

import asyncio

from googleapiclient.http import MediaInMemoryUpload

from tools.google_auth import get_drive_service


async def search_student_materials(folder_id: str, keywords: str) -> dict:
    return await asyncio.to_thread(_search_sync, folder_id, keywords)


def _search_sync(folder_id: str, keywords: str) -> dict:
    service = get_drive_service()
    # Drive query strings use single-quoted literals; escape any embedded quote.
    safe_keywords = keywords.replace("'", "\\'")
    query = f"'{folder_id}' in parents and trashed = false and name contains '{safe_keywords}'"

    results = (
        service.files()
        .list(q=query, fields="files(id, name, mimeType, webViewLink)", pageSize=10)
        .execute()
    )
    files = results.get("files", [])
    return {"folder_id": folder_id, "keywords": keywords, "results": files}


async def upload_material(
    folder_id: str, filename: str, content_text: str, mime_type: str = "text/plain"
) -> dict:
    """Upload a text-based note/material into the tenant's Drive folder. Admin only."""
    return await asyncio.to_thread(_upload_sync, folder_id, filename, content_text, mime_type)


def _upload_sync(folder_id: str, filename: str, content_text: str, mime_type: str) -> dict:
    service = get_drive_service()
    media = MediaInMemoryUpload(content_text.encode("utf-8"), mimetype=mime_type)
    created = (
        service.files()
        .create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, name, webViewLink",
        )
        .execute()
    )
    return {
        "status": "uploaded",
        "file_id": created["id"],
        "name": created["name"],
        "web_view_link": created.get("webViewLink"),
    }
