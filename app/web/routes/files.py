"""Conversation workspace files: list, upload, download/preview, delete, zip."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from app.context import prepare_workspace
from app.web import files
from app.web.deps import Services, current_user, get_db, get_services, load_conversation
from app.web.models import User
from app.web.uploads import ParsedUpload, UploadError, receive_upload


router = APIRouter(prefix="/conversations/{conversation_id}", tags=["files"])

MAX_UPLOAD_FILES = 20
DEFAULT_UPLOAD_DIR = "uploads"
# Opaque-origin sandbox for active content (HTML/SVG): scripts may run but cannot
# read cookies or call the API with the user's session.
SANDBOX_CSP = (
    "sandbox allow-scripts allow-popups; "
    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:"
)
ACTIVE_TYPES = frozenset(
    {
        "text/html",
        "image/svg+xml",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
    }
)
RENDERED_ACTIVE_TYPES = frozenset({"text/html", "image/svg+xml"})
INLINE_PREFIXES = ("text/", "image/", "audio/", "video/")
INLINE_TYPES = frozenset({"application/pdf"})
# Shown as plain text when previewed (never executed by the browser).
TEXT_LIKE_TYPES = frozenset(
    {
        "application/json",
        "application/javascript",
        "application/x-javascript",
        "application/x-yaml",
        "application/toml",
        "application/x-sh",
        "application/x-python-code",
        "application/sql",
    }
)


async def _workspace(
    services: Services, session: AsyncSession, conversation_id: str, user: User
) -> Path:
    conversation = await load_conversation(session, conversation_id, user)
    return services.settings.workspace_path(user.id, conversation.id)


def _resolve(workspace: Path, path: str) -> Path:
    try:
        return files.resolve_path(workspace, path)
    except files.PathViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def _content_disposition(kind: str, name: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "") or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@router.get("/files")
async def list_files(
    conversation_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    workspace = await _workspace(services, session, conversation_id, user)
    return await asyncio.to_thread(files.list_files, workspace)


def _store_uploads(workspace: Path, directory: str, upload: ParsedUpload) -> List[dict]:
    """Move staged uploads into ``workspace/directory`` with unique safe names."""
    prepare_workspace(workspace)
    target_dir = files.resolve_path(workspace, directory)
    if target_dir.exists() and not target_dir.is_dir():
        raise files.PathViolation("Upload directory is a file")
    target_dir.mkdir(parents=True, exist_ok=True)
    root = workspace.resolve()
    entries, taken = [], set()
    for staged in upload.files:
        destination = files.unique_path(
            target_dir, files.sanitize_filename(staged.filename), taken
        )
        taken.add(destination)
        shutil.move(staged.path, destination)
        entries.append(files.file_entry(root, destination))
    prepare_workspace(workspace)
    return entries


@router.post("/files")
async def upload_files(
    conversation_id: str,
    request: Request,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    workspace = await _workspace(services, session, conversation_id, user)
    await session.close()  # do not hold a connection while the body streams in
    settings = services.settings
    length = request.headers.get("content-length", "")
    limit = settings.max_upload_bytes * MAX_UPLOAD_FILES + 1024 * 1024
    if length.isdigit() and int(length) > limit:
        raise HTTPException(status_code=413, detail="Upload is too large")
    try:
        upload = await receive_upload(
            request.headers.get("content-type", ""),
            request.stream(),
            settings.uploads_tmp_dir,
            settings.max_upload_bytes,
            MAX_UPLOAD_FILES,
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail="Upload interrupted") from None
    try:
        directory = files.sanitize_dir(
            upload.fields.get("dir", DEFAULT_UPLOAD_DIR).strip()
        )
        return await asyncio.to_thread(_store_uploads, workspace, directory, upload)
    except files.PathViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    finally:
        await asyncio.to_thread(upload.discard)


@router.get("/files.zip")
async def download_zip(
    conversation_id: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    conversation = await load_conversation(session, conversation_id, user)
    workspace = services.settings.workspace_path(user.id, conversation.id)
    name = f"workspace-{conversation.id[:8]}.zip"
    return StreamingResponse(
        files.iter_zip(workspace),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition("attachment", name),
            "Cache-Control": "no-store",
        },
    )


@router.get("/files/{path:path}")
async def get_file(
    conversation_id: str,
    path: str,
    download: bool = Query(False),
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    workspace = await _workspace(services, session, conversation_id, user)
    target = _resolve(workspace, path)
    if not await asyncio.to_thread(target.is_file):
        raise HTTPException(status_code=404, detail="File not found")
    mime = files.guess_mime(target.name) or "application/octet-stream"
    headers = {
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        headers["Content-Disposition"] = _content_disposition("attachment", target.name)
        return FileResponse(target, media_type=mime, headers=headers)

    if mime in ACTIVE_TYPES:
        headers["Content-Security-Policy"] = SANDBOX_CSP
        media_type = mime if mime in RENDERED_ACTIVE_TYPES else "text/plain"
    elif mime.startswith("text/") or mime in TEXT_LIKE_TYPES:
        media_type = "text/plain"
    else:
        media_type = mime
    inline = (
        media_type.startswith(INLINE_PREFIXES)
        or media_type in INLINE_TYPES
        or mime in ACTIVE_TYPES
    )
    headers["Content-Disposition"] = _content_disposition(
        "inline" if inline else "attachment", target.name
    )
    if media_type.startswith("text/"):
        media_type += "; charset=utf-8"
    return FileResponse(target, media_type=media_type, headers=headers)


@router.delete("/files/{path:path}", status_code=204)
async def delete_file(
    conversation_id: str,
    path: str,
    user: User = Depends(current_user),
    services: Services = Depends(get_services),
    session: AsyncSession = Depends(get_db),
):
    workspace = await _workspace(services, session, conversation_id, user)
    try:
        target = files.resolve_entry(workspace, path)
    except files.PathViolation as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not os.path.lexists(target):
        raise HTTPException(status_code=404, detail="File not found")
    await asyncio.to_thread(files.delete_path, target)
