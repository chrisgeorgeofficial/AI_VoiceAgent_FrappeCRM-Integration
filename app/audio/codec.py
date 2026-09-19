"""The one translation between Twilio's audio and Sarvam's audio.

Twilio's media stream is G.711 mu-law at 8 kHz. Sarvam's streaming STT wants
PCM16 (wrapped in a WAV container) and its TTS can render straight back at
8 kHz. Because both ends agree on the sample *rate*, the only thing that ever
changes in here is the sample *encoding* - there is no resampling to get wrong.

`audioop` was dropped from the standard library in Python 3.13; the
`audioop-lts` package puts it back under the same import name, which is why
this module imports it without any version dance.
"""

import audioop
import io
import wave

# PCM16: two bytes per sample. Both audioop calls below need to be told this.
SAMPLE_WIDTH = 2

# Twilio speaks 8 kHz in both directions and Sarvam is happy at 8 kHz.
TELEPHONY_SAMPLE_RATE = 8000

# One Twilio media frame is 20 ms of mu-law, and mu-law is one byte a sample.
FRAME_BYTES = TELEPHONY_SAMPLE_RATE // 50


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Twilio -> Sarvam."""
    return audioop.ulaw2lin(ulaw_bytes, SAMPLE_WIDTH)


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Sarvam -> Twilio."""
    return audioop.lin2ulaw(pcm_bytes, SAMPLE_WIDTH)


def wrap_as_wav(pcm_bytes: bytes, sample_rate: int = TELEPHONY_SAMPLE_RATE) -> bytes:
    """Put a WAV header on raw PCM16.

    Sarvam's streaming STT only accepts `audio/wav` chunks, so every buffer we
    send has to carry its own header - it is 44 bytes on top of a second of
    audio, which is not worth optimising away.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buf.getvalue()


def pcm16_from_wav(wav_bytes: bytes) -> tuple[bytes, int]:
    """Pull raw PCM16 and its sample rate back out of a WAV blob.

    The usual shortcut is to lop off the first 44 bytes, but a WAV header is
    only 44 bytes when nobody has added an extra chunk to it. Parsing costs the
    same and cannot silently prepend a burst of static to the reply, so we
    parse - and we report the rate rather than assuming Sarvam honoured ours.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return wav.readframes(wav.getnframes()), wav.getframerate()


def resample_pcm16(pcm_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """Safety net for when audio does not arrive at the rate we asked for."""
    if from_rate == to_rate:
        return pcm_bytes
    converted, _ = audioop.ratecv(
        pcm_bytes, SAMPLE_WIDTH, 1, from_rate, to_rate, None
    )
    return converted


def frames(payload: bytes, size: int = FRAME_BYTES):
    """Slice a payload into fixed-size frames, for feeding Twilio in 20 ms bites."""
    for start in range(0, len(payload), size):
        yield payload[start : start + size]
