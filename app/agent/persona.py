"""How the agent opens, and what it is told to do, for this particular call.

Four situations that want four different conversations:

  inbound, stranger   - the job it was built for: find out what they want
  inbound, known lead - they have called before; do not start from zero
  inbound, customer   - a converted lead is not a prospect; help, do not qualify
  outbound            - we rang them. Say who this is and check it is a good time

Previously all four got "Hello! Thanks for calling", which is wrong in three of
them and actively absurd on a call we placed.
"""

from app.config import (
    BUSINESS_NAME,
    GREETING_CUSTOMER,
    GREETING_OUTBOUND,
    GREETING_OUTBOUND_UNKNOWN,
    GREETING_RETURNING,
    GREETING_TEXT,
)
from app.crm.context import Caller

BASE = (
    "You are a sales enquiry assistant for {business}, on a phone call. Ask one "
    "question at a time. {language_rule} Keep every reply to one or two short "
    "sentences, written the way they will be read aloud - no bullet points, no "
    "markdown, no emoji."
)

INBOUND_NEW = (
    "They called you, so let them say what they want before steering. Find out "
    "what they are interested in, and note their budget, their timeline, and "
    "any objection they raise."
)

INBOUND_KNOWN = (
    "They have spoken to you before, so do not introduce the company again and "
    "do not ask for anything already recorded below. Pick up where the last "
    "call left off, and fill only the gaps."
)

INBOUND_CUSTOMER = (
    "They are already a customer, not a prospect. Do not qualify them and do "
    "not ask about budget or timeline. Find out what they need help with and "
    "note it for their account manager."
)

OUTBOUND = (
    "You rang them - they were not expecting this call. Say who you are and why "
    "you are calling, then check whether now is a good time; if it is not, ask "
    "when to call back and let them go politely. Do not ask for anything "
    "already recorded below."
)


def greeting_for(caller: Caller) -> str:
    """The first thing the caller hears."""
    name = caller.first_name
    if caller.outbound:
        template = GREETING_OUTBOUND if name else GREETING_OUTBOUND_UNKNOWN
    elif caller.customer and name:
        template = GREETING_CUSTOMER
    elif caller.known and name:
        template = GREETING_RETURNING
    else:
        template = GREETING_TEXT
    return template.format(name=name, business=BUSINESS_NAME).strip()


def brief_for(caller: Caller, language_rule: str) -> str:
    """The system prompt for this call."""
    if caller.outbound:
        situation = OUTBOUND
    elif caller.customer:
        situation = INBOUND_CUSTOMER
    elif caller.known:
        situation = INBOUND_KNOWN
    else:
        situation = INBOUND_NEW

    parts = [BASE.format(business=BUSINESS_NAME, language_rule=language_rule), situation]
    known = _what_we_know(caller)
    if known:
        parts.append("What you already know about them:\n" + known)
    return " ".join(parts[:2]) + ("\n\n" + parts[2] if known else "")


def _what_we_know(caller: Caller) -> str:
    """Facts the agent must not ask for again."""
    facts = []
    # A lead we opened ourselves is named after the number we rang, which is
    # not a name and must not be read out or reasoned about.
    if caller.has_name:
        facts.append(f"- Their name is {caller.name}.")
    if caller.status:
        facts.append(f"- Their lead status is {caller.status}.")
    if caller.interests:
        facts.append(f"- Previously asked about: {', '.join(caller.interests)}.")
    if caller.previous_calls:
        facts.append(f"- You have spoken {caller.previous_calls} time(s) before.")
    if caller.last_summary:
        facts.append(f"- Last call: {caller.last_summary}")
    if caller.last_next_action:
        facts.append(f"- What was promised: {caller.last_next_action}")
    return "\n".join(facts)
