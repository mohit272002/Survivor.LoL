"""FastAPI entrypoint: REST + WebSocket + static frontend."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import media, rooms
from .ws import signaling


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list + ["*"] if "*" not in settings.cors_origin_list else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rooms.router)
app.include_router(media.router)
app.include_router(signaling.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": settings.app_name}


# Serve built Vite frontend when present (Docker image copies it to ../frontend/dist)
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
