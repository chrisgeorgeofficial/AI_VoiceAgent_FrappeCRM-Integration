"""Write call results into Frappe CRM.

Frappe exposes every DocType as REST at /api/resource/<DocType>, authenticated
with a single `token key:secret` header - there is no client library to learn.

The one rule that matters here is idempotency. Twilio retries status callbacks,
and a retry must not produce a second record for the same call, so every write
is preceded by a lookup on `provider_call_id`. That is a check-then-write race
in principle; in practice the DocType's unique constraint on that field is the
real guarantee, and the lookup just keeps the happy path quiet.
"""

import httpx

from app.config import (
    FRAPPE_API_KEY,
    FRAPPE_API_SECRET,
    FRAPPE_DOCTYPE,
    FRAPPE_TIMEOUT,
    FRAPPE_URL,
)
from app.logging_utils import log


class FrappeNotConfigured(RuntimeError):
    """Raised when the URL or credentials are missing, with the fix included."""


def _resource_url() -> str:
    # Frappe accepts the DocType name with spaces; httpx encodes it.
    return f"{FRAPPE_URL}/api/resource/{FRAPPE_DOCTYPE}"


def _headers() -> dict:
    missing = [
        name
        for name, value in (
            ("FRAPPE_URL", FRAPPE_URL),
            ("FRAPPE_API_KEY", FRAPPE_API_KEY),
            ("FRAPPE_API_SECRET", FRAPPE_API_SECRET),
        )
        if not value
    ]
    if missing:
        raise FrappeNotConfigured(
            f"{', '.join(missing)} not set - generate keys under the Frappe "
            "user's API Access section and put them in .env"
        )
    return {
        "Authorization": f"token {FRAPPE_API_KEY}:{FRAPPE_API_SECRET}",
        "Accept": "application/json",
    }


async def find_call_result(client: httpx.AsyncClient, provider_call_id: str) -> dict | None:
    """Has this call already been recorded?"""
    response = await client.get(
        _resource_url(),
        headers=_headers(),
        params={
            "filters": f'[["provider_call_id","=","{provider_call_id}"]]',
            "fields": '["name","provider_call_id"]',
            "limit_page_length": 1,
        },
    )
    response.raise_for_status()
    found = response.json().get("data", [])
    return found[0] if found else None


async def create_call_result(client: httpx.AsyncClient, payload: dict) -> dict:
    """Create the record. Raises on anything Frappe refuses."""
    response = await client.post(_resource_url(), headers=_headers(), json=payload)
    if response.status_code >= 400:
        # Frappe's error bodies carry the useful part - which field it disliked
        # and why - and losing that to a bare status code wastes an afternoon.
        log(f"frappe: {response.status_code} rejected the record: {response.text[:600]}")
    response.raise_for_status()
    return response.json().get("data", {})


async def attach_file(
    client: httpx.AsyncClient,
    docname: str,
    fieldname: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> str | None:
    """Upload the transcript as a file and point the record's field at it.

    The Attach fields hold a file URL in a varchar(140), so neither a
    conversation nor a WAV can go in directly - they become real attachments on
    the record, which is also nicer for whoever opens it.

    `upload_file` links the file to the record but does not fill the field in,
    so that takes a second call.
    """
    upload = await client.post(
        f"{FRAPPE_URL}/api/method/upload_file",
        headers=_headers(),
        files={"file": (filename, content, content_type)},
        data={
            "is_private": "1",
            "doctype": FRAPPE_DOCTYPE,
            "docname": docname,
            "fieldname": fieldname,
        },
    )
    upload.raise_for_status()
    file_url = upload.json()["message"]["file_url"]

    linked = await client.put(
        f"{_resource_url()}/{docname}",
        headers=_headers(),
        json={fieldname: file_url},
    )
    linked.raise_for_status()
    return file_url


async def save_call_result(
    payload: dict, transcript: str = "", recording: bytes = b""
) -> dict | None:
    """Write one call result, skipping it if this call is already recorded.

    Returns the record, or None if it could not be written. Never raises: this
    runs in a background task after the caller has already hung up, and there
    is nobody left to hand an exception to.
    """
    call_id = payload.get("provider_call_id", "")

    try:
        async with httpx.AsyncClient(timeout=FRAPPE_TIMEOUT) as client:
            existing = await find_call_result(client, call_id)
            if existing:
                log(f"frappe: {call_id} already recorded as {existing['name']}, skipping")
                return existing

            created = await create_call_result(client, payload)
            name = created.get("name")
            log(f"frappe: wrote {name} for {call_id}")

            # The analysis is already saved; losing an attachment is a
            # shame, not a reason to fail the whole write.
            if name and transcript:
                await _try_attach(
                    client, name, "transcript_reference",
                    f"{name}-transcript.txt", transcript.encode("utf-8"),
                    "text/plain",
                )
            if name and recording:
                await _try_attach(
                    client, name, "recording_reference",
                    f"{name}-recording.wav", recording, "audio/wav",
                )

            return created

    except FrappeNotConfigured as exc:
        log(f"!! frappe: {exc}")
    except httpx.HTTPStatusError as exc:
        log(f"!! frappe: {call_id} refused with HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        log(f"!! frappe: cannot reach {FRAPPE_URL}: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a lost record must not crash the app
        log(f"!! frappe: unexpected failure for {call_id}: {type(exc).__name__}: {exc}")

    return None


async def _try_attach(
    client: httpx.AsyncClient,
    docname: str,
    fieldname: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    try:
        url = await attach_file(
            client, docname, fieldname, filename, content, content_type
        )
        log(f"frappe: attached {fieldname} at {url} ({len(content)} bytes)")
    except Exception as exc:  # noqa: BLE001 - the record matters more
        log(f"!! frappe: could not attach {fieldname}: {type(exc).__name__}: {exc}")
