"""PostgreSQL (and SQLite-dev) tables for room/session metadata."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="Watch Room")
    host_name: Mapped[str] = mapped_column(String(80), default="Host")
    # current media selection (nullable FK string to keep sqlite+pg parity simple)
    current_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    playback_state: Mapped[str] = mapped_column(String(16), default="paused")  # playing|paused
    playback_position: Mapped[float] = mapped_column(default=0.0)
    playback_rate: Mapped[float] = mapped_column(default=1.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(default=True)


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="guest")  # host|guest
    camera_on: Mapped[bool] = mapped_column(default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 hex
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="video/mp4")
    size_bytes: Mapped[int] = mapped_column(default=0)
    kind: Mapped[str] = mapped_column(String(16), default="video")  # video|image
    storage_path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(String(80), default="host")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionEvent(Base):
    """Append-only log useful for analytics / debugging sync drift."""

    __tablename__ = "session_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # play|pause|seek|join|leave|media|signal
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
