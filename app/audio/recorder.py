"""Keep the call audio so it can be attached to the CRM record.

Recorded here rather than asked of Twilio, for one practical reason: a Twilio
recording lives behind Twilio's own authentication, so the URL stored in the
CRM would 401 for whoever clicks it. A file we write ourselves attaches to the
record and downloads like any other attachment.

By default both sides are mixed into a single mono track, so every voice comes
out of whatever the listener is playing it on. Hard-panning them apart reads
well on a waveform, but it means one earbud hears only one person - a poor
default for something a salesperson plays back. RECORDING_STEREO restores the
split for anyone who wants the separation.
"""

import audioop
import io
import wave

from app.audio.codec import (
    SAMPLE_WIDTH,
    TELEPHONY_SAMPLE_RATE,
    ulaw_to_pcm16,
)
from app.config import MAX_RECORDING_SECONDS, RECORDING_STEREO
from app.logging_utils import log

MAX_BYTES = MAX_RECORDING_SECONDS * TELEPHONY_SAMPLE_RATE * SAMPLE_WIDTH


class CallRecorder:
    """Two PCM16 tracks kept in step with each other.

    The caller's track is the clock: Twilio sends inbound frames continuously
    for the whole call, so its length is how far through the call we are. Agent
    audio is written at whatever position the caller's track has reached, which
    is when Twilio will start playing it - so the two line up on playback.
    """

    def __init__(self) -> None:
        self._caller = bytearray()
        self._agent = bytearray()
        self._truncated = False

    def add_caller(self, ulaw: bytes) -> None:
        if len(self._caller) >= MAX_BYTES:
            self._note_truncation()
            return
        self._caller.extend(ulaw_to_pcm16(ulaw))

    def add_agent(self, ulaw: bytes) -> None:
        """Place agent audio at the point in the call where it was sent."""
        if len(self._agent) >= MAX_BYTES:
            self._note_truncation()
            return
        # Silence up to "now", then the speech itself.
        if len(self._agent) < len(self._caller):
            self._agent.extend(bytes(len(self._caller) - len(self._agent)))
        self._agent.extend(ulaw_to_pcm16(ulaw))

    def _note_truncation(self) -> None:
        if not self._truncated:
            log(f"recorder: hit the {MAX_RECORDING_SECONDS}s cap, truncating")
            self._truncated = True

    @property
    def seconds(self) -> float:
        frames = max(len(self._caller), len(self._agent)) // SAMPLE_WIDTH
        return frames / TELEPHONY_SAMPLE_RATE

    @property
    def is_empty(self) -> bool:
        return not self._caller and not self._agent

    def _aligned(self):
        """Both tracks padded to the same length, ending on a whole sample."""
        length = max(len(self._caller), len(self._agent))
        length -= length % SAMPLE_WIDTH
        pad = bytes(1)
        caller = bytes(self._caller).ljust(length, pad)[:length]
        agent = bytes(self._agent).ljust(length, pad)[:length]
        return caller, agent, length

    def to_wav(self) -> bytes:
        """The call as a WAV: both sides mixed, or split across two channels."""
        if self.is_empty:
            return b""

        caller, agent, length = self._aligned()

        if RECORDING_STEREO:
            channels = 2
            stereo = bytearray(length * 2)
            stereo[0::4] = caller[0::2]
            stereo[1::4] = caller[1::2]
            stereo[2::4] = agent[0::2]
            stereo[3::4] = agent[1::2]
            audio = bytes(stereo)
        else:
            channels = 1
            # Halve each side before summing: two voices at full scale clip
            # wherever they overlap, and audioop.add saturates rather than
            # wrapping - which crackles on exactly the interruptions most
            # worth hearing.
            audio = audioop.add(
                audioop.mul(caller, SAMPLE_WIDTH, 0.7),
                audioop.mul(agent, SAMPLE_WIDTH, 0.7),
                SAMPLE_WIDTH,
            )

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(TELEPHONY_SAMPLE_RATE)
            wav.writeframes(audio)
        return buffer.getvalue()
