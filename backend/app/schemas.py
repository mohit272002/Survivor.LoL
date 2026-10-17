"""Pydantic schemas for rooms / media / sync."""
from pydantic import BaseModel, Field


class RoomCreate(BaseModel):
    name: str = Field(default="Watch Room", max_length=120)
    host_name: str = Field(default="Host", max_length=80)


class RoomOut(BaseModel):
    code: str
    name: str
    playback_state: str = "paused"
    playback_position: float = 0.0
    current_media_id: str | None = None


class JoinIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class JoinOut(BaseModel):
    token: str
    participant_id: str
    role: str
    code: str


class MediaOut(BaseModel):
    id: str
    filename: str
    content_type: str
    kind: str
    size_bytes: int
    stream_url: str


class SelectMediaIn(BaseModel):
    media_id: str
