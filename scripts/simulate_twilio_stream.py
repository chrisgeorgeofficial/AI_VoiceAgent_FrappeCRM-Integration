"""Replay a Twilio Media Stream against our WebSocket endpoint.

Sends the same frame sequence Twilio does - connected, start, media, stop -
so the server can be exercised without placing a real call.

    python scripts/simulate_twilio_stream.py
    python scripts/simulate_twilio_stream.py wss://<your-ngrok-host>/voice/media

Passing the public wss:// URL is the quickest way to prove the tunnel and the
server accept a WebSocket, which separates "our side is broken" from
"Twilio never connected".
"""

import asyncio
import base64
import json
import sys

import websockets

DEFAULT_URL = "ws://127.0.0.1:5000/voice/media"

STREAM_SID = "MZ_TEST_STREAM_123"
CALL_SID = "CA_TEST_CALL_123"
MEDIA_FRAMES = 10

# 160 bytes is roughly 20 ms of 8 kHz mu-law. Silence, not real speech.
SILENT_CHUNK = base64.b64encode(bytes(160)).decode("ascii")


async def simulate(uri: str) -> None:
    print("Connecting to:", uri)
    async with websockets.connect(uri) as ws:
        print("Connected!")

        await _send(ws, {
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0",
        })

        await _send(ws, {
            "event": "start",
            "sequenceNumber": "1",
            "streamSid": STREAM_SID,
            "start": {
                "accountSid": "AC_TEST_ACCOUNT",
                "streamSid": STREAM_SID,
                "callSid": CALL_SID,
                "tracks": ["inbound"],
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
                "customParameters": {},
            },
        })

        for i in range(1, MEDIA_FRAMES + 1):
            await _send(ws, {
                "event": "media",
                "sequenceNumber": str(i + 1),
                "streamSid": STREAM_SID,
                "media": {
                    "track": "inbound",
                    "chunk": str(i),
                    "timestamp": str((i - 1) * 20),
                    "payload": SILENT_CHUNK,
                },
            }, label=f"media chunk {i}")
            await asyncio.sleep(0.02)

        await _send(ws, {
            "event": "stop",
            "sequenceNumber": str(MEDIA_FRAMES + 2),
            "streamSid": STREAM_SID,
            "stop": {"accountSid": "AC_TEST_ACCOUNT", "callSid": CALL_SID},
        })


async def _send(ws, frame: dict, label: str | None = None) -> None:
    await ws.send(json.dumps(frame))
    print("Sent:", label or frame["event"])


if __name__ == "__main__":
    asyncio.run(simulate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
