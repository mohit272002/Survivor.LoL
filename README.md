# survivor.lol — synchronized watch rooms + live POV

Create a room, share the 6-letter code, press play once — everyone stays in sync.
Optional WebRTC POV cameras for virtual dates. Host uploads video/image files;
participants stream them through an authenticated endpoint.

## Project architecture

```
┌──────────────┐      HTTPS / JWT       ┌──────────────────┐     asyncpg      ┌────────────┐
│ React + Vite │ ◄────────────────────► │ FastAPI (Python) │ ◄──────────────► │ PostgreSQL │
│  Home / Room │   REST /api/rooms      │ rooms · media    │  rooms, participants,
│  useSynced   │   WS  /ws/rooms/{code} │ ws/manager       │  media_files, session_events
│  Video+POV   │ ── sync (server_ts) ──►│ services/sync    │
└──────────────┘ ── signal (SDP/ICE) ──►│ signaling relay  │   ┌──────────────┐
        ▲                               └──────────────────┘   │ /data/media  │
        │ WebRTC POV mesh (P2P, STUN) ◄── server never sees ──►│ host uploads │
        └──────── media bytes via /api/media/{id}/stream ─────►└──────────────┘
```

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | async WebSockets + easy Range streaming |
| Real-time sync | WebSockets, host-authoritative, `server_ts` stamped events | sub-250 ms typical; clients project `position + (now-server_ts)` and nudge `playbackRate` instead of hard seeks |
| POV cameras | WebRTC full mesh, backend only relays SDP/ICE (`signal` WS messages) | media stays P2P; server never sees camera bytes |
| Media | `MEDIA_DIR/<CODE>/` on disk, JWT-gated `FileResponse` (Range/206 supported) | no public URLs; `<video>` uses `?token=` query param |
| DB | PostgreSQL via SQLAlchemy async (`sqlite+aiosqlite` for local dev/tests) | `rooms`, `participants`, `media_files`, `session_events` |
| Frontend | React + Vite + TS (`frontend/src`) | `Home` (create/join), `Room` (player, POV grid, chat) |
| Deploy | Single Docker image (frontend built into `frontend/dist`, served by FastAPI) + `docker-compose.yml` (api + postgres:16) | one container to host anywhere |

> Honest latency note: true "sub-millisecond" sync is physically impossible over
> the WAN (jitter alone is 5–50 ms). This implementation targets **<250 ms
> drift** via server timestamps + RTT/2 compensation + rate-nudging, which is
> what production co-watch apps (Disney+ GroupWatch, Teleparty) actually do.

## Quickstart (local)

```bash
# 1) Backend (Python 3.10+, second terminal = backend API)
pip install -r requirements.txt
python -m pytest backend/tests -q   # 4 passed
python main.py                      # API on :8000 (serves frontend/dist if built)

# 2) Frontend (Node 22, first terminal)
cd frontend
npm install
npm run dev                         # UI on :5173 (proxies /api + /ws)

# 3) Open http://localhost:5173 → Create Room → share the 6-letter code
```

## Key code snippets

**1. Room creation** (`backend/app/routers/rooms.py`) — unique code + host JWT:

```python
@router.post("", status_code=201)
async def create_room(body: RoomCreate, session: AsyncSession = Depends(get_session)):
    for _ in range(5):
        code = generate_room_code()  # 6 chars, no ambiguous glyphs
        exists = await session.execute(select(Room.id).where(Room.code == code))
        if exists.scalar_one_or_none() is None:
            break
    ...
    token = mint_token(code, str(host.id), "host")
    return {"code": code, "token": token, ...}
```

**2. Video sync** — server stamps, client compensates (`backend/app/services/sync.py`, `frontend/src/hooks/useSyncedVideo.ts`):

```python
def authoritative_event(action: str, position: float, rate: float = 1.0) -> dict:
    return {"type": "sync", "action": action, "position": float(position),
            "rate": float(rate), "server_ts": server_now_ms()}
```

```ts
export function applySync(video, action, position, serverTs, offsetMs) {
  const target = action === 'pause' ? position : position + (Date.now() + offsetMs - serverTs) / 1000;
  const drift = target - video.currentTime;
  if (Math.abs(drift) > 0.6) video.currentTime = target;       // hard seek
  else if (Math.abs(drift) > 0.06) { video.playbackRate = drift > 0 ? 1.1 : 0.9; /* nudge */ }
  if (action === 'play') void video.play(); else if (action === 'pause') video.pause();
}
```

**3. Camera sharing** — WebRTC mesh, WS only signals (`frontend/src/hooks/usePovMesh.ts`, `backend/app/ws/signaling.py`):

