"""Check the Frappe side before trusting it with a real call.

Run:  myenv\Scripts\python -m scripts.check_crm

Answers four questions in order, stopping at the first that fails:
  1. Are the URL and credentials set?
  2. Do the credentials authenticate?
  3. Does the DocType exist, and do its fields match what we send?
  4. Can a record actually be created, and is the second attempt skipped?
"""

import asyncio

import httpx

from app.config import (
    FRAPPE_API_KEY,
    FRAPPE_API_SECRET,
    FRAPPE_DOCTYPE,
    FRAPPE_TIMEOUT,
    FRAPPE_URL,
)
from app.crm.frappe import _headers, save_call_result

TEST_CALL_ID = "TEST-CHECK-CRM"

# Everything app/crm/writeback.py puts in a payload.
SENT_FIELDS = {
    "provider_call_id", "direction", "language", "call_status", "call_duration",
    "customer_intent", "lead_interest", "requested_product_service",
    "primary_objection", "call_summary", "next_action", "follow_up_at",
    "review_flag", "lead", "assigned_user",
}
# Filled in after creation, by uploading files to the record.
ATTACHED_FIELDS = {"transcript_reference", "recording_reference"}


def bad(message: str) -> bool:
    print(f"  FAIL  {message}")
    return False


async def main() -> None:
    print(f"\nChecking {FRAPPE_URL or '<no FRAPPE_URL>'} / {FRAPPE_DOCTYPE!r}\n")

    print("1. credentials present")
    missing = [n for n, v in (("FRAPPE_URL", FRAPPE_URL),
                              ("FRAPPE_API_KEY", FRAPPE_API_KEY),
                              ("FRAPPE_API_SECRET", FRAPPE_API_SECRET)) if not v]
    if missing:
        bad(f"{', '.join(missing)} empty in .env")
        print("\n  Generate keys at "
              f"{FRAPPE_URL or 'http://127.0.0.1:8000'}/app/user/Administrator"
              " -> API Access -> Generate Keys")
        return
    print("  ok")

    async with httpx.AsyncClient(timeout=FRAPPE_TIMEOUT) as client:
        print("2. credentials authenticate")
        try:
            r = await client.get(
                f"{FRAPPE_URL}/api/method/frappe.auth.get_logged_user",
                headers=_headers(),
            )
        except httpx.RequestError as exc:
            bad(f"cannot reach {FRAPPE_URL}: {exc}")
            return
        if r.status_code != 200:
            bad(f"HTTP {r.status_code} - the key or secret is wrong")
            return
        print(f"  ok - authenticated as {r.json().get('message')}")

        print(f"3. DocType {FRAPPE_DOCTYPE!r} matches the payload")
        r = await client.get(
            f"{FRAPPE_URL}/api/resource/DocType/{FRAPPE_DOCTYPE}",
            headers=_headers(),
        )
        if r.status_code != 200:
            bad(f"HTTP {r.status_code} - is the DocType name exactly right?")
            return

        fields = r.json()["data"].get("fields", [])
        names = {f["fieldname"] for f in fields}
        required = {f["fieldname"] for f in fields
                    if f.get("reqd") and f["fieldname"] not in (SENT_FIELDS | ATTACHED_FIELDS)}
        unknown = (SENT_FIELDS | ATTACHED_FIELDS) - names
        unused = names - SENT_FIELDS - ATTACHED_FIELDS

        if unknown:
            bad(f"we send fields the DocType does not have: {sorted(unknown)}")
        if required:
            bad(f"DocType requires fields we never send: {sorted(required)}")
        if not unknown and not required:
            print(f"  ok - all {len(SENT_FIELDS | ATTACHED_FIELDS)} fields exist")
        if unused:
            print(f"  note: DocType fields we leave empty: {sorted(unused)}")

        unique = [f["fieldname"] for f in fields if f.get("unique")]
        if "provider_call_id" in unique:
            print("  ok - provider_call_id is unique (duplicates blocked at the DB)")
        else:
            print("  note: provider_call_id is NOT marked unique - the idempotency")
            print("        check still runs, but nothing stops a racing duplicate")
        if unknown or required:
            return

    print("4. a record can be created, and a repeat is skipped")
    payload = {
        "provider_call_id": TEST_CALL_ID,
        "direction": "inbound",
        "language": "English",
        "call_status": "completed",
        "call_duration": 12,
        "customer_intent": "Connectivity check",
        "lead_interest": "Cold",
        "requested_product_service": "",
        "primary_objection": "Other",
        "call_summary": "Written by scripts/check_crm.py - safe to delete.",
        "next_action": "None.",
        "transcript_reference": "Agent: test\nCaller: test",
        "review_flag": True,
    }
    transcript = "Agent: test" + chr(10) + "Caller: testing the attachment"
    first = await save_call_result(payload, transcript)
    if not first:
        bad("the write failed - the reason is logged above")
        return
    print(f"  ok - created {first.get('name')}")

    second = await save_call_result(payload, transcript)
    if second and second.get("name") == first.get("name"):
        print("  ok - the repeat was skipped, not duplicated")
    else:
        bad("a second write did not resolve to the same record")

    print(f"\nAll good. Delete the test record at "
          f"{FRAPPE_URL}/app/{FRAPPE_DOCTYPE.lower().replace(' ', '-')}\n")


asyncio.run(main())
