"""Turn a finished conversation into the fields the CRM DocType expects.

The model is asked for a fixed JSON shape rather than prose. Sarvam supports
structured outputs natively (`response_format` with a JSON Schema), which is a
good deal sturdier than asking politely for "only JSON" and hoping - but the
parsing fallbacks below still exist, because a model that wraps its answer in
a code fence should cost us a tidy record, not the whole call.
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from sarvamai import AsyncSarvamAI

from app.config import SARVAM_CHAT_MODEL
from app.logging_utils import log

# These must match the Select options on the DocType exactly - Frappe rejects
# a value that is not one of its options.
LEAD_INTEREST = ["Hot", "Warm", "Cold"]
OBJECTIONS = ["Price", "Timing", "Competitor", "No need", "Other"]

FOLLOW_UP_DAYS = [
    "today", "tomorrow", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "next_week", "in_two_weeks",
    "next_month",
]

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
        "follow_up_when",
        "follow_up_time_of_day",
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
        "follow_up_when": {
            # An enum, not free text: asked for a string the model answered
            # "Thursday morning" - the whole phrase - and the day was lost.
            # The enum fields in this schema have never once come back wrong.
            "type": ["string", "null"],
            "enum": [None, *FOLLOW_UP_DAYS],
            "description": (
                "Which day the caller asked to be contacted, or null if "
                "none was agreed. Name the day only - the time of day goes "
                "in follow_up_time_of_day."
            ),
        },
        "follow_up_time_of_day": {
            "type": ["string", "null"],
            "description": (
                "morning, afternoon, evening, or an exact 24-hour HH:MM. "
                "Null if the caller did not say."
            ),
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
    "It is now {now} ({weekday}). For follow_up_when, answer with the exact day "
    "the caller named: if they said Tuesday, answer 'tuesday', even though that "
    "falls in the week ahead. Only answer 'next_week', 'in_two_weeks' or "
    "'next_month' when the caller named no particular day. Put the time of day "
    "in follow_up_time_of_day, or null if they did not say one. Never work out "
    "a calendar date - that is done for you."
)


def system_prompt() -> str:
    now = datetime.now()
    return SYSTEM_PROMPT.format(
        now=now.strftime("%Y-%m-%d %H:%M:%S"),
        weekday=now.strftime("%A"),
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
    "follow_up_when": None,
    "follow_up_time_of_day": None,
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
            # Nothing here benefits from variety: the same transcript should
            # always classify the same way, and at 0.1 the follow-up day
            # occasionally wandered.
            temperature=0.0,
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

    follow_up = resolve_follow_up(
        out.get("follow_up_when"), out.get("follow_up_time_of_day")
    )
    if out.get("follow_up_when") and follow_up is None:
        # The model named a day nobody recognises. The phrasing survives in
        # next_action; flag it rather than guess at a date.
        log(f"extract: unusable follow_up_when {out['follow_up_when']!r}")
        flagged = True
    out["follow_up_at"] = follow_up

    out["review_flag"] = flagged
    for key in ("customer_intent", "requested_product_service", "call_summary", "next_action"):
        out[key] = str(out.get(key) or "").strip()

    name = str(out.get("caller_name") or "").strip()
    out["caller_name"] = name or None

    return out


WEEKDAYS = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]

# What the caller means by a part of the day, and what a bare day defaults to.
TIMES_OF_DAY = {"morning": 9, "afternoon": 14, "evening": 17}
DEFAULT_HOUR = 10

OFFSETS = {"today": 0, "tomorrow": 1, "next_week": 7, "in_two_weeks": 14,
           "next_month": 30}


def resolve_follow_up(when, time_of_day, now: datetime | None = None) -> str | None:
    """Turn "thursday" + "morning" into a timestamp Frappe will accept.

    The model is asked which day the caller wanted, never which date. Given
    only today's date it computed the rest itself and got "Thursday morning"
    wrong three different ways across three runs; given a fortnight's calendar
    it picked the second Friday instead of the first. Naming a day is something
    it does reliably, so the counting happens here, where it is deterministic
    and testable without calling anything.
    """
    if not when:
        return None

    now = now or datetime.now()
    key = str(when).strip().lower().replace(" ", "_").replace("-", "_")

    # The enum should make this unnecessary, but a phrase like "thursday_morning"
    # still resolves to the right day rather than to nothing at all.
    if key not in OFFSETS and key not in WEEKDAYS:
        for token in (*OFFSETS, *WEEKDAYS):
            if token in key:
                key = token
                break

    if key in OFFSETS:
        day = (now + timedelta(days=OFFSETS[key])).date()
    elif key in WEEKDAYS:
        # The soonest one that has not happened yet; "monday" said on a Monday
        # means the Monday coming, not today.
        ahead = (WEEKDAYS.index(key) - now.weekday()) % 7 or 7
        day = (now + timedelta(days=ahead)).date()
    else:
        try:
            day = datetime.strptime(str(when).strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    hour, minute = DEFAULT_HOUR, 0
    spoken = str(time_of_day or "").strip().lower()
    if spoken in TIMES_OF_DAY:
        hour = TIMES_OF_DAY[spoken]
    elif ":" in spoken:
        try:
            hh, mm = spoken.split(":")[:2]
            hour, minute = int(hh), int(mm)
        except ValueError:
            pass

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = DEFAULT_HOUR, 0

    return f"{day} {hour:02d}:{minute:02d}:00"