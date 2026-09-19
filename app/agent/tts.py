"""Turn the agent's words into audio Twilio will play.

Sarvam renders at 8 kHz on request - "basic telephony quality" - which is
exactly what the phone line wants, so nothing is resampled on the way out
either. All that is left is PCM16 -> mu-law.
"""

import base64

from sarvamai import AsyncSarvamAI

from app.audio.codec import (
    TELEPHONY_SAMPLE_RATE,
    pcm16_from_wav,
    pcm16_to_ulaw,
    resample_pcm16,
)
from app.config import SARVAM_LANGUAGE, SARVAM_TTS_MODEL, SARVAM_TTS_SPEAKER
from app.logging_utils import log


async def text_to_speech(client: AsyncSarvamAI, text: str) -> bytes:
    """Synthesise `text` and return it as 8 kHz mu-law, ready for Twilio."""
    response = await client.text_to_speech.convert(
        text=text,
        model=SARVAM_TTS_MODEL,
        language_code=SARVAM_LANGUAGE,
        speaker=SARVAM_TTS_SPEAKER,
        speech_sample_rate=TELEPHONY_SAMPLE_RATE,
    )

    audio = base64.b64decode(response.audios[0])
    pcm, rate = _as_pcm16(audio)
    pcm = resample_pcm16(pcm, rate, TELEPHONY_SAMPLE_RATE)

    ulaw = pcm16_to_ulaw(pcm)
    log(f"tts: {len(text)} chars -> {len(ulaw)} bytes ({len(ulaw) / 8000:.1f}s)")
    return ulaw


def _as_pcm16(audio: bytes) -> tuple[bytes, int]:
    """Sarvam documents WAV output; accept raw PCM16 too rather than trust it."""
    if audio[:4] == b"RIFF":
        return pcm16_from_wav(audio)
    return audio, TELEPHONY_SAMPLE_RATE