```ts
const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
localStream?.getTracks().forEach((t) => pc.addTrack(t, localStream));
pc.onicecandidate = (e) => e.candidate && sendSignal(peerId, 'ice', e.candidate.toJSON());
```

```python
elif mtype == "signal":  # relay SDP offer/answer + ICE verbatim
    target = state.peers.get(msg.get("to"))
    payload = {"type": "signal", "from": participant_id, "kind": msg.get("kind"),
               "payload": msg.get("payload"), "server_ts": server_now_ms()}
    await (target.ws.send_json(payload) if target and target.ws
           else broadcast(code, payload, exclude=participant_id))
```

## API reference

| Method | Endpoint | Auth | Notes |
|---|---|---|---|
| POST | `/api/rooms` | — | `{name, host_name}` → `{code, token, participant_id}` |
| GET | `/api/rooms/{code}` | — | public metadata for join screen |
| POST | `/api/rooms/{code}/join` | — | `{display_name}` → guest JWT |
| POST | `/api/rooms/{code}/leave?participant_id=` | — | removes participant |
| GET | `/api/rooms/{code}/files` | Bearer JWT | list uploads |
| POST | `/api/rooms/{code}/media/upload` | Bearer host JWT | multipart video/* or image/* |
| POST | `/api/rooms/{code}/media/select` | Bearer host JWT | `{media_id}` → broadcasts `sync:media` |
| GET | `/api/media/{id}/stream?code=&token=` | JWT (header or query) | Range-capable file stream |
| WS | `/ws/rooms/{code}?token=&name=` | JWT query | `hello/play/pause/seek/ping/signal/camera/chat` |

## WS protocol

Client → server: `hello` (implicit) · `play {position}` / `pause` / `seek` (host only) ·
`ping {client_ts}` · `signal {to, kind: offer|answer|ice, payload}` · `camera {on}` · `chat {text}`.
Server → client: `hello_ack {sync, peers}` · `roster {peers}` · `sync {action, position, server_ts, media_id?}` ·
`signal {from, kind, payload}` · `camera {from, on}` · `chat {name, text}` · `pong` · `peer_left` · `error`.

Playback controls are host-authoritative: guests do not receive local player
controls, and the host's seek bar broadcasts the selected timestamp while
preserving whether the room is currently playing or paused. Camera peers are
initialized from the room's `hello_ack` roster and negotiate when camera tracks
are enabled.

## Deployment workflow (Docker + cloud)

```bash
cp .env.example .env   # set JWT_SECRET, DATABASE_URL
docker compose up --build          # api :8000 + postgres:16
# production: single image
docker build -t survivorlol:latest .
docker run -e DATABASE_URL=postgresql+asyncpg://... -e JWT_SECRET=... -p 8000:8000 survivorlol:latest
```

Cloud mapping (any host works — Render/Fly.io/AWS/DigitalOcean):
1. Provision managed Postgres; set `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db`.
2. Set `JWT_SECRET` (≥32 chars), `CORS_ORIGINS=https://your-domain`.
3. Deploy the single `Dockerfile` (multi-stage: npm build → FastAPI serves `frontend/dist` + API on `$PORT`).
4. Attach a persistent volume at `/data/media` (or swap `_media_dir` for S3 pre-signed URLs — `storage_path` column already abstracts location).
5. Scale-out note: `ws/manager.py` is in-memory; for >1 replica put a Redis pub/sub channel per room code in `broadcast()` and keep Postgres as the authoritative playback row (already updated on select).

## Security

- Room JWTs bind `participant_id + room code + role + expiry`; media streaming and
  upload/select both verify them; only `host` may upload/select or send playback commands.
- Filenames sanitized (`Path(name).name`), content-type allow-list (`video/*`, `image/*`),
  size cap (`MAX_UPLOAD_MB`), files stored outside the web root, served only via the authed endpoint.
- WS handshake rejects bad tokens (`4401`); guest playback commands get an `error` reply.
- Production checklist: HTTPS (wss), `JWT_SECRET` rotation, rate-limit join attempts,
  Postgres RLS if rows become user-owned, virus-scan uploads, TURN server (coturn) for symmetric NATs.

## Repo map

```
backend/app/{main,config,db,models,schemas,security}.py
backend/app/routers/{rooms,media}.py   backend/app/ws/{manager,signaling}.py
backend/app/services/sync.py           backend/tests/test_rooms.py
frontend/src/{main.tsx,styles.css,pages/{Home,Room}.tsx,hooks/{useSyncedVideo,usePovMesh}.ts}
Dockerfile  Dockerfile.backend  docker-compose.yml  .env.example
```
