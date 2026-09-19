"""One shared Sarvam client for the whole process.

The SDK holds an httpx connection pool, so building a client per call would
throw away warm connections on every ring. It is created lazily rather than at
import so the app still boots - and still serves /health - when the key is
missing.
"""

from sarvamai import AsyncSarvamAI

from app.config import SARVAM_API_KEY
from app.logging_utils import log

_client: AsyncSarvamAI | None = None


class MissingApiKey(RuntimeError):
    """Raised when SARVAM_API_KEY is absent, with the fix in the message."""


def get_client() -> AsyncSarvamAI:
    global _client
    if _client is None:
        if not SARVAM_API_KEY:
            raise MissingApiKey(
                "SARVAM_API_KEY is not set - put your api-subscription-key in .env"
            )
        log("sarvam: creating shared async client")
        _client = AsyncSarvamAI(api_subscription_key=SARVAM_API_KEY)
    return _client
