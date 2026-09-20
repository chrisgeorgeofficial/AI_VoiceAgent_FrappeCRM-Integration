"""The endpoints Twilio talks to: the call webhook, the media socket, and
the status callback that fires once the call is over."""

from fastapi import APIRouter, BackgroundTasks, Request, WebSocket
from fastapi.responses import Response

from app.agent import transcripts
from app.config import LOG_REQUEST_DETAIL, USE_MEDIA_STREAM
from app.crm.writeback import process_completed_call
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

    # The media stream's start event carries no phone number, and the status
    # callback arrives after the call - so the caller is noted here, where
    # Twilio does tell us who they are.
    form = await request.form()
    if form.get("CallSid"):
        transcripts.start(
            form["CallSid"],
            from_number=form.get("From", ""),
            to_number=form.get("To", ""),
            direction=form.get("Direction", "inbound"),
        )

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


@router.post("/status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """Twilio's status callback: the call is over, here is how it went.

    The analysis and the CRM write take a couple of seconds between them, and
    Twilio is waiting on this response - so the work is handed to a background
    task and Twilio gets its 200 immediately. Twilio retries callbacks it thinks
    failed, which is why the write-back is idempotent on the CallSid.
    """
    form = dict(await request.form())
    log("")
    log("================ POST /voice/status ================")
    log("  call     :", form.get("CallSid"))
    log("  status   :", form.get("CallStatus"))
    log("  duration :", form.get("CallDuration", "?"), "seconds")
    background_tasks.add_task(process_completed_call, form)
    return Response(status_code=200)


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
