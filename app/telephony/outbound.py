"""Messages we send back up the Twilio media socket.

Twilio's outbound protocol is the mirror of the inbound one: JSON frames
carrying base64 mu-law, all tagged with the `streamSid` that arrived on the
`start` event. Twilio buffers whatever we send and plays it out in real time,
so there is no need to pace these writes against the clock.
"""

import base64

from fastapi import WebSocket

from app.audio.codec import FRAME_BYTES, TELEPHONY_SAMPLE_RATE, frames
from app.logging_utils import log

# Audio per outbound message. Small enough to stay responsive, big enough that
# a ten-second reply is a few dozen writes rather than five hundred. It is a
# whole number of 20ms frames, which is the part that matters - see FrameSender.
OUTBOUND_CHUNK_MS = 200
OUTBOUND_CHUNK_BYTES = TELEPHONY_SAMPLE_RATE * OUTBOUND_CHUNK_MS // 1000

# Mu-law encodes silence as 0xFF, which is what a short final frame is padded
# with rather than left ragged.
ULAW_SILENCE = b"\xff"


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


class FrameSender:
    """Writes to Twilio on its own 20ms frame clock, whatever size audio arrives.

    Twilio's media stream is a sequence of 160-byte frames. The buffered TTS path
    happened to respect that - it sliced one long clip into 1600-byte pieces, ten
    whole frames each. Streaming TTS does not: Sarvam hands back chunks of 100,
    600, 1100, 1200 bytes, and forwarding those one-to-one puts a partial frame in
    almost every message.

    So this holds the remainder back and only ever writes whole frames, keeping
    the leftover for the next chunk. `flush` pads the tail with silence, which
    costs at most 20ms of quiet at the end of a sentence.
    """

    def __init__(self, websocket: WebSocket, stream_sid: str):
        self._websocket = websocket
        self._stream_sid = stream_sid
        self._buffer = bytearray()
        self.sent = 0

    async def feed(self, ulaw: bytes) -> None:
        """Take audio of any length; write out whatever completes whole frames.

        Everything complete goes immediately rather than waiting for a fuller
        message - holding audio back to batch it would spend the head start that
        streaming TTS just bought. `send_audio` still caps each message at
        OUTBOUND_CHUNK_BYTES, which is itself a whole number of frames.
        """
        self._buffer.extend(ulaw)

        whole = len(self._buffer) - (len(self._buffer) % FRAME_BYTES)
        if not whole:
            return

        await self._write(bytes(self._buffer[:whole]))
        del self._buffer[:whole]

    async def flush(self) -> None:
        """Send the tail, padded up to a whole frame."""
        if not self._buffer:
            return
        short = len(self._buffer) % FRAME_BYTES
        if short:
            self._buffer.extend(ULAW_SILENCE * (FRAME_BYTES - short))
        await self._write(bytes(self._buffer))
        self._buffer.clear()

    async def _write(self, audio: bytes) -> None:
        await send_audio(self._websocket, self._stream_sid, audio)
        self.sent += len(audio)


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
