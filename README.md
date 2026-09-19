# AI Voice Agent — Frappe CRM Integration

AI voice agent ecosystem for an existing Frappe CRM environment. The agent handles
both incoming and outgoing calls in English and Malayalam, connects through a real
telephony/voice service, tracks every call, and automatically writes structured call
data back into the CRM for segmentation, follow-up and reporting.

## Project layout

```
app/
  main.py              FastAPI app factory. Entry point: app.main:app
  config.py            Every environment-driven setting, read once
  logging_utils.py     Single log() chokepoint, swap for stdlib logging later
  routers/
    health.py          GET /  and  GET /health
    voice.py           POST /voice/incoming  and  WS /voice/media
  telephony/
    twiml.py           Build + sanity-check the TwiML sent to Twilio
    media_stream.py    Consume the Twilio Media Stream WebSocket
scripts/
  simulate_twilio_stream.py   Replay a Twilio stream without placing a call
```

Later stages slot in alongside `telephony/`: `speech/` for Sarvam STT/TTS,
`agent/` for the conversation loop, and `crm/` for the Frappe writeback.

## Setup

```powershell
myenv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Running

```powershell
uvicorn app.main:app --reload --port 5000
```

Expose it so Twilio can reach it, and point the number's **A call comes in**
webhook at `https://<ngrok-host>/voice/incoming`:

```powershell
ngrok http 5000
```

ngrok free URLs change on every restart, so the webhook URL has to be updated
in the Twilio console each time. The `wss://` host follows automatically,
because it is read from the incoming request's `Host` header.

## Endpoints

| Method | Path              | Purpose                                      |
| ------ | ----------------- | -------------------------------------------- |
| GET    | `/`               | Liveness check                                |
| GET    | `/health`         | Liveness check                                |
| POST   | `/voice/incoming` | Twilio call webhook; returns TwiML            |
| WS     | `/voice/media`    | Twilio Media Stream; receives mu-law audio    |

`USE_MEDIA_STREAM` decides which TwiML `/voice/incoming` returns — `<Say>` when
false, `<Connect><Stream>` when true.

## Testing without a phone call

Start the server, then replay a Twilio stream against it:

```powershell
python scripts/simulate_twilio_stream.py
python scripts/simulate_twilio_stream.py wss://<ngrok-host>/voice/media
```

Passing the public `wss://` URL proves the tunnel and server accept a WebSocket,
which separates "our side is broken" from "Twilio never connected".

## Debugging a call

Every webhook logs the request headers, Twilio's form data, how the `wss://`
host was resolved, and the exact TwiML returned. Watch for:

- `ok: Stream url uses wss://` — the URL is well-formed
- `>>> WS /voice/media connection attempt` — Twilio actually opened the socket

If the TwiML looks right but no WS block follows, the failure is on Twilio's
side; check **Monitor → Logs → Errors** in the Twilio console for that CallSid.
