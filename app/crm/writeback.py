"""What happens after the caller hangs up.

Twilio's status callback is the trigger rather than the media socket's `stop`
event, because the callback fires even when the socket dropped unexpectedly -
and a call that failed mid-way is exactly the one worth recording.

Everything in here runs in a background task, after Twilio already has its 200.
Nothing may raise, and nothing may block the response.
"""

from app.agent import transcripts
from app.agent.client import MissingApiKey, get_client
from app.agent.extract import FALLBACK, extract_call_fields
import httpx

from app.config import CRM_ENABLED, FRAPPE_TIMEOUT, SARVAM_LANGUAGE
from app.crm.frappe import save_call_result
from app.crm.leads import choose_assignee, create_lead, find_lead
from app.logging_utils import log

# Twilio's call statuses mapped onto the DocType's Select options. Frappe
# validates Select values server-side, so anything unlisted must become
# "other" rather than be passed through.
CALL_STATUS = {
    "completed": "completed",
    "no-answer": "no-answer",
    "failed": "failed",
    "busy": "other",
    "canceled": "other",
}

# The call is over and worth recording at these statuses, and only these.
TERMINAL = set(CALL_STATUS)

# The DocType's language Select, keyed by Sarvam's BCP-47 code.
LANGUAGES = {"en-IN": "English", "ml-IN": "Malayalam"}


async def process_completed_call(form: dict) -> None:
    """Analyse a finished call and write it to the CRM. Never raises."""
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")

    if status not in TERMINAL:
        # ringing / in-progress and friends: the call is not over yet.
        return

    record = transcripts.get(call_sid)
    log(f"crm: {call_sid} finished as {status!r} "
        f"({'transcript held' if record else 'no transcript'})")

    if not CRM_ENABLED:
        log("crm: CRM_ENABLED is false, not writing")
        return

    fields = await _analyse(record)
    payload = _build_payload(form, record, status, fields)

    lead, assignee = await _link_to_lead(form, record, fields)
    if lead:
        payload["lead"] = lead
    if assignee:
        payload["assigned_user"] = assignee

    transcript = record.as_transcript() if record else ""
    recording = record.recorder.to_wav() if record else b""
    if recording:
        log(f"crm: recording is {record.recorder.seconds:.0f}s, {len(recording)} bytes")

    if await save_call_result(payload, transcript, recording) is not None:
        transcripts.drop(call_sid)


async def _link_to_lead(form: dict, record, fields: dict) -> tuple[str, str]:
    """Find or open the caller's lead, and decide whose call this is.

    A caller already on someone's list stays with them. Only a number nobody
    owns goes into the round-robin.
    """
    phone = form.get("From") or (record.from_number if record else "")
    if not phone:
        log("crm: no caller number available, cannot link a lead")
        return "", ""

    try:
        async with httpx.AsyncClient(timeout=FRAPPE_TIMEOUT) as client:
            lead = await find_lead(client, phone)
            assignee = await choose_assignee(client, lead)

            if lead is None:
                created = await create_lead(
                    client, phone, fields.get("caller_name") or "", assignee
                )
                lead = created

            return (lead or {}).get("name", ""), assignee

    except Exception as exc:  # noqa: BLE001 - the call result still gets written
        log(f"!! crm: lead linking failed: {type(exc).__name__}: {exc}")
        return "", ""


async def _analyse(record) -> dict:
    """Structured fields from the conversation, or a flagged stand-in."""
    if record is None or record.is_empty:
        # A call that never got a word in is still worth recording - a missed
        # lead is information - but there is nothing for the model to read.
        return {
            **FALLBACK,
            "call_summary": "The call ended before anything was said.",
            "next_action": "Call the number back.",
        }

    try:
        return await extract_call_fields(get_client(), record.as_transcript())
    except MissingApiKey as exc:
        log(f"!! crm: {exc}")
        return dict(FALLBACK)


def _build_payload(form: dict, record, status: str, fields: dict) -> dict:
    """Assemble exactly the fields the DocType declares."""
    payload = {
        "provider_call_id": form.get("CallSid", ""),
        "direction": _direction(form, record),
        "language": LANGUAGES.get(SARVAM_LANGUAGE, "Mixed"),
        "call_status": CALL_STATUS.get(status, "other"),
        "call_duration": _duration(form),
        # transcript_reference and recording_reference are Attach fields, filled
        # in after the record exists by uploading files to it.
        # caller_name is extracted for naming a new lead; the DocType has no
        # such field, and Frappe rejects a payload key it does not know.
        **{k: v for k, v in fields.items() if k != "caller_name"},
    }
    # Frappe wants a Datetime or the field absent; the string "null" is neither.
    if payload.get("follow_up_at") is None:
        payload.pop("follow_up_at", None)
    return payload


def _direction(form: dict, record) -> str:
    """Twilio says 'inbound' or 'outbound-api'; the DocType wants one word."""
    raw = (form.get("Direction") or (record.direction if record else "") or "").lower()
    return "outbound" if raw.startswith("outbound") else "inbound"


def _duration(form: dict) -> int:
    try:
        return int(form.get("CallDuration") or 0)
    except (TypeError, ValueError):
        return 0
