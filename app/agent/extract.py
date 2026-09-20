"""Turn a finished conversation into the fields the CRM DocType expects.

The model is asked for a fixed JSON shape rather than prose. Sarvam supports
structured outputs natively (`response_format` with a JSON Schema), which is a
good deal sturdier than asking politely for "only JSON" and hoping - but the
parsing fallbacks below still exist, because a model that wraps its answer in
a code fence should cost us a tidy record, not the whole call.
"""

import json
import re
from datetime import datetime
from typing import Any

from sarvamai import AsyncSarvamAI

from app.config import SARVAM_CHAT_MODEL
from app.logging_utils import log

# These must match the Select options on the DocType exactly - Frappe rejects
# a value that is not one of its options.
LEAD_INTEREST = ["Hot", "Warm", "Cold"]
OBJECTIONS = ["Price", "Timing", "Competitor", "No need", "Other"]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "caller_name",
        "customer_intent",
        "lead_interest",
        "requested_product_service",
        "primary_objection",
        "call_summary",
        "next_action",
        "follow_up_at",
        "review_flag",
    ],
    "properties": {
        "caller_name": {
            "type": ["string", "null"],
            "description": "The caller's name if they gave it, otherwise null.",
        },
        "customer_intent": {
            "type": "string",
            "description": "What the caller actually wanted, in one sentence.",
        },
        "lead_interest": {"type": "string", "enum": LEAD_INTEREST},
        "requested_product_service": {
            "type": "string",
            "description": "The product or service asked about, or empty if none.",
        },
        "primary_objection": {"type": "string", "enum": OBJECTIONS},
        "call_summary": {
            "type": "string",
            "description": "A few sentences a salesperson could read before calling back.",
        },
        "next_action": {
            "type": "string",
            "description": "The single concrete next step.",
        },
        "follow_up_at": {
            "type": ["string", "null"],
            "description": "When to follow up, as 'YYYY-MM-DD HH:MM:SS', or null.",
        },
        "review_flag": {
            "type": "boolean",
            "description": "True if a human should check this record.",
        },
    },
}

# The model cannot turn "call me Tuesday" into a date without knowing what day
# it is, and Frappe's Datetime field will not take the word "Tuesday". So the
# current time goes into the prompt.
SYSTEM_PROMPT = (
    "You analyse sales enquiry phone calls. Read the transcript and fill in the "
    "structured record. Be faithful to what was actually said: if the caller "
    "never mentioned a budget, a timeline or an objection, do not invent one - "
    "use 'Other' for the objection and say so in the summary. If the caller "
    "complained about cost or price, the objection is 'Price'; if they were "
    "happy but not ready yet, it is 'Timing'. Set review_flag to true when the "
    "call was too short, unclear or cut off to be trusted. "
    "The current date and time is {now} ({weekday}). Resolve anything relative "
    "the caller said - 'Tuesday', 'next month', 'in a week' - into an absolute "
    "timestamp in exactly the format YYYY-MM-DD HH:MM:SS. Never answer with a "
    "day name or a phrase; use null if no follow-up was agreed."
)


def system_prompt() -> str:
    now = datetime.now()
    return SYSTEM_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M:%S"), weekday=now.strftime("%A")
    )

# Returned when the model cannot be parsed at all. The call is still recorded,
# flagged for a human, rather than dropped for want of tidy JSON.
FALLBACK = {
    "caller_name": None,
    "customer_intent": "",
    "lead_interest": "Cold",
    "requested_product_service": "",
    "primary_objection": "Other",
    "call_summary": "Automatic analysis failed; see the transcript.",
    "next_action": "Review the transcript manually.",
    "follow_up_at": None,
    "review_flag": True,
}


async def extract_call_fields(client: AsyncSarvamAI, transcript: str) -> dict:
    """Analyse `transcript` and return the DocType's structured fields."""
    if not transcript.strip():
        log("extract: empty transcript, nothing to analyse")
        return {**FALLBACK, "call_summary": "No conversation was recorded."}

    try:
        response = await client.chat.completions(
            messages=[
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": transcript},
            ],
            model=SARVAM_CHAT_MODEL,
            temperature=0.1,
            max_tokens=600,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "call_result",
                    "schema": SCHEMA,
                    "strict": True,
                },
            },
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - never lose a call over analysis
        log(f"extract: model call failed: {type(exc).__name__}: {exc}")
        return dict(FALLBACK)

    fields = _parse(raw)
    if fields is None:
        log(f"extract: could not parse a record from {raw[:200]!r}")
        return dict(FALLBACK)

    return _coerce(fields)


def _parse(raw: str) -> dict | None:
    """JSON first; then the same text with a code fence peeled off."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # ```json { ... } ``` or a sentence either side of the object.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _coerce(fields: dict) -> dict:
    """Force the model's answer into values Frappe will accept.

    Select fields are validated server-side, so a creative synonym for "Warm"
    would fail the whole write. Falling back to a safe option and flagging for
    review keeps the record.
    """
    out = {**FALLBACK, **{k: v for k, v in fields.items() if k in FALLBACK}}
    flagged = bool(out.get("review_flag"))

    if out["lead_interest"] not in LEAD_INTEREST:
        log(f"extract: unexpected lead_interest {out['lead_interest']!r}")
        out["lead_interest"], flagged = "Cold", True

    if out["primary_objection"] not in OBJECTIONS:
        log(f"extract: unexpected objection {out['primary_objection']!r}")
        out["primary_objection"], flagged = "Other", True

    follow_up, follow_up_ok = _as_datetime(out.get("follow_up_at"))
    if not follow_up_ok:
        # A Datetime field will not take "Tuesday" or "next week". Rather than
        # let one soft field fail the whole write, drop it and flag the record -
        # the phrasing survives in next_action either way.
        log(f"extract: unusable follow_up_at {out['follow_up_at']!r}, dropping it")
        flagged = True
    out["follow_up_at"] = follow_up

    out["review_flag"] = flagged
    for key in ("customer_intent", "requested_product_service", "call_summary", "next_action"):
        out[key] = str(out.get(key) or "").strip()

    name = str(out.get("caller_name") or "").strip()
    out["caller_name"] = name or None

    return out


# What Frappe accepts, plus the shapes a model tends to produce anyway.
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _as_datetime(value: Any) -> tuple[str | None, bool]:
    """Normalise to 'YYYY-MM-DD HH:MM:SS'. Returns (value, was_understood)."""
    if value is None:
        return None, True
    text = str(value).strip()
    if text.lower() in {"", "null", "none"}:
        return None, True

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S"), True
        except ValueError:
            continue
    return None, False
