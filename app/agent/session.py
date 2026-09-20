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
from collections import deque

from fastapi import WebSocket

from app.agent import transcripts
from app.agent.client import MissingApiKey, get_client
from app.agent.llm import get_ai_reply
from app.agent.stt import Transcriber
from app.agent.tts import stream_speech, text_to_speech
from app.audio.codec import TELEPHONY_SAMPLE_RATE, rms, ulaw_to_pcm16
from app.config import (
    AGENT_REPLY_ENABLED,
    ECHO_TEST,
    RECORDING_ENABLED,
    BARGE_IN,
    BARGE_IN_FRAMES,
    BARGE_IN_RMS,
    GREETING_TEXT,
    HALF_DUPLEX,
    LOG_MEDIA_EVERY,
    TTS_STREAMING,
)
from app.logging_utils import log
from app.telephony.outbound import FrameSender, send_clear, send_mark

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
        self.call_sid = ""
        self.history: list[dict] = []
        # Outlives this object: the status callback arrives after the socket
        # has closed and can only find the conversation by CallSid.
        self.record: transcripts.CallRecord | None = None

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

        # Set when the caller talks over the agent; the playback loop watches it.
        self._interrupt = asyncio.Event()
        self._loud_frames = 0
        # The caller's first word arrives while we are still deciding whether it
        # was speech at all. Hold those frames so they can be replayed into STT
        # rather than lost to the decision.
        self._prebuffer: deque[bytes] = deque(maxlen=BARGE_IN_FRAMES + 5)

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
        self.call_sid = start.get("callSid", "")
        log(f"twilio: start streamSid={self.stream_sid} call={self.call_sid}")
        log(f"twilio: media format {start.get('mediaFormat')}")

        if self.call_sid:
            self.record = transcripts.start(self.call_sid)

        if ECHO_TEST:
            log("ECHO TEST: bouncing your audio back, nothing else is running")
            return

        try:
            self._client = get_client()
        except MissingApiKey as exc:
            # The call still connects and the frames still log; only the agent
            # is missing. Saying so once is more use than a 401 per chunk.
            log(f"!! {exc}")
            return

        self._worker = asyncio.create_task(self._run_worker())

        # Queue the greeting before opening the STT socket, not after. The two
        # are independent, and waiting for Sarvam to connect first left the
        # caller listening to about a second of nothing.
        if GREETING_TEXT:
            self._work.put_nowait((SAY, GREETING_TEXT))

        transcriber = Transcriber(self._client, self._on_transcript)
        try:
            await transcriber.start()
        except Exception as exc:  # noqa: BLE001 - a bad key or a flaky socket
            log(f"!! could not open the STT stream: {type(exc).__name__}: {exc}")
        else:
            self._transcriber = transcriber

    async def _on_media(self, frame: dict) -> None:
        self._media_frames += 1

        if ECHO_TEST:
            payload = frame.get("media", {}).get("payload")
            if payload and self.stream_sid:
                # Straight back out, byte for byte, in Twilio's own framing.
                await self.websocket.send_json(
                    {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": payload},
                    }
                )
            if self._media_frames % LOG_MEDIA_EVERY == 0:
                log(f"ECHO TEST: bounced {self._media_frames} frames")
            return

        if self._media_frames % LOG_MEDIA_EVERY == 0:
            log(
                f"twilio: {self._media_frames} media frames in "
                f"({self._dropped_frames} dropped while speaking)"
            )

        if self._transcriber is None:
            return

        payload = frame.get("media", {}).get("payload")
        if not payload:
            return
        ulaw = base64.b64decode(payload)
        self._record("caller", ulaw)

        # The caller's line carries our own voice back while we are talking.
        # Feeding that to STT makes the agent answer itself.
        if HALF_DUPLEX and self._speaking:
            if time.monotonic() >= self._speak_until:
                # Twilio's mark echo is the precise signal, but it is not
                # guaranteed to arrive. Without this the agent stayed deaf for
                # the whole call - which is exactly what happened once.
                log("twilio: no mark came back; the clip is over, listening again")
                self._stop_speaking()
            elif await self._barged_in(ulaw):
                return  # this frame already went in with the prebuffer
            else:
                self._dropped_frames += 1
                return

        await self._transcriber.feed(ulaw)

    def _record(self, track: str, ulaw: bytes) -> None:
        """Keep the audio for the recording attached to the CRM record."""
        if RECORDING_ENABLED and self.record is not None:
            getattr(self.record.recorder, f"add_{track}")(ulaw)

    async def _barged_in(self, ulaw: bytes) -> bool:
        """Has the caller started talking over the agent?

        Judged on loudness sustained across consecutive frames. A single loud
        frame is a door slam or a click; a fifth of a second of them is a person.
        """
        if not BARGE_IN:
            return False

        self._prebuffer.append(ulaw)

        if rms(ulaw_to_pcm16(ulaw)) < BARGE_IN_RMS:
            # A quiet frame breaks the run: we want sustained speech, not noise.
            self._loud_frames = 0
            return False

        self._loud_frames += 1
        if self._loud_frames < BARGE_IN_FRAMES:
            return False

        log(f"agent: caller cut in ({self._loud_frames} loud frames) - stopping")
        self._interrupt.set()
        self._stop_speaking()
        # Drop whatever Twilio has buffered but not yet played, or the agent
        # keeps talking for seconds after we stopped sending.
        await send_clear(self.websocket, self.stream_sid)

        for held in self._prebuffer:
            await self._transcriber.feed(held)
        self._prebuffer.clear()
        return True

    def _stop_speaking(self) -> None:
        self._speaking = False
        self._awaiting_mark = ""
        self._loud_frames = 0

    def _on_mark(self, frame: dict) -> None:
        name = frame.get("mark", {}).get("name", "")
        log(f"twilio: mark {name!r} (awaiting {self._awaiting_mark!r})")
        if name == self._awaiting_mark:
            log(f"twilio: finished playing {name}; listening again")
            self._stop_speaking()

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
                    # The greeting is spoken, not generated, so it never passes
                    # through _answer - record it or the transcript starts
                    # mid-conversation.
                    if await self._speak(payload):
                        self._remember("assistant", payload)
                else:
                    await self._answer(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad turn is not a dead call
                log(f"agent: turn failed: {type(exc).__name__}: {exc}")
                self._stop_speaking()
            finally:
                self._work.task_done()

    async def _answer(self, transcript: str) -> None:
        self.history.append({"role": "user", "content": transcript})
        self._remember("user", transcript)
        reply = await get_ai_reply(self._client, self.history)
        if not reply:
            log("agent: empty reply, saying nothing")
            return

        finished = await self._speak(reply)
        # A reply the caller cut off was only half heard. Recording that stops
        # the model assuming it landed and referring back to it next turn.
        spoken = reply if finished else f"{reply} [interrupted by caller]"
        self.history.append({"role": "assistant", "content": spoken})
        self._remember("assistant", spoken)

    async def _speak(self, text: str) -> bool:
        """Play `text` into the call. False means the caller cut in."""
        if not self.stream_sid:
            log("!! no streamSid yet - cannot send audio")
            return True

        self._interrupt.clear()
        self._prebuffer.clear()
        self._loud_frames = 0
        self._mark += 1
        self._awaiting_mark = f"reply-{self._mark}"
        # Left over from the previous utterance; the loop accumulates afresh.
        self._speak_until = 0.0

        # Sarvam's chunk sizes and Twilio's 20ms frame clock have nothing to do
        # with each other; this keeps every write frame-aligned regardless.
        sender = FrameSender(
            self.websocket, self.stream_sid, should_stop=self._interrupt.is_set
        )

        async with contextlib.aclosing(self._audio_for(text)) as audio:
            async for chunk in audio:
                if self._interrupt.is_set():
                    break

                # Mute the caller from the first frame, not once the chunk has
                # finished going out: sending is paced to real time now, so a
                # chunk takes as long to send as it does to play.
                self._speaking = True
                # Playback is continuous, so each chunk extends the deadline
                # from where the last one ended rather than from now.
                self._speak_until = (
                    max(time.monotonic(), self._speak_until)
                    + len(chunk) / TELEPHONY_SAMPLE_RATE
                )

                self._record("agent", chunk)
                await sender.feed(chunk)

        interrupted = self._interrupt.is_set()
        if not interrupted:
            await sender.flush()

        if sender.sent and not interrupted:
            self._speak_until += SPEECH_TAIL_MARGIN
            await send_mark(self.websocket, self.stream_sid, self._awaiting_mark)

        log(
            f"agent: {'cut off after' if interrupted else 'sent'} "
            f"{sender.sent / TELEPHONY_SAMPLE_RATE:.1f}s of audio"
        )
        return not interrupted

    async def _audio_for(self, text: str):
        """Mu-law for `text`: streamed as it renders, or one buffered lump."""
        if TTS_STREAMING:
            async with contextlib.aclosing(
                stream_speech(self._client, text)
            ) as stream:
                async for chunk in stream:
                    yield chunk
        else:
            yield await text_to_speech(self._client, text)

    # --- teardown ------------------------------------------------------------

    def _remember(self, role: str, content: str) -> None:
        """Keep the turn for the CRM write-back after the call ends."""
        if self.record is not None:
            self.record.add(role, content)

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
