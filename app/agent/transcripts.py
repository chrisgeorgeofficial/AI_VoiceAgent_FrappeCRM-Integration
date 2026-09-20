"""What was said on a call, kept until the status callback comes for it.

The media WebSocket and Twilio's status callback are two separate connections.
The socket closes the moment the caller hangs up, taking the `CallSession` with
it - but the status callback, which is what tells us the call is *over* and how
long it lasted, arrives afterwards on a plain HTTP request that knows only the
CallSid. So the conversation has to outlive the socket, keyed by that SID.

In-memory on purpose: a restart losing a few pending write-backs is an
acceptable trade for a demo, and a real deployment would put this in Redis.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.audio.recorder import CallRecorder
from app.config import TRANSCRIPT_CACHE_SIZE
from app.logging_utils import log


@dataclass
class CallRecord:
    """One call's conversation, waiting to be written to the CRM."""

    call_sid: str
    from_number: str = ""
    to_number: str = ""
    direction: str = "inbound"
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    turns: list[dict] = field(default_factory=list)
    recorder: CallRecorder = field(default_factory=CallRecorder)

    def add(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content})

    def as_transcript(self) -> str:
        """The conversation as plain text, which is what the model reads."""
        speaker = {"user": "Caller", "assistant": "Agent"}
        return "\n".join(
            f"{speaker.get(turn['role'], turn['role'])}: {turn['content']}"
            for turn in self.turns
        )

    @property
    def is_empty(self) -> bool:
        return not self.turns


# Ordered so the oldest can be evicted first if callbacks stop arriving.
_calls: "OrderedDict[str, CallRecord]" = OrderedDict()


def start(call_sid: str, **details) -> CallRecord:
    """Open a record for a call, or update the one already opened for it.

    Two things open a record: the /voice/incoming webhook, which is the only
    place the caller's phone number appears, and the media stream's `start`
    event, which is where the conversation begins. Whichever arrives second
    must not wipe what the first one learned.
    """
    record = _calls.get(call_sid)
    if record is None:
        record = CallRecord(call_sid=call_sid)
        _calls[call_sid] = record

    for key, value in details.items():
        if value:
            setattr(record, key, value)

    _calls.move_to_end(call_sid)

    while len(_calls) > TRANSCRIPT_CACHE_SIZE:
        dropped, _ = _calls.popitem(last=False)
        log(f"transcripts: evicted {dropped} - no status callback ever arrived")

    return record


def get(call_sid: str) -> CallRecord | None:
    return _calls.get(call_sid)


def drop(call_sid: str) -> None:
    """Forget a call once its result is safely in the CRM."""
    _calls.pop(call_sid, None)


def pending() -> int:
    return len(_calls)
