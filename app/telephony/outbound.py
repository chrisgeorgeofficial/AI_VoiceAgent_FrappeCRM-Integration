"""Messages we send back up the Twilio media socket.

Twilio's outbound protocol is the mirror of the inbound one: JSON frames
carrying base64 mu-law, all tagged with the `streamSid` that arrived on the
`start` event.

The documentation says Twilio buffers whatever you send and plays it back in
order, at any size. On this account it does not behave that way: a whole reply
written at once is never heard, while an echo of Twilio's own frames - the same
bytes, arriving at the rate Twilio sent them - is perfectly audible. So audio
goes out one 20ms frame at a time, paced to the speed it plays.
"""

import asyncio
import base64
import time

from fastapi import WebSocket

from app.audio.codec import FRAME_BYTES, TELEPHONY_SAMPLE_RATE, frames
from app.logging_utils import log

# One 20ms frame per message, exactly as Twilio sends them to us. The echo
# test - bouncing Twilio's own frames straight back - is audible, so this is
# the shape of traffic known to work on this account.
OUTBOUND_CHUNK_BYTES = FRAME_BYTES

# How far ahead of real time we are allowed to get. Twilio's documentation says
# it buffers whatever you send, but a whole sentence written at once is not
# heard, while the paced echo is - so audio is fed at roughly the speed it
# plays, keeping this much in hand to absorb jitter.
LEAD_SECONDS = 0.4

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
    the leftover for the next chunk, and paces them against the clock. `flush`
    pads the tail with silence, which costs at most 20ms of quiet at the end of
    a sentence.
    """

    def __init__(self, websocket: WebSocket, stream_sid: str, should_stop=None):
        self._websocket = websocket
        self._stream_sid = stream_sid
        # Checked between frames. Now that sending takes as long as the audio
        # lasts, waiting for the chunk to finish before noticing an interruption
        # would leave the agent talking over the caller for seconds.
        self._should_stop = should_stop or (lambda: False)
        self._buffer = bytearray()
        self._started: float | None = None
        self.sent = 0

    async def feed(self, ulaw: bytes) -> None:
        """Take audio of any length; write out whatever completes whole frames.

        Whole frames go out as soon as they are complete, then `_write` waits
        if that has put us too far ahead of playback.
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
        """Send frame by frame, checking the clock between each.

        Pacing has to happen per frame, not per call: handing a whole chunk to
        `send_audio` would write every frame in it instantly and only then
        wait, which is the same burst on a smaller scale.
        """
        if self._started is None:
            self._started = time.monotonic()

        for frame in frames(audio, FRAME_BYTES):
            if self._should_stop():
                return
            await self._websocket.send_json(
                {
                    "event": "media",
                    "streamSid": self._stream_sid,
                    "media": {"payload": base64.b64encode(frame).decode()},
                }
            )
            self.sent += len(frame)
            await self._pace()

    async def _pace(self) -> None:
        """Hold back once we are far enough ahead of what has been played.

        Without this, a whole reply is written in a few milliseconds. Twilio is
        documented to buffer that, but in practice the caller hears nothing -
        whereas the echo test, which is paced by the inbound frame clock, is
        perfectly audible. So we feed at playback speed.
        """
        played_by_now = time.monotonic() - self._started
        written = self.sent / TELEPHONY_SAMPLE_RATE
        ahead = written - played_by_now
        if ahead > LEAD_SECONDS:
            await asyncio.sleep(ahead - LEAD_SECONDS)


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
