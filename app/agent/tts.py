"""Turn the agent's words into audio Twilio will play.

Sarvam renders at 8 kHz on request - "basic telephony quality" - which is
exactly what the phone line wants, so nothing is resampled on the way out
either. All that is left is PCM16 -> mu-law.

Two paths live here. `text_to_speech` waits for the whole clip, which is simple
and was the first thing to work on a real call. `stream_speech` hands back each
piece as it renders, so the caller hears the first syllable while the rest is
still being made - measured at 0.7s to first audio against 2.0s for the
buffered path. `TTS_STREAMING` chooses between them.
"""

import asyncio
import base64
import time
from typing import AsyncIterator

from sarvamai import AsyncSarvamAI

from app.audio.codec import (
    TELEPHONY_SAMPLE_RATE,
    pcm16_from_wav,
    pcm16_to_ulaw,
    resample_pcm16,
)
from app.config import SARVAM_LANGUAGE, SARVAM_TTS_MODEL, SARVAM_TTS_SPEAKER
from app.logging_utils import log

# Sarvam generates ~3.6x faster than realtime, so a gap this long means the
# stream has stalled rather than that it is merely thinking.
STREAM_TIMEOUT = 20.0

# What bulbul can actually speak. A language Sarvam detected but the voice
# cannot render - or no detection at all - falls back to the configured one
# rather than failing the reply.
SPEAKABLE = {
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN",
    "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
}


def speakable(language: str) -> str:
    if language in SPEAKABLE:
        return language
    if language:
        log(f"tts: cannot speak {language}, using {SARVAM_LANGUAGE}")
    return SARVAM_LANGUAGE


async def text_to_speech(
    client: AsyncSarvamAI, text: str, language: str = ""
) -> bytes:
    """Synthesise `text` and return it as 8 kHz mu-law, ready for Twilio."""
    response = await client.text_to_speech.convert(
        text=text,
        model=SARVAM_TTS_MODEL,
        language_code=speakable(language),
        speaker=SARVAM_TTS_SPEAKER,
        speech_sample_rate=TELEPHONY_SAMPLE_RATE,
    )

    audio = base64.b64decode(response.audios[0])
    pcm, rate = _as_pcm16(audio)
    pcm = resample_pcm16(pcm, rate, TELEPHONY_SAMPLE_RATE)

    ulaw = pcm16_to_ulaw(pcm)
    log(f"tts: {len(text)} chars -> {len(ulaw)} bytes ({len(ulaw) / 8000:.1f}s)")
    return ulaw


async def stream_speech(
    client: AsyncSarvamAI, text: str, language: str = ""
) -> AsyncIterator[bytes]:
    """Synthesise `text`, yielding 8 kHz mu-law as each piece is rendered.

    A socket per reply, not per call: an idle TTS socket is closed by the server
    with a 408 ("left open without any messages for too long"), so keeping one
    alive across a whole call would mean a keepalive task for a saving the
    handshake does not justify.
    """
    started = time.monotonic()
    first: float | None = None
    total = 0

    async with client.text_to_speech_streaming.connect(
        model=SARVAM_TTS_MODEL,
        # Without this the socket never says it is done and simply hangs.
        send_completion_event="true",
    ) as socket:
        await socket.configure(
            target_language_code=speakable(language),
            speaker=SARVAM_TTS_SPEAKER,
            speech_sample_rate=TELEPHONY_SAMPLE_RATE,
            # Twilio's own wire format, so these bytes need no conversion at all.
            output_audio_codec="mulaw",
        )
        await socket.convert(text)
        await socket.flush()

        while True:
            try:
                message = await asyncio.wait_for(socket.recv(), STREAM_TIMEOUT)
            except asyncio.TimeoutError:
                log("tts: stream stalled; abandoning the rest of this reply")
                break

            kind = getattr(message, "type", None)
            if kind == "audio":
                chunk = _to_ulaw(
                    base64.b64decode(message.data.audio), message.data.content_type
                )
                if first is None:
                    first = time.monotonic() - started
                total += len(chunk)
                yield chunk
            elif kind == "error":
                detail = getattr(message.data, "message", message.data)
                log(f"tts: error from sarvam: {detail}")
                break
            else:
                break  # completion event

    if first is not None:
        log(
            f"tts: {len(text)} chars -> {total / TELEPHONY_SAMPLE_RATE:.1f}s of audio, "
            f"first chunk in {first:.2f}s"
        )


def _to_ulaw(payload: bytes, content_type: str) -> bytes:
    """Whatever Sarvam sent back, hand out 8 kHz mu-law.

    We ask for mulaw and get it. But the codec is the server's decision, and a
    format change would not raise - it would play down the phone as static, which
    is a miserable thing to debug. Checking is three lines.
    """
    if "mulaw" in (content_type or ""):
        return payload
    pcm, rate = _as_pcm16(payload)
    return pcm16_to_ulaw(resample_pcm16(pcm, rate, TELEPHONY_SAMPLE_RATE))


def _as_pcm16(audio: bytes) -> tuple[bytes, int]:
    """Sarvam documents WAV output; accept raw PCM16 too rather than trust it."""
    if audio[:4] == b"RIFF":
        return pcm16_from_wav(audio)
    return audio, TELEPHONY_SAMPLE_RATE
