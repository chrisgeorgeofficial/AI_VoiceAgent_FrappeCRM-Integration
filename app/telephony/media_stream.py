"""Consume the Twilio Media Stream WebSocket.

Twilio sends JSON frames: `connected`, `start`, then a steady flow of `media`
frames (base64 mu-law, 8 kHz, ~50/sec), and finally `stop`. For now we only
log them; transcription and the agent reply loop plug in here later.
"""

import json

from fastapi import WebSocket, WebSocketDisconnect

from app.config import LOG_MEDIA_EVERY
from app.logging_utils import log


async def handle_media_stream(websocket: WebSocket) -> None:
    log("\n>>> WS /voice/media connection attempt")
    log(f"  client : {websocket.client}")
    log(f"  headers: {dict(websocket.headers)}")

    await websocket.accept()
    log(">>> WS accepted")

    frames = 0
    try:
        async for message in websocket.iter_text():
            frames += 1
            _log_frame(frames, message)
    except WebSocketDisconnect as exc:
        log(f"<<< WS disconnected (code={exc.code}) after {frames} frames")
    except Exception as exc:  # noqa: BLE001 - debug aid only
        log(f"<<< WS error after {frames} frames: {type(exc).__name__}: {exc}")
    else:
        log(f"<<< WS stream ended after {frames} frames")


def _log_frame(frames: int, message: str) -> None:
    try:
        event = json.loads(message).get("event")
    except json.JSONDecodeError:
        event = "<non-json>"

    # Control frames are rare and interesting; media frames are a firehose.
    if event != "media":
        log(f"  ws event #{frames}: {event} :: {message[:200]}")
    elif frames % LOG_MEDIA_EVERY == 0:
        log(f"  ws media frames received: {frames}")
