"""One phone call, from `start` to `stop`.

This is where the loop closes: Twilio's audio goes to Sarvam's STT, finished
transcripts go to the LLM, its reply goes to Sarvam's TTS, and that audio goes
back down the same socket the caller is already on.

The one piece of structure that matters here is that the socket read loop never
waits on a model. Transcripts are handed to a queue, and a worker task takes
them one at a time - so a slow reply delays the agent's answer but never stalls
the stream of inbound audio.
"""

import asyncio
import base64
import contextlib
import time

from fastapi import WebSocket

from app.agent.client import MissingApiKey, get_client
from app.agent.llm import get_ai_reply
from app.agent.stt import Transcriber
from app.agent.tts import text_to_speech
from app.audio.codec import TELEPHONY_SAMPLE_RATE
from app.config import (
    AGENT_REPLY_ENABLED,
    GREETING_TEXT,
    HALF_DUPLEX,
    LOG_MEDIA_EVERY,
)
from app.logging_utils import log
from app.telephony.outbound import send_audio, send_mark

# Queue items. "say" speaks fixed text; "turn" runs a transcript through the
# model first.
SAY = "say"
TURN = "turn"

# Twilio buffers what we send and plays it out in real time, so the audio is
# still playing long after the last write returns. This much slack on top of
# the clip's own length covers the lag before playback starts.
SPEECH_TAIL_MARGIN = 0.5


class CallSession:
    """Holds everything that lives exactly as long as one call does."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.stream_sid = ""
        self.history: list[dict] = []

        self._client = None
        self._transcriber: Transcriber | None = None
        self._work: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None

        # True from the moment we start sending audio until Twilio tells us it
        # finished playing it.
        self._speaking = False
        self._speak_until = 0.0
        self._mark = 0
        self._awaiting_mark = ""

        self._media_frames = 0
        self._dropped_frames = 0

    # --- inbound events ------------------------------------------------------

    async def handle(self, frame: dict) -> None:
        event = frame.get("event")
        if event == "start":
            await self._on_start(frame)
        elif event == "media":
            await self._on_media(frame)
        elif event == "mark":
            self._on_mark(frame)
        elif event == "stop":
            await self._on_stop()
        elif event == "connected":
            log("twilio: connected")
        else:
            log(f"twilio: unhandled event {event!r}")

    async def _on_start(self, frame: dict) -> None:
        start = frame.get("start", {})
        self.stream_sid = frame.get("streamSid") or start.get("streamSid", "")
        log(f"twilio: start streamSid={self.stream_sid} call={start.get('callSid')}")
        log(f"twilio: media format {start.get('mediaFormat')}")

        try:
            self._client = get_client()
        except MissingApiKey as exc:
            # The call still connects and the frames still log; only the agent
            # is missing. Saying so once is more use than a 401 per chunk.
            log(f"!! {exc}")
            return

        self._worker = asyncio.create_task(self._run_worker())

        transcriber = Transcriber(self._client, self._on_transcript)
        try:
            await transcriber.start()
        except Exception as exc:  # noqa: BLE001 - a bad key or a flaky socket
            log(f"!! could not open the STT stream: {type(exc).__name__}: {exc}")
        else:
            self._transcriber = transcriber

        if GREETING_TEXT:
            self._work.put_nowait((SAY, GREETING_TEXT))

    async def _on_media(self, frame: dict) -> None:
        self._media_frames += 1
        if self._media_frames % LOG_MEDIA_EVERY == 0:
            log(
                f"twilio: {self._media_frames} media frames in "
                f"({self._dropped_frames} dropped while speaking)"
            )

        if self._transcriber is None:
            return

        # The caller's line carries our own voice back while we are talking.
        # Feeding that to STT makes the agent answer itself.
        if HALF_DUPLEX and self._speaking:
            if time.monotonic() < self._speak_until:
                self._dropped_frames += 1
                return
            # Twilio's mark echo is the precise signal, but it is not guaranteed
            # to arrive. Without this the agent stays deaf for the whole call.
            log("twilio: no mark came back; the clip is over, listening again")
            self._speaking = False
            self._awaiting_mark = ""

        payload = frame.get("media", {}).get("payload")
        if payload:
            await self._transcriber.feed(base64.b64decode(payload))

    def _on_mark(self, frame: dict) -> None:
        name = frame.get("mark", {}).get("name", "")
        log(f"twilio: mark {name!r} (awaiting {self._awaiting_mark!r})")
        if name == self._awaiting_mark:
            log(f"twilio: finished playing {name}; listening again")
            self._speaking = False
            self._awaiting_mark = ""

    async def _on_stop(self) -> None:
        log("twilio: stop")
        if self._transcriber is not None:
            await self._transcriber.flush()

    # --- the turn loop -------------------------------------------------------

    async def _on_transcript(self, transcript: str) -> None:
        if not AGENT_REPLY_ENABLED:
            # Step 18's checkpoint: prove transcription works on its own before
            # anything tries to answer.
            log("agent: replies disabled, transcript only")
            return
        self._work.put_nowait((TURN, transcript))

    async def _run_worker(self) -> None:
        """Process one piece of work at a time, forever, without dying."""
        while True:
            kind, payload = await self._work.get()
            try:
                if kind == SAY:
                    await self._speak(payload)
                else:
                    await self._answer(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad turn is not a dead call
                log(f"agent: turn failed: {type(exc).__name__}: {exc}")
                self._speaking = False
            finally:
                self._work.task_done()

    async def _answer(self, transcript: str) -> None:
        self.history.append({"role": "user", "content": transcript})
        reply = await get_ai_reply(self._client, self.history)
        if not reply:
            log("agent: empty reply, saying nothing")
            return
        self.history.append({"role": "assistant", "content": reply})
        await self._speak(reply)

    async def _speak(self, text: str) -> None:
        ulaw = await text_to_speech(self._client, text)
        if not self.stream_sid:
            log("!! no streamSid yet - cannot send audio")
            return

        seconds = len(ulaw) / TELEPHONY_SAMPLE_RATE
        self._mark += 1
        self._awaiting_mark = f"reply-{self._mark}"
        self._speaking = True
        self._speak_until = time.monotonic() + seconds + SPEECH_TAIL_MARGIN

        await send_audio(self.websocket, self.stream_sid, ulaw)
        await send_mark(self.websocket, self.stream_sid, self._awaiting_mark)
        log(f"agent: sent {self._awaiting_mark} ({seconds:.1f}s of audio)")

    # --- teardown ------------------------------------------------------------

    async def aclose(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self._transcriber is not None:
            await self._transcriber.close()
            self._transcriber = None
        log(
            f"agent: call finished - {self._media_frames} frames, "
            f"{len(self.history)} messages exchanged"
        )
