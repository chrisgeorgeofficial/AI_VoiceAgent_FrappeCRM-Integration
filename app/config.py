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


# --- Frappe CRM --------------------------------------------------------------

# Base URL of the Frappe desk, no trailing slash. The CRM frontend and the desk
# are different ports; this wants the desk, the one that serves /api/resource.
FRAPPE_URL = os.getenv("FRAPPE_URL", "").strip().rstrip("/")

# From the user's API Access section. The secret is shown once at generation.
FRAPPE_API_KEY = os.getenv("FRAPPE_API_KEY", "").strip()
FRAPPE_API_SECRET = os.getenv("FRAPPE_API_SECRET", "").strip()

# The custom DocType that call results are written to.
FRAPPE_DOCTYPE = os.getenv("FRAPPE_DOCTYPE", "AI Voice Agent").strip()

# False parks the write-back without removing it: calls still run and the
# extraction still logs, nothing reaches Frappe.
CRM_ENABLED = _flag("CRM_ENABLED", default=True)

# Frappe is a local service, but a hung request must not keep a background task
# alive for ever.
FRAPPE_TIMEOUT = float(os.getenv("FRAPPE_TIMEOUT", "20"))

# How many finished calls to keep in memory awaiting their status callback.
# Twilio normally fires within seconds; this is a guard against a leak if it
# never does.
TRANSCRIPT_CACHE_SIZE = int(os.getenv("TRANSCRIPT_CACHE_SIZE", "200"))


# --- Diagnostics -------------------------------------------------------------

# Echo the caller's own audio straight back and do nothing else: no Sarvam, no
# TTS, no framing logic. If the caller hears themselves, Twilio plays outbound
# stream audio and the fault is ours. If they hear nothing, the outbound path
# itself is broken and no amount of audio-pipeline work will fix it.
ECHO_TEST = _flag("ECHO_TEST")


# --- CRM lead linking --------------------------------------------------------

# Create a lead when the caller's number matches none. False links only to
# leads that already exist and leaves the field empty otherwise.
CRM_CREATE_LEADS = _flag("CRM_CREATE_LEADS", default=True)

# Status a newly created lead starts in, and where it says it came from.
# Both must exist in Frappe (CRM Lead Status / CRM Lead Source).
CRM_NEW_LEAD_STATUS = os.getenv("CRM_NEW_LEAD_STATUS", "New").strip()
CRM_NEW_LEAD_SOURCE = os.getenv("CRM_NEW_LEAD_SOURCE", "").strip()

# Who new callers are shared out between. Read live from Frappe: every enabled
# user holding this role, minus Administrator and Guest. Adding a telecaller in
# the CRM is then all it takes - nothing here needs editing.
CRM_TELECALLER_ROLE = os.getenv("CRM_TELECALLER_ROLE", "Sales User").strip()

# Fallback only, for when the role lookup returns nobody or Frappe is
# unreachable. Leave empty to rely on the role entirely.
CRM_TELECALLERS = [
    user.strip()
    for user in os.getenv("CRM_TELECALLERS", "").split(",")
    if user.strip()
]


# --- Call recording ----------------------------------------------------------

# Keep the call audio and attach it to the CRM record. Recorded here rather
# than by Twilio: a Twilio recording URL needs Twilio credentials to fetch, so
# it would sit in the CRM as a link that 401s for whoever clicks it.
RECORDING_ENABLED = _flag("RECORDING_ENABLED", default=True)

# Stop recording after this long. 8 kHz stereo PCM16 is ~32 KB/s, so ten
# minutes is roughly a 19 MB attachment - long enough for any sales call and
# short enough that a stuck line cannot exhaust memory.
MAX_RECORDING_SECONDS = int(os.getenv("MAX_RECORDING_SECONDS", "600"))

# Keep the two sides of the call on separate channels - caller left, agent
# right. Useful for analysis, but on a single earbud or a mono player you hear
# only one of them, so the default mixes both into one channel.
RECORDING_STEREO = _flag("RECORDING_STEREO")


# --- Outbound calling --------------------------------------------------------

# From the Twilio console. The account SID also appears on every inbound
# webhook; the auth token is only shown in the console.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()

# The Twilio number calls are placed from.
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "").strip()

# Public https base for the URLs Twilio fetches - the ngrok address. Falls back
# to PUBLIC_HOST so there is one less thing to keep in step.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/") or (
    f"https://{PUBLIC_HOST}" if PUBLIC_HOST else ""
)

# Origins allowed to call /voice/trigger-outbound from a browser. The Frappe
# desk is served from a different port, so the CRM's "Call with AI Agent"
# button is a cross-origin request and is blocked without this.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", FRAPPE_URL).split(",")
    if origin.strip()
]


# --- Follow-up tasks ---------------------------------------------------------

# Open a CRM Task when a call leaves something to do. Frappe CRM has no plain
# "Task" doctype - CRM Task is the one the lead view shows.
FOLLOW_UP_TASKS = _flag("FOLLOW_UP_TASKS", default=True)
FOLLOW_UP_TASK_STATUS = os.getenv("FOLLOW_UP_TASK_STATUS", "Todo").strip()
