"""Who is on the other end, looked up before the agent speaks.

Until now the CRM was only read *after* a call, to decide where to file it. So
the agent met every caller as a stranger, even one it had spoken to the day
before, and an outbound call - where we know exactly who we are ringing and why
- opened by thanking them for calling us.

This reads the lead, and the last thing that was said to them, in time to shape
the greeting and the brief. It is deliberately forgiving: anything missing just
means an anonymous call, which is what the agent did before.
"""

import json
from dataclasses import dataclass, field

import httpx

from app.config import CALLER_CONTEXT, FRAPPE_DOCTYPE, FRAPPE_TIMEOUT, FRAPPE_URL
from app.crm.frappe import _headers
from app.crm.leads import find_lead, get_lead
from app.logging_utils import log


@dataclass
class Caller:
    """What the agent knows about this person before it says hello."""

    direction: str = "inbound"
    lead: str = ""
    name: str = ""
    status: str = ""
    owner: str = ""
    # A converted lead is an existing customer, not a prospect to qualify.
    customer: bool = False
    previous_calls: int = 0
    last_summary: str = ""
    last_next_action: str = ""
    interests: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return bool(self.lead)

    @property
    def outbound(self) -> bool:
        return self.direction == "outbound"

    @property
    def has_name(self) -> bool:
        """A lead we opened ourselves is named after the number we had.

        Greeting somebody as "Hello plus nine one seven nine zero..." is worse
        than not using a name at all.
        """
        letters = sum(1 for ch in self.name if ch.isalpha())
        return letters >= 2

    @property
    def first_name(self) -> str:
        """Just the given name - honorifics and surnames read oddly aloud."""
        if not self.has_name:
            return ""
        parts = [p for p in self.name.replace(".", " ").split() if len(p) > 2]
        skip = {"mr", "mrs", "ms", "dr", "shri", "smt"}
        for part in parts:
            if part.lower().strip(".") not in skip:
                return part
        return self.name


async def load_caller(phone: str, lead_id: str = "", direction: str = "inbound") -> Caller:
    """Look the caller up. Never raises - an unknown caller is a valid answer."""
    caller = Caller(direction=direction)
    if not CALLER_CONTEXT:
        return caller

    try:
        async with httpx.AsyncClient(timeout=FRAPPE_TIMEOUT) as client:
            # An outbound call already knows the lead; an inbound one has only
            # a number to go on.
            lead = (
                await get_lead(client, lead_id)
                if lead_id
                else await find_lead(client, phone)
            )
            if not lead:
                log(f"caller: {phone or lead_id} is not in the CRM - treating as new")
                return caller

            caller.lead = lead.get("name", "")
            caller.name = (lead.get("lead_name") or lead.get("first_name") or "").strip()
            caller.status = lead.get("status") or ""
            caller.owner = lead.get("lead_owner") or ""
            caller.customer = bool(lead.get("converted"))

            await _add_history(client, caller)

    except Exception as exc:  # noqa: BLE001 - never hold up a ringing phone
        log(f"caller: lookup failed, continuing anonymously: {type(exc).__name__}: {exc}")
        return Caller(direction=direction)

    log(
        f"caller: {caller.name or 'unnamed'} ({caller.lead}), "
        f"status={caller.status}, {caller.previous_calls} previous call(s)"
        + (", already a customer" if caller.customer else "")
    )
    return caller


async def _add_history(client: httpx.AsyncClient, caller: Caller) -> None:
    """What was said last time, so the agent can pick up where it left off."""
    response = await client.get(
        f"{FRAPPE_URL}/api/resource/{FRAPPE_DOCTYPE}",
        headers=_headers(),
        params={
            "filters": json.dumps([[FRAPPE_DOCTYPE, "lead", "=", caller.lead]]),
            "fields": json.dumps(
                ["name", "call_summary", "next_action", "requested_product_service"]
            ),
            "order_by": "creation desc",
            "limit_page_length": 5,
        },
    )
    if response.status_code >= 400:
        return

    calls = response.json().get("data", [])
    caller.previous_calls = len(calls)
    if calls:
        caller.last_summary = (calls[0].get("call_summary") or "").strip()
        caller.last_next_action = (calls[0].get("next_action") or "").strip()
    # The extractor writes a placeholder when the caller never named anything;
    # repeating "Not specified" back at the model is worse than silence.
    placeholders = {"not specified", "none", "n/a", "unknown", "-"}
    seen, interests = set(), []
    for call in calls:
        value = (call.get("requested_product_service") or "").strip()
        key = value.lower()
        if value and key not in placeholders and key not in seen:
            seen.add(key)
            interests.append(value)
    caller.interests = interests[:3]
