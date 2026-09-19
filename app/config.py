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
