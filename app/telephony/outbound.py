"""Messages we send back up the Twilio media socket.

Twilio's outbound protocol is the mirror of the inbound one: JSON frames
carrying base64 mu-law, all tagged with the `streamSid` that arrived on the
`start` event. Twilio buffers whatever we send and plays it out in real time,
so there is no need to pace these writes against the clock.
"""

import base64

from fastapi import WebSocket

from app.audio.codec import TELEPHONY_SAMPLE_RATE, frames
from app.logging_utils import log

# Audio per outbound message. Small enough to stay responsive, big enough that
# a ten-second reply is a few dozen writes rather than five hundred.
OUTBOUND_CHUNK_MS = 200
OUTBOUND_CHUNK_BYTES = TELEPHONY_SAMPLE_RATE * OUTBOUND_CHUNK_MS // 1000


async def send_audio(websocket: WebSocket, stream_sid: str, ulaw: bytes) -> None:
    """Play mu-law audio into the live call."""
    for chunk in frames(ulaw, OUTBOUND_CHUNK_BYTES):
        await websocket.send_json(
            {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": base64.b64encode(chunk).decode()},
            }
        )


async def send_mark(websocket: WebSocket, stream_sid: str, name: str) -> None:
    """Drop a bookmark after the audio.

    Twilio echoes a `mark` event back once everything queued ahead of it has
    actually been played. That echo is the only honest signal that the agent
    has stopped talking - the send loop above finishes long before the caller
    hears the last word.
    """
    await websocket.send_json(
        {"event": "mark", "streamSid": stream_sid, "mark": {"name": name}}
    )


async def send_clear(websocket: WebSocket, stream_sid: str) -> None:
    """Throw away audio Twilio has buffered but not yet played."""
    log("twilio: clearing queued audio")
    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
