"""Room + join/leave REST endpoints."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Participant, Room, SessionEvent
from ..schemas import JoinIn, JoinOut, RoomCreate, RoomOut
from ..security import generate_room_code, mint_token
from ..ws.manager import get_room, roster

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


async def _get_room_or_404(session: AsyncSession, code: str) -> Room:
    res = await session.execute(select(Room).where(Room.code == code.upper(), Room.is_active == True))  # noqa: E712
    room = res.scalar_one_or_none()
    if room is None:
        raise HTTPException(404, "room not found")
    return room


@router.post("", response_model=dict, status_code=201)
async def create_room(body: RoomCreate, session: AsyncSession = Depends(get_session)):
    for _ in range(5):  # retry on code collision
        code = generate_room_code()
        exists = await session.execute(select(Room.id).where(Room.code == code))
        if exists.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(500, "could not allocate room code")

    room = Room(code=code, name=body.name, host_name=body.host_name)
    session.add(room)
    await session.flush()
    host = Participant(room_id=room.id, display_name=body.host_name, role="host")
    session.add(host)
    session.add(SessionEvent(room_id=room.id, kind="join", payload='{"role":"host"}'))
    await session.commit()

    token = mint_token(code, str(host.id), "host")
    state = await get_room(code)
    async with state.lock:
        state.playback_state = room.playback_state
    return {"code": code, "token": token, "participant_id": str(host.id), "role": "host"}


@router.get("/{code}", response_model=RoomOut)
async def get_room_meta(code: str, session: AsyncSession = Depends(get_session)):
    room = await _get_room_or_404(session, code)
    return RoomOut(
        code=room.code,
        name=room.name,
        playback_state=room.playback_state,
        playback_position=room.playback_position,
        current_media_id=room.current_media_id,
    )


@router.post("/{code}/join", response_model=JoinOut)
async def join_room(code: str, body: JoinIn, session: AsyncSession = Depends(get_session)):
    room = await _get_room_or_404(session, code)
    participant = Participant(room_id=room.id, display_name=body.display_name.strip(), role="guest")
    session.add(participant)
    session.add(SessionEvent(room_id=room.id, kind="join", payload="{}"))
    await session.commit()
    token = mint_token(room.code, str(participant.id), "guest")
    return JoinOut(token=token, participant_id=str(participant.id), role="guest", code=room.code)


@router.post("/{code}/leave")
async def leave_room(code: str, participant_id: str, session: AsyncSession = Depends(get_session)):
    room = await _get_room_or_404(session, code)
    try:
        pid = uuid.UUID(participant_id)
    except ValueError:
        raise HTTPException(400, "bad participant id")
    res = await session.execute(
        select(Participant).where(Participant.room_id == room.id, Participant.id == pid)
    )
    p = res.scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "participant not found")
    session.add(SessionEvent(room_id=room.id, kind="leave", payload="{}"))
    await session.delete(p)
    await session.commit()
    state = await get_room(code)
    async with state.lock:
        state.peers.pop(participant_id, None)
    return {"ok": True, "roster": roster(state)}


@router.get("/{code}/roster")
async def get_roster(code: str):
    state = await get_room(code)
    return {"code": state.code, "peers": roster(state)}
