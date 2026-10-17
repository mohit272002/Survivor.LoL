"""WebSocket room protocol.

Client -> server:  {type: play|pause|seek|ping|signal|camera|chat, ...}
Server -> client:  {type: roster|sync|signal|chat|pong|peer_left|camera, ...}

- Only `host` may send play/pause/seek/media actions (authoritative host).
- `signal` messages are relayed verbatim for WebRTC POV mesh: clients exchange
  SDP offers/answers + ICE candidates peer-to-peer; server never sees media.
- Every `sync` broadcast carries `server_ts` for latency compensation.
"""
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal, get_session
from ..security import verify_token
from ..services.sync import authoritative_event
from .manager import broadcast, get_room, roster, server_now_ms
from ..models import Room as RoomModel

router = APIRouter()


async def _persist_playback(code: str, state) -> None:
    """Best-effort: write authoritative playback state back to Postgres."""
    pos, _ts, rate, st = state.snapshot()
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(RoomModel)
                .where(RoomModel.code == code.upper())
                .values(playback_state=st, playback_position=pos, playback_rate=rate,
                        updated_at=func.now())
            )
            await session.commit()
    except Exception:
        pass


@router.websocket("/ws/rooms/{code}")
async def room_socket(
    ws: WebSocket, code: str, token: str = Query(...), name: str = Query(default="Guest")
):
    code = code.upper()
    try:
        claims = verify_token(token, code)
    except ValueError:
        await ws.close(code=4401, reason="unauthorized")
        return
    participant_id: str = claims["sub"]
    role: str = claims.get("role", "guest")
    await ws.accept()

    from .manager import Peer

    state = await get_room(code)
    async with state.lock:
        state.peers[participant_id] = Peer(
            participant_id=participant_id, display_name=name[:80] or "Guest", role=role, ws=ws
        )
    pos, ts, rate, st = state.snapshot()
    await ws.send_json({
        "type": "hello_ack", "code": code, "participant_id": participant_id,
        "server_ts": ts,
        "peers": roster(state),
        "sync": {"action": "play" if st == "playing" else "pause", "position": pos,
                 "server_ts": ts, "rate": rate, "media_id": state.current_media_id},
    })
    await broadcast(code, {"type": "roster", "peers": roster(state)}, exclude=participant_id)

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "ping":
                await ws.send_json({"type": "pong", "client_ts": msg.get("client_ts"), "server_ts": server_now_ms()})
            elif mtype in ("play", "pause", "seek"):
                if role != "host":
                    await ws.send_json({"type": "error", "message": "only host controls playback"})
                    continue
                pos = float(msg.get("position", state.playback_position))
                rate = float(msg.get("rate", state.playback_rate or 1.0))
                async with state.lock:
                    # anchor the position to *now* so snapshot() can project later
                    state.position_ts = server_now_ms()
                    state.playback_position = pos
                    state.playback_rate = rate
                    state.playback_state = "playing" if mtype == "play" else (
                        "paused" if mtype == "pause" else state.playback_state)
                    # A seek changes position without changing the current
                    # play/pause state; tell clients which state to apply.
                    action = (
                        ("play" if state.playback_state == "playing" else "pause")
                        if mtype == "seek"
                        else mtype
                    )
                await broadcast(code, authoritative_event(action, pos, rate))
                await _persist_playback(code, state)
            elif mtype == "signal":
                # WebRTC POV signaling relay: {to, sdp|im, kind: offer|answer|ice}
                to = msg.get("to")
                target = state.peers.get(to) if to else None
                payload = {"type": "signal", "from": participant_id, "kind": msg.get("kind"),
                           "payload": msg.get("payload"), "server_ts": server_now_ms()}
                if target and target.ws:
                    try:
                        await target.ws.send_json(payload)
                    except Exception:
                        pass
                else:
                    await broadcast(code, payload, exclude=participant_id)
            elif mtype == "camera":
                on = bool(msg.get("on", False))
                async with state.lock:
                    if participant_id in state.peers:
                        state.peers[participant_id].camera_on = on
                await broadcast(code, {"type": "camera", "from": participant_id, "on": on})
            elif mtype == "chat":
                await broadcast(code, {"type": "chat", "from": participant_id,
                                       "name": state.peers.get(participant_id).display_name
                                       if participant_id in state.peers else name,
                                       "text": str(msg.get("text", ""))[:500],
                                       "server_ts": server_now_ms()})
    except WebSocketDisconnect:
        pass
    finally:
        async with state.lock:
            state.peers.pop(participant_id, None)
        await broadcast(code, {"type": "peer_left", "participant_id": participant_id,
                               "peers": roster(state)})
