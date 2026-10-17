"""Secure media upload / select / range-streaming.

Security model:
- Every call needs a valid room JWT (room code + participant binding).
- Only `host` role can upload/select media.
- Files are stored outside the web root under MEDIA_DIR/<ROOMCODE>/ and served
  through an authenticated streaming endpoint with HTTP Range support.
- Filenames are sanitized; content-type is allow-listed to video/* + images.
"""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import MediaFile, Room
from ..schemas import MediaOut, SelectMediaIn
from ..security import verify_token
from ..ws.manager import broadcast, get_room, server_now_ms

router = APIRouter(tags=["media"])

ALLOWED_PREFIXES = ("video/", "image/")


def _require_auth(code: str, authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return verify_token(authorization.removeprefix("Bearer ").strip(), code)
    except ValueError:
        raise HTTPException(401, "invalid or expired token")


async def _room(session: AsyncSession, code: str) -> Room:
    res = await session.execute(select(Room).where(Room.code == code.upper()))
    room = res.scalar_one_or_none()
    if room is None or not room.is_active:
        raise HTTPException(404, "room not found")
    return room


def _media_dir(code: str) -> Path:
    p = Path(settings.media_dir) / code.upper()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _kind_of(content_type: str) -> str:
    return "image" if content_type.startswith("image/") else "video"


@router.get("/api/rooms/{code}/files", response_model=list[MediaOut])
async def list_files(
    code: str, authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_session)
):
    _require_auth(code, authorization)
    room = await _room(session, code)
    res = await session.execute(select(MediaFile).where(MediaFile.room_id == room.id).order_by(MediaFile.created_at))
    out = []
    for m in res.scalars():
        out.append(
            MediaOut(
                id=m.id, filename=m.filename, content_type=m.content_type, kind=m.kind,
                size_bytes=m.size_bytes, stream_url=f"/api/media/{m.id}/stream?code={room.code}",
            )
        )
    return out


@router.post("/api/rooms/{code}/media/upload", response_model=MediaOut, status_code=201)
async def upload_media(
    code: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    claims = _require_auth(code, authorization)
    if claims.get("role") != "host":
        raise HTTPException(403, "only host can upload")
    ctype = file.content_type or "application/octet-stream"
    if not ctype.startswith(ALLOWED_PREFIXES):
        raise HTTPException(415, "only video/* and image/* allowed")
    room = await _room(session, code)
    safe_name = Path(file.filename or "upload").name[:120] or "upload"
    fid = uuid.uuid4().hex
    dest = _media_dir(code) / f"{fid}_{safe_name}"
    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large")
            f.write(chunk)
    m = MediaFile(
        id=fid, room_id=room.id, filename=safe_name, content_type=ctype,
        size_bytes=size, kind=_kind_of(ctype), storage_path=str(dest),
        uploaded_by=claims.get("sub", "host"),
    )
    session.add(m)
    await session.commit()
    return MediaOut(id=fid, filename=safe_name, content_type=ctype, kind=m.kind,
                    size_bytes=size, stream_url=f"/api/media/{fid}/stream?code={room.code}")


@router.post("/api/rooms/{code}/media/select")
async def select_media(
    code: str, body: SelectMediaIn, request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    claims = _require_auth(code, authorization)
    if claims.get("role") != "host":
        raise HTTPException(403, "only host can select media")
    room = await _room(session, code)
    res = await session.execute(
        select(MediaFile).where(MediaFile.room_id == room.id, MediaFile.id == body.media_id)
    )
    media = res.scalar_one_or_none()
    if media is None:
        raise HTTPException(404, "media not found")
    room.current_media_id = media.id
    room.playback_state = "paused"
    room.playback_position = 0.0
    await session.commit()

    state = await get_room(code)
    async with state.lock:
        state.current_media_id = media.id
        state.playback_state = "paused"
        state.playback_position = 0.0
        state.playback_rate = 1.0
        state.position_ts = server_now_ms()
    from ..services.sync import authoritative_event
    await broadcast(code, {**authoritative_event("media", 0.0), "media_id": media.id,
                           "stream_url": f"/api/media/{media.id}/stream?code={room.code}"})
    return {"ok": True, "media_id": media.id}


@router.get("/api/media/{media_id}/stream")
async def stream_media(
    media_id: str, code: str, request: Request,
    range: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    token: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Token can come from `Authorization` header OR `?token=` (needed for <video> tags)."""
    bearer = authorization.removeprefix("Bearer ").strip() if authorization else (token or "")
    # also allow token passed as query param named `token`; `code` scoping still enforced
    qp_token = request.query_params.get("token")
    if qp_token:
        bearer = qp_token
    try:
        verify_token(bearer, code)
    except ValueError:
        raise HTTPException(401, "invalid or expired token")
    res = await session.execute(select(MediaFile).where(MediaFile.id == media_id))
    media = res.scalar_one_or_none()
    if media is None or not os.path.exists(media.storage_path):
        raise HTTPException(404, "media not found")
    # FileResponse handles Range requests (206 Partial Content) automatically.
    return FileResponse(media.storage_path, media_type=media.content_type, filename=media.filename)
