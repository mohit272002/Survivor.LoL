"""In-memory sync-state + WebSocket fan-out.

Production note: replace `_rooms` with Redis pub/sub (one channel per room
code) and persist authoritative state in Postgres when scaling past 1 replica.
The wire protocol already carries `server_ts` so clients can compensate for
network latency; see services/sync.py.
"""
import asyncio
import time
from dataclasses import dataclass, field

from fastapi import WebSocket


@dataclass
class Peer:
    participant_id: str
    display_name: str
    role: str
    camera_on: bool = False
    ws: WebSocket | None = None


@dataclass
class RoomState:
    code: str
    playback_state: str = "paused"  # playing|paused
    playback_position: float = 0.0
    playback_rate: float = 1.0
    # server-clock ms when playback_position was last set (authoritative anchor)
    position_ts: int = 0
    current_media_id: str | None = None
    peers: dict[str, Peer] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> tuple[float, int, float, str]:
        """Authoritative playback snapshot projected to *now*.

        Returns (position, server_ts_now, rate, state). While playing, position
        advances with real time from the last authoritative anchor, so a client
        joining mid-playback can seek to the correct timestamp.
        """
        now = server_now_ms()
        if self.playback_state == "playing":
            pos = self.playback_position + (now - self.position_ts) / 1000.0 * self.playback_rate
            pos = max(0.0, pos)
        else:
            pos = self.playback_position
        return pos, now, self.playback_rate, self.playback_state


_rooms: dict[str, RoomState] = {}
_rooms_lock = asyncio.Lock()


async def get_room(code: str) -> RoomState:
    code = code.upper()
    async with _rooms_lock:
        if code not in _rooms:
            _rooms[code] = RoomState(code=code)
        return _rooms[code]


def server_now_ms() -> int:
    return int(time.time() * 1000)


async def broadcast(code: str, message: dict, exclude: str | None = None):
    """Best-effort fan-out to every connected socket in the room."""
    room = await get_room(code)
    dead: list[str] = []
    for pid, peer in list(room.peers.items()):
        if pid == exclude or peer.ws is None:
            continue
        try:
            await peer.ws.send_json(message)
        except Exception:
            dead.append(pid)
    for pid in dead:
        room.peers.pop(pid, None)


def roster(room: RoomState) -> list[dict]:
    return [
        {
            "participant_id": p.participant_id,
            "display_name": p.display_name,
            "role": p.role,
            "camera_on": p.camera_on,
        }
        for p in room.peers.values()
    ]
