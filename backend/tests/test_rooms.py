"""Smoke tests: room lifecycle + sync math + upload auth."""
import asyncio
import io
import os
import tempfile
import time
from pathlib import Path

import pytest

_TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="survivor-lol-tests-")
_TEST_ROOT = Path(_TEST_DIRECTORY.name)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["MEDIA_DIR"] = str(_TEST_ROOT / "media")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import settings  # noqa: E402
settings.media_dir = str(_TEST_ROOT / "media")

from backend.app.db import engine, init_db  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.services.sync import project_position  # noqa: E402

asyncio.run(init_db())
client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_resources():
    yield
    client.close()
    asyncio.run(engine.dispose())
    _TEST_DIRECTORY.cleanup()


def test_health():
    assert client.get("/api/health").json()["ok"] is True


def test_create_join_flow():
    r = client.post("/api/rooms", json={"name": "Date Night", "host_name": "Alex"})
    assert r.status_code == 201, r.text
    room = r.json()
    code = room["code"]
    assert len(code) == 6

    meta = client.get(f"/api/rooms/{code}").json()
    assert meta["code"] == code

    j = client.post(f"/api/rooms/{code}/join", json={"display_name": "Sam"})
    assert j.status_code == 200, j.text
    guest_token = j.json()["token"]

    # guest cannot upload (host-only)
    up = client.post(
        f"/api/rooms/{code}/media/upload",
        headers={"Authorization": f"Bearer {guest_token}"},
        files={"content_type": "video/mp4", "file": ("x.mp4", io.BytesIO(b"fake"), "video/mp4")},
    )
    assert up.status_code == 403

    # host can upload + select + stream (token via query param for <video> tags)
    host_token = room["token"]
    up2 = client.post(
        f"/api/rooms/{code}/media/upload",
        headers={"Authorization": f"Bearer {host_token}"},
        files={"file": ("clip.mp4", io.BytesIO(b"0" * 1024), "video/mp4")},
    )
    assert up2.status_code == 201, up2.text
    mid = up2.json()["id"]
    sel = client.post(
        f"/api/rooms/{code}/media/select",
        headers={"Authorization": f"Bearer {host_token}", "Content-Type": "application/json"},
        json={"media_id": mid},
    )
    assert sel.status_code == 200, sel.text
    stream = client.get(f"/api/media/{mid}/stream", params={"code": code, "token": host_token})
    assert stream.status_code == 200


def test_sync_projection():
    # playing for 2s at 1x advances 2s; paused stays put
    assert project_position("playing", 10.0, 1.0, 1000, 3000) == 12.0
    assert project_position("paused", 10.0, 1.0, 1000, 3000) == 10.0


def test_websocket_sync_and_signaling():
    r = client.post("/api/rooms", json={"name": "WS", "host_name": "H"}).json()
    code, host_token = r["code"], r["token"]
    guest = client.post(f"/api/rooms/{code}/join", json={"display_name": "G"}).json()
    with client.websocket_connect(f"/ws/rooms/{code}?token={host_token}&name=H") as wsh:
        hello = wsh.receive_json()
        assert hello["type"] == "hello_ack"
        # hello_ack must carry a full sync snapshot (action/position/rate/server_ts)
        assert hello["sync"]["action"] in ("play", "pause")
        assert hello["sync"]["position"] == 0.0
        assert "server_ts" in hello["sync"]
        assert [peer["participant_id"] for peer in hello["peers"]] == [hello["participant_id"]]
        with client.websocket_connect(f"/ws/rooms/{code}?token={guest['token']}&name=G") as wsg:
            guest_hello = wsg.receive_json()
            assert guest_hello["type"] == "hello_ack"
            assert {peer["participant_id"] for peer in guest_hello["peers"]} == {
                hello["participant_id"], guest_hello["participant_id"],
            }
            roster_msg = wsh.receive_json()  # roster update
            assert roster_msg["type"] == "roster"
            # host play -> guest receives authoritative sync with server_ts
            wsh.send_json({"type": "play", "position": 5.0})
            echo = wsh.receive_json()  # host also gets the broadcast echo
            assert echo["type"] == "sync"
            sync = wsg.receive_json()
            assert sync["type"] == "sync" and sync["action"] == "play"
            assert "server_ts" in sync
            # guest play rejected (host-authoritative)
            wsg.send_json({"type": "play", "position": 0})
            err = wsg.receive_json()
            assert err["type"] == "error"
            # signaling relay for WebRTC POV
            wsg.send_json({"type": "signal", "kind": "offer", "payload": {"sdp": "fake"}})
            sig = wsh.receive_json()
            assert sig["type"] == "signal" and sig["kind"] == "offer"


def test_seek_preserves_paused_state():
    room = client.post("/api/rooms", json={"name": "Seek", "host_name": "H"}).json()
    with client.websocket_connect(f"/ws/rooms/{room['code']}?token={room['token']}&name=H") as ws:
        ws.receive_json()
        ws.send_json({"type": "seek", "position": 12.5})
        sync = ws.receive_json()
        assert sync["type"] == "sync"
        assert sync["action"] == "pause"
        assert sync["position"] == 12.5


def test_join_mid_playback_gets_position():
    r = client.post("/api/rooms", json={"name": "Mid", "host_name": "H"}).json()
    code, host_token = r["code"], r["token"]
    with client.websocket_connect(f"/ws/rooms/{code}?token={host_token}&name=H") as wsh:
        wsh.receive_json()  # hello_ack
        wsh.send_json({"type": "play", "position": 10.0})
        wsh.receive_json()  # echo sync
        time.sleep(0.2)  # let the authoritative clock advance mid-playback
        guest = client.post(f"/api/rooms/{code}/join", json={"display_name": "G"}).json()
        with client.websocket_connect(f"/ws/rooms/{code}?token={guest['token']}&name=G") as wsg:
            ghello = wsg.receive_json()
            assert ghello["type"] == "hello_ack"
            assert ghello["sync"]["action"] == "play"
            # guest joins at the projected timestamp (~10s + elapsed), NOT 0
            assert ghello["sync"]["position"] >= 10.0
            assert "server_ts" in ghello["sync"]
