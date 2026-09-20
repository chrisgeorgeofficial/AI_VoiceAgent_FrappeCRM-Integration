# AI Voice Agent — Frappe CRM Integration

A bilingual (English / Malayalam) AI voice agent that answers and places phone
calls, holds a sales qualification conversation, and writes the result back into
Frappe CRM as a structured record with a transcript, a call recording, a linked
lead and a follow-up task.

---

## What it does

- **Answers inbound calls.** Recognises the caller from their number, greets a
  returning lead by name, and does not ask for anything already on file.
- **Places outbound calls** from a button on the CRM lead page.
- **Speaks English or Malayalam**, detected per utterance and switched mid-call.
- **Lets the caller interrupt** — the agent stops talking when spoken over.
- **Writes every call to the CRM**: structured analysis, transcript, stereo
  recording, lead link, telecaller assignment, and a follow-up task.
- **Reports on itself** through a Frappe dashboard.

---

## Architecture

```
  caller ──phone──► Twilio ──webhook──► /voice/incoming     (returns TwiML)
                       │
                       └──websocket──► /voice/media         (audio, both ways)
                                          │
                        μ-law 8kHz ◄──────┼──────► Sarvam STT  (saaras:v3)
                                          │        Sarvam LLM  (sarvam-105b-conversations)
                                          │        Sarvam TTS  (bulbul:v3)
                       │
                       └──status────────► /voice/status      (call ended)
                                          │
                                          ▼
                                     Frappe CRM
                              AI Voice Agent · CRM Lead · CRM Task
```

The audio path never waits on a model: inbound frames go to a queue, and a
worker handles transcription, the reply and speech, so a slow model delays the
answer but never stalls the stream.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Telephony | Twilio Media Streams | bidirectional μ-law 8kHz over one WebSocket |
| Speech to text | Sarvam `saaras:v3` | streaming, 8kHz native, auto language detection |
| Language model | Sarvam `sarvam-105b-conversations` | ~0.3s replies; the plain `sarvam-105b` is a reasoning model and too slow |
| Text to speech | Sarvam `bulbul:v3` | emits μ-law at 8kHz directly — no conversion |
| API | FastAPI + uvicorn | one process serves the webhook and the WebSocket |
| CRM | Frappe CRM | REST at `/api/resource/<DocType>` |
| Runtime | Python 3.14 | `audioop-lts` restores `audioop`, removed in 3.13 |

No AI orchestration framework is used. Structured extraction uses Sarvam's
native JSON-Schema support, and follow-up dates are computed in Python rather
than by the model — see [docs/PRODUCTION.md](docs/PRODUCTION.md) for the
reasoning.

---

## Setup

### 1. Frappe CRM

Run Frappe CRM locally (Docker is the quickest route) and confirm the desk is
reachable at `http://127.0.0.1:8000/app`.

### 2. Create the `AI Voice Agent` DocType

Desk → **DocType → New**, name it `AI Voice Agent`, and add:

| Field | Type | Notes |
|---|---|---|
| `provider_call_id` | Data | **tick Unique** — this is what stops duplicates |
| `lead` | Link → CRM Lead | |
| `direction` | Select | `inbound` / `outbound` |
| `language` | Select | `English` / `Malayalam` / `Mixed` |
| `call_status` | Select | `completed` / `no-answer` / `failed` / `transferred` / `other` |
| `call_duration` | Int | seconds |
| `customer_intent` | Small Text | |
| `lead_interest` | Select | `Hot` / `Warm` / `Cold` |
| `requested_product_service` | Data | |
| `primary_objection` | Select | `Price` / `Timing` / `Competitor` / `No need` / `Other` |
| `call_summary` | Text | |
| `next_action` | Small Text | |
| `follow_up_at` | Datetime | |
| `assigned_user` | Link → User | |
| `transcript_reference` | Attach | holds a **file URL**, not text — see [docs/API.md](docs/API.md) |
| `recording_reference` | Attach | |
| `review_flag` | Check | |

The Select options must match exactly — Frappe rejects unknown values.

