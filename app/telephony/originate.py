"""Placing a call, rather than waiting for one.

Twilio's REST API is a form POST with basic auth, so this uses httpx directly
rather than pulling in the Twilio SDK - the same shape as the Frappe client,
and one less dependency for a single endpoint. Swap in `twilio.rest.Client` if
you later want its webhook-signature helpers.
"""

import httpx

from app.config import (
    PUBLIC_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
)
from app.logging_utils import log

TWILIO_API = "https://api.twilio.com/2010-04-01"


class TwilioNotConfigured(RuntimeError):
    """Raised when credentials or the public URL are missing."""


def _check_config() -> None:
    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID),
            ("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN),
            ("TWILIO_FROM_NUMBER", TWILIO_FROM_NUMBER),
            ("PUBLIC_URL", PUBLIC_URL),
        )
        if not value
    ]
    if missing:
        raise TwilioNotConfigured(
            f"{', '.join(missing)} not set - outbound calling needs the Twilio "
            "console credentials and the public https URL Twilio should fetch"
        )


async def place_call(to_number: str, lead_id: str = "") -> dict:
    """Ring `to_number` and connect it to the agent when answered.

    On a trial account Twilio will only dial numbers verified as caller IDs,
    so this reaches your own test phone and refuses anything else - which is
    all the demo needs.
    """
    _check_config()

    answer_url = f"{PUBLIC_URL}/voice/outbound-connect"
    if lead_id:
        answer_url += f"?lead_id={lead_id}"

    payload = {
        "To": to_number,
        "From": TWILIO_FROM_NUMBER,
        "Url": answer_url,
        "StatusCallback": f"{PUBLIC_URL}/voice/status",
        "StatusCallbackMethod": "POST",
        # Only the terminal event matters; the write-back ignores the rest.
        "StatusCallbackEvent": "completed",
    }

    log(f"twilio: dialling {to_number} for lead {lead_id or '<none>'}")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TWILIO_API}/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data=payload,
        )

    if response.status_code >= 400:
        # Twilio's errors are specific and worth keeping - "unverified number"
        # on a trial account looks like a generic 400 otherwise.
        log(f"twilio: call refused: {response.status_code} {response.text[:400]}")
        response.raise_for_status()

    call = response.json()
    log(f"twilio: call {call.get('sid')} is {call.get('status')}")
    return call
