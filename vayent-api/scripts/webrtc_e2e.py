#!/usr/bin/env python3
"""
Headless WebRTC E2E test using aiortc.
- Creates a remote Aethex conversation session via `app.routers.voice.create_remote_voice_session`
- Builds an RTCPeerConnection configured with returned ICE servers
- Sends an SDP offer (full offer with ICE gathered), proxies it to Aethex via
  `app.routers.voice.proxy_offer_to_remote`, sets the returned answer
- Waits for an incoming audio track and attempts to consume a few frames
- Closes the remote session

Run with:
  APP_ENV=development PYTHONPATH="<repo>/vayent-api" python scripts/webrtc_e2e.py

Note: this script calls into the application router functions directly
(bypassing HTTP auth) by providing a fake `current_user` object.
"""
import os
import sys
import asyncio
from pathlib import Path

# best-effort load .env into environment if not already present
def load_env_from_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

if not os.environ.get("AETHEX_API_KEY") or not os.environ.get("AETHEX_AGENT_ID"):
    repo_root = Path(__file__).resolve().parents[1]
    load_env_from_file(repo_root / ".env")

# Ensure project package is importable when run from repo root
repo_api = Path(__file__).resolve().parents[1]
if str(repo_api) not in sys.path:
    sys.path.insert(0, str(repo_api))

# Import app modules after env setup
from types import SimpleNamespace
from app.config import get_settings
from app.routers import voice

# Attempt to import aiortc
try:
    from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, RTCSessionDescription
except Exception as e:
    print("aiortc is not installed or failed to import:", e, file=sys.stderr)
    print("Install with: python -m pip install aiortc", file=sys.stderr)
    raise

# Refresh settings and ensure voice module sees the same settings
try:
    get_settings.cache_clear()
except Exception:
    pass
settings = get_settings()
voice.settings = settings


async def main():
    fake_user = SimpleNamespace(id="user-123")

    print("Creating remote Aethex session (server-proxied)...")
    try:
        create_resp = await voice.create_remote_voice_session(agent_id=None, current_user=fake_user)
    except Exception as e:
        print("Failed to create remote session:", e, file=sys.stderr)
        return 1

    print("Create response:", create_resp)
    sid = create_resp.get("session_id") or create_resp.get("sessionId") or create_resp.get("id")
    if not sid:
        print("No session id returned", file=sys.stderr)
        return 1

    ice_conf = create_resp.get("ice_config") or create_resp.get("iceConfig") or create_resp.get("ice") or {}
    ice_servers = []
    for s in ice_conf.get("iceServers", []):
        urls = s.get("urls") or s.get("url") or s.get("urls")
        username = s.get("username")
        credential = s.get("credential")
        ice_servers.append(RTCIceServer(urls=urls, username=username, credential=credential))

    rtc_config = RTCConfiguration(iceServers=ice_servers) if ice_servers else None
    pc = RTCPeerConnection(configuration=rtc_config)

    got_audio = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        print("Track received:", track.kind)
        if track.kind == "audio":
            # attempt to read a few audio frames (async)
            async def consume_audio():
                print("Consuming audio frames from track for 6 seconds...")
                try:
                    # try to receive some frames; some implementations expose recv()
                    for i in range(30):
                        frame = await track.recv()
                        # frame may be av.AudioFrame; print basic info
                        print(f"Got audio frame: pts={getattr(frame, 'pts', None)} samples={getattr(frame, 'samples', None)}")
                        await asyncio.sleep(0.2)
                except Exception as ex:
                    print("Audio consume error (non-fatal):", ex)
                finally:
                    got_audio.set()

            asyncio.ensure_future(consume_audio())

    # receive-only audio transceiver
    pc.addTransceiver("audio", direction="recvonly")

    print("Creating local SDP offer...")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering to complete so we send full SDP (no trickle)
    print("Waiting for ICE gathering to complete...")
    for _ in range(200):
        if pc.iceGatheringState == "complete":
            break
        await asyncio.sleep(0.1)

    local = pc.localDescription
    offer_body = {"sdp": local.sdp, "type": local.type}

    print("Proxying offer to remote Aethex session...")
    try:
        answer_resp = await voice.proxy_offer_to_remote(sid, body=offer_body, current_user=fake_user)
    except Exception as e:
        print("Failed to proxy offer:", e, file=sys.stderr)
        await pc.close()
        return 1

    print("Answer response:", answer_resp)
    answer_sdp = answer_resp.get("sdp") or (answer_resp.get("answer") or {}).get("sdp")
    answer_type = answer_resp.get("type") or "answer"
    if not answer_sdp:
        print("No SDP answer returned", file=sys.stderr)
        await pc.close()
        return 1

    print("Setting remote description...")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type=answer_type))

    print("Waiting for audio track or timeout (20s)...")
    try:
        await asyncio.wait_for(got_audio.wait(), timeout=20)
        print("Audio frames consumed (or track delivered)")
    except asyncio.TimeoutError:
        print("Timed out waiting for audio track")

    print("Closing remote session and peer connection...")
    try:
        await voice.proxy_close_session(sid, current_user=fake_user)
    except Exception as e:
        print("Failed to close remote session (non-fatal):", e)

    await pc.close()
    print("Done")
    return 0


if __name__ == '__main__':
    res = asyncio.run(main())
    sys.exit(res)
