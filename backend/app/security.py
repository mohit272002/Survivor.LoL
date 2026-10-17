"""Room codes + JWT participant tokens."""
import random
import string
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no ambiguous chars


def generate_room_code(length: int = 6) -> str:
    return "".join(random.choice(_CODE_ALPHABET) for _ in range(length))


def mint_token(room_code: str, participant_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "room": room_code.upper(),
        "sub": participant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.room_token_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_token(token: str, room_code: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise ValueError("invalid token") from exc
    if payload.get("room") != room_code.upper():
        raise ValueError("token room mismatch")
    return payload
