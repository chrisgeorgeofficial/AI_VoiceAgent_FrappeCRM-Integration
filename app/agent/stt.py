"""Streaming speech-to-text: one Sarvam socket for the length of a call.

Twilio delivers ~50 audio frames a second, each 20 ms long - far too small to
transcribe one at a time. So we accumulate about a second of audio and push
that up as a chunk. The Sarvam socket itself stays open for the whole call:
reconnecting per chunk would add a TLS handshake to every utterance and throw
away the server's view of the conversation.

Transcripts come back whenever Sarvam's voice-activity detection decides the
caller has finished a thought, so sending and receiving are decoupled - a
background task reads results and hands them to a callback.
"""

import asyncio
import base64
import contextlib
from typing import Awaitable, Callable

from sarvamai import AsyncSarvamAI

from app.audio.codec import (
    SAMPLE_WIDTH,
    TELEPHONY_SAMPLE_RATE,
    ulaw_to_pcm16,
    wrap_as_wav,
)
from app.config import (
    SARVAM_LANGUAGE,
    SARVAM_STT_MODE,
    SARVAM_STT_MODEL,
    STT_CHUNK_MS,
)
from app.logging_utils import log

# How many bytes of PCM16 make up one chunk's worth of audio.
CHUNK_BYTES = TELEPHONY_SAMPLE_RATE * SAMPLE_WIDTH * STT_CHUNK_MS // 1000

TranscriptHandler = Callable[[str], Awaitable[None]]


class Transcriber:
    """Feed it mu-law, it calls `on_transcript` with finished sentences."""

    def __init__(self, client: AsyncSarvamAI, on_transcript: TranscriptHandler):
        self._client = client
        self._on_transcript = on_transcript
        self._stack = contextlib.AsyncExitStack()
        self._socket = None
        self._reader: asyncio.Task | None = None
        self._pcm = bytearray()
        self._chunks_sent = 0

    async def __aenter__(self) -> "Transcriber":
        return await self.start()

    async def start(self) -> "Transcriber":
        """Open the socket and begin reading results."""
        log(
            f"stt: connecting (model={SARVAM_STT_MODEL} mode={SARVAM_STT_MODE} "
            f"language={SARVAM_LANGUAGE} rate={TELEPHONY_SAMPLE_RATE} "
            f"chunk={STT_CHUNK_MS}ms)"
        )
        self._socket = await self._stack.enter_async_context(
            self._client.speech_to_text_streaming.connect(
                language_code=SARVAM_LANGUAGE,
                model=SARVAM_STT_MODEL,
                mode=SARVAM_STT_MODE,
                # The SDK types this query parameter as a string.
                sample_rate=str(TELEPHONY_SAMPLE_RATE),
            )
        )
        log("stt: connected")
        self._reader = asyncio.create_task(self._read_results())
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def feed(self, ulaw_bytes: bytes) -> None:
        """Take one Twilio media frame; send a chunk once enough has piled up."""
        self._pcm.extend(ulaw_to_pcm16(ulaw_bytes))
        while len(self._pcm) >= CHUNK_BYTES:
            chunk = bytes(self._pcm[:CHUNK_BYTES])
            del self._pcm[:CHUNK_BYTES]
            await self._send(chunk)

    async def flush(self) -> None:
        """Send the tail of the buffer and ask Sarvam to finalise it.

        Worth calling when the caller hangs up, so the last half-sentence is
        not lost along with the connection.
        """
        if self._pcm:
            await self._send(bytes(self._pcm))
            self._pcm.clear()
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.flush()

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        await self._stack.aclose()
        self._socket = None
        log(f"stt: closed after {self._chunks_sent} chunks")

    async def _send(self, pcm_chunk: bytes) -> None:
        wav = wrap_as_wav(pcm_chunk, TELEPHONY_SAMPLE_RATE)
        await self._socket.transcribe(
            audio=base64.b64encode(wav).decode(),
            encoding="audio/wav",
            sample_rate=TELEPHONY_SAMPLE_RATE,
        )
        self._chunks_sent += 1

    async def _read_results(self) -> None:
        """Drain the socket for as long as the call lasts."""
        try:
            async for message in self._socket:
                try:
                    await self._handle(message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - skip the message, keep the call
                    log(f"stt: dropped a result: {type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dead socket must not kill the call
            log(f"stt: reader stopped: {type(exc).__name__}: {exc}")

    async def _handle(self, message) -> None:
        kind = getattr(message, "type", None)
        data = getattr(message, "data", None)

        if kind == "error":
            log(f"stt: error from sarvam: {getattr(data, 'error', data)}")
            return

        if kind == "events":
            # Voice-activity signals (speech started/ended). Useful later for
            # barge-in; for now they are only worth seeing in the log.
            log(f"stt: event {getattr(data, 'signal_type', None)}")
            return

        transcript = (getattr(data, "transcript", "") or "").strip()
        if not transcript:
            return

        log(f"stt: transcript: {transcript!r}")
        await self._on_transcript(transcript)
