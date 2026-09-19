"""Consume the Twilio Media Stream WebSocket.

Twilio sends JSON frames: `connected`, `start`, then a steady flow of `media`
frames (base64 mu-law, 8 kHz, ~50/sec), `mark` echoes for audio we sent, and
finally `stop`. This module does the reading and the framing; what to do with
each event belongs to `CallSession`, so the transport stays separable from the
agent sitting behind it.
"""

import json

from fastapi import WebSocket, WebSocketDisconnect

from app.agent.session import CallSession
from app.logging_utils import log


async def handle_media_stream(websocket: WebSocket) -> None:
    log("\n>>> WS /voice/media connection attempt")
    log(f"  client : {websocket.client}")
    log(f"  headers: {dict(websocket.headers)}")

    await websocket.accept()
    log(">>> WS accepted")

    session = CallSession(websocket)
    frames = 0
    try:
        async for message in websocket.iter_text():
            frames += 1
            frame = _parse(frames, message)
            if frame is not None:
                await session.handle(frame)
    except WebSocketDisconnect as exc:
        log(f"<<< WS disconnected (code={exc.code}) after {frames} frames")
    except Exception as exc:  # noqa: BLE001 - debug aid only
        log(f"<<< WS error after {frames} frames: {type(exc).__name__}: {exc}")
    else:
        log(f"<<< WS stream ended after {frames} frames")
    finally:
        await session.aclose()


def _parse(frames: int, message: str) -> dict | None:
    try:
        return json.loads(message)
    except json.JSONDecodeError:
        log(f"  ws frame #{frames} was not json: {message[:200]}")
        return None