### 3. Frappe API credentials

Desk → your user → **API Access → Generate Keys**. The secret is shown once.

### 4. Sarvam API key

Sign up at [sarvam.ai](https://sarvam.ai), copy the `api-subscription-key`.

### 5. Twilio

A phone number, plus the Account SID and Auth Token from the console.

### 6. Configure

```bash
cp .env.example .env      # then fill in the blanks
```

The keys that must be set: `SARVAM_API_KEY`, `FRAPPE_URL`, `FRAPPE_API_KEY`,
`FRAPPE_API_SECRET`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`, `PUBLIC_URL`, and `USE_MEDIA_STREAM = true`.

### 7. Install and run

```bash
python -m venv myenv
myenv\Scripts\pip install -r requirements.txt
myenv\Scripts\uvicorn app.main:app --port 5000
```

> Omit `--reload` when taking real calls — a file save restarts the process and
> kills the call's WebSocket mid-conversation.

### 8. Expose it and point Twilio at it

```bash
ngrok http 5000
```

On the Twilio number, set **both**:

| Setting | Value |
|---|---|
| A call comes in | `https://<ngrok-host>/voice/incoming` (HTTP POST) |
| Call status changes | `https://<ngrok-host>/voice/status` (HTTP POST) |

Without the second one, nothing is ever written to the CRM.

### 9. The "Call with AI Agent" button

Desk → **Client Script → New**, DocType `CRM Lead`, Apply to `Form`, and paste
[docs/crm_call_button.js](docs/crm_call_button.js).

### 10. Dashboard

The manager dashboard lives at
`/app/dashboard-view/AI Voice Agent Dashboard`.

---

## Verifying the setup

```bash
myenv\Scripts\python -m scripts.check_crm
```

Checks credentials, authentication, that every field the code writes exists on
the DocType, and that a record can be created and a repeat correctly skipped.

```bash
myenv\Scripts\python scripts\simulate_twilio_stream.py wss://<ngrok-host>/voice/media
```

Replays a Twilio media stream without placing a call — separates "our side is
broken" from "Twilio never connected".

---

## Demo accounts

Frappe users are created during setup; passwords are whatever you set then.

---

## Documentation

| | |
|---|---|
| [docs/API.md](docs/API.md) | every endpoint we expose and every provider API we consume, with request/response shapes and the vendor quirks that matter |
| [docs/PRODUCTION.md](docs/PRODUCTION.md) | what is ready, what is not, and what to fix first before real traffic |
| [docs/GUIDE.md](docs/GUIDE.md) | what the system does in plain English, plus the problems hit while building it and how each was diagnosed |
| [docs/crm_call_button.js](docs/crm_call_button.js) | the Frappe Client Script for the "Call with AI Agent" button |

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/voice/incoming` | Twilio call webhook; returns TwiML |
| WS | `/voice/media` | the audio stream, both directions |
| POST | `/voice/status` | call ended — triggers the CRM write-back |
| POST | `/voice/trigger-outbound` | place a call (`{phone_number, lead_id}`) |
| GET/POST | `/voice/outbound-connect` | TwiML for a call we placed |
| GET | `/health` | liveness |

---

---

## Known limitations

**This is a working prototype, not production software.** The full assessment —
blockers, capacity limits, data-loss paths, privacy and a prioritised fix list —
is in [docs/PRODUCTION.md](docs/PRODUCTION.md).

The three things to fix before any real traffic:

1. **The Twilio webhook signature is not validated.** Anyone who learns the
   public URL can post fabricated calls.
2. **`/voice/trigger-outbound` has no authentication.** CORS restricts browsers;
   it is not access control, and any non-browser client ignores it.
3. **A free ngrok tunnel drops calls silently** — the caller hears nothing while
   the server log shows no error.

And two that shape what it can do today:

- **It runs as a single process.** Call state is held in memory, so
  `--workers 2` would silently lose call data.
- **The agent can invent facts** — in testing it quoted a price nobody gave it.
  Put your real price list in the prompt before letting it near customers.
