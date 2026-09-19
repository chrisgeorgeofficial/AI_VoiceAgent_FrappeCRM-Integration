import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

# Read .env into the process environment before any os.getenv call below.
load_dotenv()

app = FastAPI()

# Flip to True to hand the call over to the media stream instead of <Say>.
USE_MEDIA_STREAM = os.getenv("USE_MEDIA_STREAM", "false").lower() == "true"

# Optional override, e.g. "abc123.ngrok-free.app". Leave unset to use the
# Host header Twilio/ngrok sends (that is the ngrok host in a tunnelled setup).
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "")


def log(*parts):
    print(*parts, flush=True)


def resolve_host(request: Request) -> str:
    """Work out the public host to put in the wss:// URL, and show the working."""
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

    # The usual ways this URL ends up wrong.
    if not host:
        log("  !! no host available - the Stream url will be malformed")
    if host.startswith(("http://", "https://", "ws://", "wss://")):
        log("  !! host contains a scheme; it must be bare (example.ngrok-free.app)")
    if host.endswith("/"):
        log("  !! host has a trailing slash")
    if host.strip() != host:
        log("  !! host has surrounding whitespace")
    if "localhost" in host or "127.0.0.1" in host or host.startswith("0.0.0.0"):
        log("  !! host is local - Twilio cannot reach this; expose it via ngrok")

    return host


def check_twiml(twiml: str) -> None:
    """Eyeball checks on the TwiML we are about to hand Twilio."""
    log("--- twiml checks ---")
    if "ws://" in twiml and "wss://" not in twiml:
        log("  !! Stream url uses ws:// - Twilio requires wss://")
    elif "wss://" in twiml:
        log("  ok: Stream url uses wss://")
    else:
        log("  note: no <Stream> in this response (no wss:// expected)")


@app.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI server is running on port 5000"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/voice/incoming")
async def incoming_call(request: Request):
    log("\n================ POST /voice/incoming ================")
    log(f"client: {request.client}")
    log("headers:")
    for key, value in request.headers.items():
        log(f"  {key}: {value}")

    # Twilio posts the call details as form-encoded data.
    try:
        form = await request.form()
        log(f"form: {dict(form)}")
    except Exception as exc:  # noqa: BLE001 - debug aid only
        log(f"form: <could not parse: {exc}>")

    host = resolve_host(request)

    if USE_MEDIA_STREAM:
        stream_url = f"wss://{host}/voice/media"
        log(f"stream url: {stream_url}")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}"/>
    </Connect>
</Response>"""
    else:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>
        Hello! This response is coming from my FastAPI server.
        The Twilio webhook is working successfully.
    </Say>
</Response>"""

    check_twiml(twiml)
    log("--- twiml being returned to Twilio ---")
    print(twiml)
    log("======================================================\n")

    return Response(content=twiml, media_type="application/xml")


@app.websocket("/voice/media")
async def media_stream(websocket: WebSocket):
    log("\n>>> WS /voice/media connection attempt")
    log(f"  client : {websocket.client}")
    log(f"  headers: {dict(websocket.headers)}")

    await websocket.accept()
    log(">>> WS accepted")

    frames = 0
    try:
        async for message in websocket.iter_text():
            frames += 1
            try:
                event = json.loads(message).get("event")
            except json.JSONDecodeError:
                event = "<non-json>"

            # Media frames arrive ~50/sec, so only log the interesting ones.
            if event != "media":
                log(f"  ws event #{frames}: {event} :: {message[:200]}")
            elif frames % 100 == 0:
                log(f"  ws media frames received: {frames}")
    except WebSocketDisconnect as exc:
        log(f"<<< WS disconnected (code={exc.code}) after {frames} frames")
    except Exception as exc:  # noqa: BLE001 - debug aid only
        log(f"<<< WS error after {frames} frames: {type(exc).__name__}: {exc}")
    else:
        log(f"<<< WS stream ended after {frames} frames")
