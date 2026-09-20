"""All environment-driven settings, read once at import.

Everything the app can be tuned with lives here, so there is a single place
to look when you are wondering where a value came from. `.env` is loaded
before anything is read; real environment variables win over the file.
"""

import os

from dotenv import load_dotenv

load_dotenv()

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


# --- Telephony ---------------------------------------------------------------

# False: answer the call with <Say>. True: hand the audio to /voice/media.
USE_MEDIA_STREAM = _flag("USE_MEDIA_STREAM")

# Optional override for the wss:// host, e.g. "abc123.ngrok-free.dev".
# Leave unset to use the Host header, which is what ngrok/Twilio send.
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "").strip()


# --- Debug logging -----------------------------------------------------------

# Media frames arrive ~50/sec; log a heartbeat every N frames instead of each.
LOG_MEDIA_EVERY = int(os.getenv("LOG_MEDIA_EVERY", "100"))

# Set false once the Twilio webhook is behaving to quieten the terminal.
LOG_REQUEST_DETAIL = _flag("LOG_REQUEST_DETAIL", default=True)


# --- Sarvam ------------------------------------------------------------------

# The `api-subscription-key` from the Sarvam dashboard. The SDK reads this same
# variable by itself, but we look it up here so a missing key is reported once,
# at startup, rather than as a 401 in the middle of someone's phone call.
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()

# Language for the whole loop: STT input, the language the LLM replies in, and
# the TTS voice. en-IN keeps the first test in English.
SARVAM_LANGUAGE = os.getenv("SARVAM_LANGUAGE", "en-IN").strip()

# saaras:v3 is the recommended streaming model. `transcribe` returns the words
# as spoken; `translate` would render them in English regardless of input.
SARVAM_STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v3").strip()
SARVAM_STT_MODE = os.getenv("SARVAM_STT_MODE", "transcribe").strip()

# Chat model for the reply. The plain sarvam-105b is a reasoning model: it
# spends its token budget thinking, returns content=None, and takes ~1.2s to
# do it. The -conversations variant answers in ~0.3s, which is what a caller
# sitting in silence needs. This is also the swap point if you move to Gemini.
SARVAM_CHAT_MODEL = os.getenv(
    "SARVAM_CHAT_MODEL", "sarvam-105b-conversations"
).strip()

# bulbul:v2 is deprecated and the API refuses it. Speakers are tied to the
# model too - the SDK's speaker list spans both, so a v2 voice on v3 is a 400
# at call time rather than a type error at edit time.
SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3").strip()
SARVAM_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "shreya").strip()


# --- Agent loop --------------------------------------------------------------

# Twilio sends 20 ms of audio at a time, which is far too little to transcribe.
# Accumulate this much before handing a chunk to Sarvam.
STT_CHUNK_MS = int(os.getenv("STT_CHUNK_MS", "1000"))

# False: transcribe and print only, leaving the caller in silence. That is the
# checkpoint worth passing before turning the rest of the loop on.
AGENT_REPLY_ENABLED = _flag("AGENT_REPLY_ENABLED", default=True)

# Stop feeding the caller's audio to STT while the agent is talking. Without
# this the agent hears itself down the line and answers its own sentences.
HALF_DUPLEX = _flag("HALF_DUPLEX", default=True)

# Spoken as soon as the stream opens. It proves the outbound audio path works
# before you have said a word. Set empty to answer in silence.
GREETING_TEXT = os.getenv(
    "GREETING_TEXT",
    "Hello! Thanks for calling. What can I help you with today?",
).strip()

# How much of the conversation to replay to the LLM each turn, in messages.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "10"))


# --- Speech pacing -----------------------------------------------------------

# True: play each piece of the reply as Sarvam renders it (~0.7s to first audio).
# False: wait for the whole clip (~2.0s), which is the simpler, older path.
TTS_STREAMING = _flag("TTS_STREAMING", default=True)

# Let the caller talk over the agent and have it stop. Detection is by audio
# energy, not by transcript - waiting for words would cost the second that
# barging in exists to save.
BARGE_IN = _flag("BARGE_IN", default=True)

# How loud an inbound frame must be to count as speech, and for how many frames
# running. 10 frames is 200ms: long enough to ignore a cough, a keypress or a
# bump on the line, short enough to feel immediate. Raise BARGE_IN_RMS if a
# noisy line keeps cutting the agent off; lower it if it ignores you.
BARGE_IN_RMS = int(os.getenv("BARGE_IN_RMS", "800"))
BARGE_IN_FRAMES = int(os.getenv("BARGE_IN_FRAMES", "10"))
