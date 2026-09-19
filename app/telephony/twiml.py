"""Build and sanity-check the TwiML documents handed back to Twilio."""

from fastapi import Request

from app.config import PUBLIC_HOST
from app.logging_utils import log

SAY_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>
        Hello! This response is coming from my FastAPI server.
        The Twilio webhook is working successfully.
    </Say>
</Response>"""


def resolve_host(request: Request) -> str:
    """Work out the public host for the wss:// URL, showing the working."""
    header_host = request.headers.get("host", "")
    forwarded_host = request.headers.get("x-forwarded-host", "")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")

    log("--- host resolution ---")
    log(f"  host header        : {header_host!r}")
    log(f"  x-forwarded-host   : {forwarded_host!r}")
    log(f"  x-forwarded-proto  : {forwarded_proto!r}")
    log(f"  PUBLIC_HOST env    : {PUBLIC_HOST!r}")

    host = PUBLIC_HOST or forwarded_host or header_host
    log(f"  -> using host      : {host!r}")

    _warn_about_host(host)
    return host


def _warn_about_host(host: str) -> None:
    """The usual ways this value ends up wrong."""
    if not host:
        log("  !! no host available - the Stream url will be malformed")
    if host.startswith(("http://", "https://", "ws://", "wss://")):
        log("  !! host contains a scheme; it must be bare (example.ngrok-free.dev)")
    if host.endswith("/"):
        log("  !! host has a trailing slash")
    if host.strip() != host:
        log("  !! host has surrounding whitespace")
    if "localhost" in host or "127.0.0.1" in host or host.startswith("0.0.0.0"):
        log("  !! host is local - Twilio cannot reach this; expose it via ngrok")


def build_stream_response(host: str) -> str:
    """TwiML that hands the call audio to our WebSocket endpoint."""
    stream_url = f"wss://{host}/voice/media"
    log(f"stream url: {stream_url}")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}"/>
    </Connect>
</Response>"""


def check_twiml(twiml: str) -> None:
    """Eyeball checks on the TwiML we are about to hand Twilio."""
    log("--- twiml checks ---")
    if "ws://" in twiml and "wss://" not in twiml:
        log("  !! Stream url uses ws:// - Twilio requires wss://")
    elif "wss://" in twiml:
        log("  ok: Stream url uses wss://")
    else:
        log("  note: no <Stream> in this response (no wss:// expected)")
