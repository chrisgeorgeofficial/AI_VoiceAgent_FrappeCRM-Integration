"""The two endpoints Twilio talks to: the call webhook and the media socket."""

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response

from app.config import LOG_REQUEST_DETAIL, USE_MEDIA_STREAM
from app.logging_utils import log
from app.telephony.media_stream import handle_media_stream
from app.telephony.twiml import (
    SAY_RESPONSE,
    build_stream_response,
    check_twiml,
    resolve_host,
)

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/incoming")
async def incoming_call(request: Request):
    """Twilio's 'A call comes in' webhook. Returns the TwiML for the call."""
    log("\n================ POST /voice/incoming ================")
    if LOG_REQUEST_DETAIL:
        await _log_request(request)

    host = resolve_host(request)
    twiml = build_stream_response(host) if USE_MEDIA_STREAM else SAY_RESPONSE

    check_twiml(twiml)
    log("--- twiml being returned to Twilio ---")
    log(twiml)
    log("======================================================\n")

    return Response(content=twiml, media_type="application/xml")


@router.websocket("/media")
async def media_stream(websocket: WebSocket):
    """Twilio connects here when <Connect><Stream> runs."""
    await handle_media_stream(websocket)


async def _log_request(request: Request) -> None:
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
