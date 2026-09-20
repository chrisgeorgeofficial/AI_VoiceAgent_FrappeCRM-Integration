"""Opening a follow-up task when a call leaves something to do.

Frappe CRM has no plain `Task` doctype - `CRM Task` is the one its lead view
renders, and the tasks already in this instance reference CRM Lead rather than
any custom record. So a follow-up points at the lead, where the telecaller will
actually see it, and names the call record in its description.
"""

import httpx

from app.config import (
    FOLLOW_UP_TASK_STATUS,
    FOLLOW_UP_TASKS,
    FRAPPE_DOCTYPE,
    FRAPPE_URL,
)
from app.crm.frappe import _headers
from app.logging_utils import log

TASK_DOCTYPE = "CRM Task"

# CRM Task's Select options; anything else is rejected server-side.
PRIORITIES = {"Hot": "High", "Warm": "Medium", "Cold": "Low"}


def needs_follow_up(fields: dict) -> bool:
    """Is there anything for a human to do after this call?"""
    return bool(fields.get("follow_up_at") or fields.get("review_flag"))


async def create_follow_up_task(
    client: httpx.AsyncClient,
    call_record: str,
    lead: str,
    assignee: str,
    fields: dict,
) -> str | None:
    """Open a task for the follow-up. Returns its id, or None."""
    if not FOLLOW_UP_TASKS or not needs_follow_up(fields):
        return None

    why = (
        "a follow-up time was agreed"
        if fields.get("follow_up_at")
        else "the call was flagged for review"
    )

    summary = (fields.get("call_summary") or "").strip()
    description = (
        f"<p>{fields.get('next_action') or 'Follow up on this call.'}</p>"
        f"<p>{summary}</p>"
        f"<p><i>Opened automatically from {FRAPPE_DOCTYPE} {call_record}.</i></p>"
    )

    payload = {
        # Titles show in a list, so lead with the action rather than the summary.
        "title": (fields.get("next_action") or "Follow up on AI call")[:140],
        "description": description,
        "status": FOLLOW_UP_TASK_STATUS,
        "priority": PRIORITIES.get(fields.get("lead_interest"), "Medium"),
    }
    if fields.get("follow_up_at"):
        payload["due_date"] = fields["follow_up_at"]
    if assignee:
        payload["assigned_to"] = assignee
    if lead:
        # Point at the lead, not the call record: that is what the CRM's lead
        # view lists, so the telecaller sees it without going looking.
        payload["reference_doctype"] = "CRM Lead"
        payload["reference_docname"] = lead

    response = await client.post(
        f"{FRAPPE_URL}/api/resource/{TASK_DOCTYPE}",
        headers=_headers(),
        json=payload,
    )
    if response.status_code >= 400:
        log(f"!! crm: task refused: {response.status_code} {response.text[:300]}")
        return None

    task = response.json().get("data", {})
    log(f"crm: opened task {task.get('name')} for {assignee or 'nobody'} - {why}")
    return task.get("name")
