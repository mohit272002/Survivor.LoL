"""Latency-compensated sync helpers.

True sub-millisecond sync is physically impossible over the public internet
(jitter alone is ~5-50ms). What production co-watch apps do instead:

1. The server stamps every authoritative event with `server_ts` (ms).
2. Receivers estimate one-way delay as RTT/2 (measured via ping/pong).
3. Receiver sets `position = event.position + (now - server_ts)/1000 * rate`
   when state == playing.
4. Small drifts (<250ms) are corrected with `playbackRate` nudging instead of
   hard seeks, to avoid visible jumps.

This module implements the server-side part (#1 + state projection).
"""
from ..ws.manager import server_now_ms


def authoritative_event(action: str, position: float, rate: float = 1.0) -> dict:
    return {
        "type": "sync",
        "action": action,  # play|pause|seek|media
        "position": float(position),
        "rate": float(rate),
        "server_ts": server_now_ms(),
    }


def project_position(state: str, position: float, rate: float, as_of_ms: int, now_ms: int) -> float:
    """Project where playback should be *now* given an authoritative snapshot."""
    if state != "playing":
        return float(position)
    elapsed_s = max(0.0, (now_ms - as_of_ms) / 1000.0)
    return float(position) + elapsed_s * float(rate)
