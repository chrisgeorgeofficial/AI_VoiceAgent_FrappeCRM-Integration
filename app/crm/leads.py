"""Finding the caller in the CRM, and assigning someone to them.

Matching a phone number is fiddlier than it looks. The same person appears as
"+917907703013", "7907703013" and "+91 79077 03013" across a CRM, and the
numbers of two different people can share a suffix. So matching runs in tiers,
most specific first, and only falls back to a looser comparison when the exact
one finds nothing.
"""

import json

import httpx

from app.config import (
    CRM_CREATE_LEADS,
    CRM_TELECALLER_ROLE,
    CRM_NEW_LEAD_SOURCE,
    CRM_NEW_LEAD_STATUS,
    CRM_TELECALLERS,
    FRAPPE_URL,
)
from app.crm.frappe import _headers
from app.logging_utils import log

LEAD_DOCTYPE = "CRM Lead"
LEAD_FIELDS = '["name","lead_name","mobile_no","phone","lead_owner","modified"]'

# Indian mobile numbers are 10 digits; that is the most that can be compared
# without matching two different people who share a country code.
SUFFIX_DIGITS = 10

# Never handed a call, whatever role they hold.
NOT_TELECALLERS = {"Administrator", "Guest"}

# Who got the last unowned call. Tracking the person rather than an index
# means the rotation survives someone being added to or removed from the
# role between calls.
_last_assigned = ""


def digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def find_lead(client: httpx.AsyncClient, phone: str) -> dict | None:
    """The lead this caller is, or None.

    Tier 1 is an exact match on the number as dialled. Tier 2 compares the last
    ten digits, which catches a lead stored without its country code. Where a
    tier returns several, the most recently modified wins - two leads really do
    share a number in practice, and the newer one is the likelier current
    record.
    """
    if not digits(phone):
        return None

    exact = await _query(client, [[LEAD_DOCTYPE, "mobile_no", "=", phone]])
    if exact:
        return _best(exact, phone, "exact number")

    suffix = digits(phone)[-SUFFIX_DIGITS:]
    loose = await _query(client, [[LEAD_DOCTYPE, "mobile_no", "like", f"%{suffix}"]])
    if loose:
        return _best(loose, phone, f"last {SUFFIX_DIGITS} digits")

    return None


def _best(candidates: list[dict], phone: str, how: str) -> dict:
    if len(candidates) > 1:
        names = [f"{c['name']} ({c.get('lead_name')})" for c in candidates]
        log(f"crm: {phone} matches {len(candidates)} leads by {how}: {names}")
    chosen = max(candidates, key=lambda c: c.get("modified") or "")
    log(f"crm: {phone} -> {chosen['name']} ({chosen.get('lead_name')}) by {how}")
    return chosen


async def _query(client: httpx.AsyncClient, filters: list) -> list[dict]:
    import json

    response = await client.get(
        f"{FRAPPE_URL}/api/resource/{LEAD_DOCTYPE}",
        headers=_headers(),
        params={
            "filters": json.dumps(filters),
            "fields": LEAD_FIELDS,
            "limit_page_length": 20,
        },
    )
    response.raise_for_status()
    return response.json().get("data", [])


async def create_lead(
    client: httpx.AsyncClient, phone: str, caller_name: str, owner: str
) -> dict | None:
    """Open a lead for a caller nobody has on file."""
    if not CRM_CREATE_LEADS:
        log("crm: CRM_CREATE_LEADS is false, not creating a lead")
        return None

    payload = {
        # first_name is required, and a number is at least identifiable when
        # the caller never said their name.
        "first_name": caller_name.strip() or phone,
        "status": CRM_NEW_LEAD_STATUS,
        "mobile_no": phone,
    }
    if owner:
        payload["lead_owner"] = owner
    if CRM_NEW_LEAD_SOURCE:
        payload["source"] = CRM_NEW_LEAD_SOURCE

    response = await client.post(
        f"{FRAPPE_URL}/api/resource/{LEAD_DOCTYPE}",
        headers=_headers(),
        json=payload,
    )
    if response.status_code >= 400:
        log(f"crm: could not create a lead: {response.status_code} {response.text[:300]}")
        return None

    created = response.json().get("data", {})
    log(f"crm: created lead {created.get('name')} for {phone}")
    return created


async def fetch_telecallers(client: httpx.AsyncClient) -> list[str]:
    """Everyone currently holding the telecaller role, straight from Frappe.

    Asked per call rather than cached: it is one request against a local
    service, and it means someone added to the role this morning is taking
    calls this afternoon without a restart.
    """
    try:
        response = await client.get(
            f"{FRAPPE_URL}/api/resource/User",
            headers=_headers(),
            params={
                "filters": json.dumps(
                    [
                        ["Has Role", "role", "=", CRM_TELECALLER_ROLE],
                        ["enabled", "=", 1],
                    ]
                ),
                "fields": '["name"]',
                "limit_page_length": 0,
            },
        )
        response.raise_for_status()
        users = [
            row["name"]
            for row in response.json().get("data", [])
            if row["name"] not in NOT_TELECALLERS
        ]
    except Exception as exc:  # noqa: BLE001 - fall back rather than fail the write
        log(f"crm: could not read the {CRM_TELECALLER_ROLE!r} role: "
            f"{type(exc).__name__}: {exc}")
        users = []

    if users:
        return users

    if CRM_TELECALLERS:
        log(f"crm: no users hold {CRM_TELECALLER_ROLE!r}; using CRM_TELECALLERS")
        return list(CRM_TELECALLERS)
    return []


def next_telecaller(pool: list[str]) -> str:
    """The next person in the rotation, given who is available right now."""
    global _last_assigned
    if not pool:
        return ""

    ordered = sorted(pool)
    if _last_assigned in ordered:
        position = (ordered.index(_last_assigned) + 1) % len(ordered)
    else:
        # First call of the process, or the last assignee has gone away.
        position = 0

    _last_assigned = ordered[position]
    return _last_assigned


async def choose_assignee(client: httpx.AsyncClient, lead: dict | None) -> str:
    """Who this call belongs to.

    A caller already on someone's list stays on it - reassigning a known lead
    to whoever happens to be next in the rotation would take them off the
    telecaller who has been working them.
    """
    owner = (lead or {}).get("lead_owner") or ""
    if owner and owner not in NOT_TELECALLERS:
        log(f"crm: {lead['name']} already belongs to {owner}, keeping it there")
        return owner
    if owner:
        # A lead sitting on Administrator was imported or made by hand, not
        # worked by anyone. Treat it as unassigned rather than handing a caller
        # to a system account nobody monitors.
        log(f"crm: {lead['name']} is owned by {owner}, which is not a telecaller")

    pool = await fetch_telecallers(client)
    assignee = next_telecaller(pool)
    if assignee:
        log(f"crm: unowned caller, round-robin picked {assignee} "
            f"from {len(pool)} holding {CRM_TELECALLER_ROLE!r}")
    else:
        log(f"crm: nobody holds {CRM_TELECALLER_ROLE!r} - leaving unassigned")
    return assignee
