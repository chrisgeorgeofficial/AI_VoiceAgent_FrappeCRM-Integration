# API, Webhook and Provider Reference

Every interface this service exposes, and every external one it consumes.
Contracts below are taken from the code, not from vendor documentation — where
the two disagree, a note says so.

- [Part 1 — Endpoints we expose](#part-1--endpoints-we-expose)
- [Part 2 — Twilio](#part-2--twilio)
- [Part 3 — Sarvam AI](#part-3--sarvam-ai)
- [Part 4 — Frappe CRM](#part-4--frappe-crm)
- [Part 5 — Failure behaviour](#part-5--failure-behaviour)

---

## Part 1 — Endpoints we expose

Base URL is the public tunnel, e.g. `https://<ngrok-host>`. Locally, port 5000.

| Method | Path | Called by | Auth |
|---|---|---|---|
| POST | `/voice/incoming` | Twilio | none ⚠ |
| WS | `/voice/media` | Twilio | none ⚠ |
| POST | `/voice/status` | Twilio | none ⚠ |
| POST | `/voice/trigger-outbound` | Frappe CRM button | none (CORS-restricted) |
| GET/POST | `/voice/outbound-connect` | Twilio | none ⚠ |
| GET | `/health` | anything | none |

⚠ Twilio signs every request with `x-twilio-signature`. **We do not currently
validate it**, so anyone who learns the public URL can post to these. See
*Known limitations* in the README.

---

### POST `/voice/incoming`

Twilio's "A call comes in" webhook. Also the only place the caller's phone
number appears, so it is recorded here against the CallSid.

**Request** — `application/x-www-form-urlencoded`. Twilio sends ~30 fields; we
use six:

| Field | Example | Used for |
|---|---|---|
| `CallSid` | `CAa1ab9ccc…` | the key everything else is filed under |
| `From` | `+917907703013` | caller lookup, lead matching |
| `To` | `+19288778493` | the number they dialled |
| `Direction` | `inbound` | recorded on the call |
| `CallStatus` | `ringing` | — |
| `AccountSid` | `AC65ff…` | — |

**Response** — `application/xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://<host>/voice/media"/>
    </Connect>
</Response>
```

`<Connect>` (not `<Start>`) is what makes the stream **bidirectional** — with
`<Start>` Twilio sends audio but ignores anything sent back.

With `USE_MEDIA_STREAM = false` a plain `<Say>` is returned instead, which is
useful for proving the webhook works before involving the agent.

---

### WS `/voice/media`

The live call audio. Twilio connects after the TwiML above. Both directions
carry JSON text frames whose payloads are **base64-encoded μ-law, 8 kHz, mono**.

#### Messages Twilio sends us

| `event` | When | We do |
|---|---|---|
| `connected` | handshake | log |
| `start` | once, first | read `streamSid` + `start.callSid`, load the caller, queue the greeting |
| `media` | ~50/second | decode, record, feed to STT (unless the agent is speaking) |
| `mark` | after our audio has *played* | confirm playback; release the mute |
| `stop` | call ending | flush the transcriber |

```jsonc
// start
{ "event": "start", "streamSid": "MZ…",
  "start": { "callSid": "CA…", "mediaFormat":
             { "encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1 } } }

// media
{ "event": "media", "media": { "track": "inbound", "chunk": "1",
                               "timestamp": "20", "payload": "<base64 μ-law>" } }
```

#### Messages we send Twilio

```jsonc
// audio — one 20ms frame (160 bytes) per message
{ "event": "media", "streamSid": "MZ…", "media": { "payload": "<base64 μ-law>" } }

// bookmark — echoed back once the audio before it has PLAYED
{ "event": "mark", "streamSid": "MZ…", "mark": { "name": "playing-1" } }

// discard audio Twilio has buffered but not yet played (barge-in)
{ "event": "clear", "streamSid": "MZ…" }
```

**Two constraints learned the hard way**, both contradicting the documentation:

1. **One 20 ms frame per message.** Twilio's docs say payloads may be "of any
   size"; in practice, messages carrying partial frames are not played.
2. **Pace it.** The docs say Twilio buffers whatever you send. In practice a
   whole sentence written at once is silent, while the same bytes fed at the
   speed they play are audible. We stay at most **0.4s ahead** of playback.

The `mark` echo is also the only honest signal that the caller heard anything —
a mark sent right after the first frame is used as a playback probe, and its
absence is reported at the end of every call.

---

### POST `/voice/status`

Twilio's status callback. **Must be configured on the phone number** — without
it nothing is ever written to the CRM.

**Request** — form-encoded:

| Field | Example |
|---|---|
| `CallSid` | `CA709d7a8a…` |
| `CallStatus` | `completed`, `no-answer`, `failed`, `busy`, `canceled` |
| `CallDuration` | `108` (seconds) |
| `Direction` | `inbound` / `outbound-api` |
| `From`, `To` | on an outbound call the customer is `To`, not `From` |

**Response** — `200` immediately, empty body. The analysis and CRM write take a
couple of seconds and run in a background task, because Twilio is waiting.

**Idempotent.** Twilio retries callbacks it believes failed; the write is keyed
on `provider_call_id`, so a retry is skipped rather than duplicated — and no
second follow-up task is created.

Only terminal statuses are acted on. `ringing` and `in-progress` are ignored.

---

### POST `/voice/trigger-outbound`

Places a call. Called by the **Call with AI Agent** button on a CRM lead.

**Request** — `application/json`:

```json
{ "phone_number": "+917907703013", "lead_id": "CRM-LEAD-2026-00018" }
```

`lead_id` is optional but strongly preferred: knowing the lead up front means
the agent introduces itself correctly and does not re-ask known facts.

**Response** — `200`:

```json
{ "call_sid": "CAe3bb691b…", "status": "queued" }
```

**Errors**

| Status | Meaning |
|---|---|
| `400` | `phone_number` missing |
| `502` | Twilio refused — body carries Twilio's own message. On a trial account the usual cause is an unverified number |
| `503` | Twilio credentials or `PUBLIC_URL` not configured |

**CORS.** The CRM desk is served from another port, so the browser treats this
as cross-origin. Allowed origins come from `CORS_ORIGINS`, defaulting to
`FRAPPE_URL`. Without it the button fails silently in the browser console.

---

### GET/POST `/voice/outbound-connect?lead_id=…`

TwiML for a call **we** placed; Twilio fetches it when the customer answers.
Returns the same `<Connect><Stream>` document as `/voice/incoming`.

---

### GET `/health`

```json
{ "status": "healthy" }
```

---

## Part 2 — Twilio

### Outbound calls (REST)

`POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Calls.json`
with HTTP Basic auth (`AccountSid` : `AuthToken`), form-encoded:

| Parameter | Value |
|---|---|
| `To` | the customer |
| `From` | `TWILIO_FROM_NUMBER` |
| `Url` | `{PUBLIC_URL}/voice/outbound-connect?lead_id=…` |
| `StatusCallback` | `{PUBLIC_URL}/voice/status` |
| `StatusCallbackMethod` | `POST` |
| `StatusCallbackEvent` | `completed` |

We call this with `httpx` rather than the Twilio SDK — one endpoint, and the
SDK is not otherwise needed.

### Console configuration

| Setting | Value |
|---|---|
| A call comes in | `https://<host>/voice/incoming` · HTTP POST |
| Call status changes | `https://<host>/voice/status` · HTTP POST |

### Error codes worth recognising

| Code | Meaning | Usually |
|---|---|---|
| `31921` | Stream — WebSocket closed without handshake | the tunnel dropped, or the server restarted mid-call |
| `15003` | Webhook returned a non-2xx | a `404` here means the request never reached the app |
| `21219` | number not verified | trial account, outbound |

### Trial account limits

Plays a message before your TwiML runs, and will only dial numbers verified as
caller IDs.

---

## Part 3 — Sarvam AI

One `AsyncSarvamAI` client is shared across the process. Auth is the
`api-subscription-key` from `SARVAM_API_KEY`.

### Speech to text — `saaras:v3`, streaming WebSocket

```python
speech_to_text_streaming.connect(
    language_code="unknown",   # "unknown" = detect it; or a BCP-47 code
    model="saaras:v3",
    mode="transcribe",         # or translate / verbatim / translit / codemix
    sample_rate="8000",        # a STRING, per the SDK's typing
)
socket.transcribe(audio=<base64 WAV>, encoding="audio/wav", sample_rate=8000)
```

Audio is sent as **WAV-wrapped PCM16** in ~1-second chunks; the socket stays
open for the whole call. Responses:

```jsonc
{ "type": "data", "data": {
    "transcript": "…",
    "language_code": "ml-IN",        // only present when you did NOT specify one
    "language_probability": 0.96 } }
```

Below `LANGUAGE_MIN_CONFIDENCE` (0.45) the detection is discarded and the call
continues in whatever language it was already using.

### Chat — `sarvam-105b-conversations`

Replies: `temperature=0.3`, `max_tokens=120`.

Extraction: `temperature=0.0` with native structured outputs —

```python
response_format={"type": "json_schema",
                 "json_schema": {"name": "call_result", "schema": SCHEMA,
                                 "strict": True}}
```

> **Do not use plain `sarvam-105b`.** It is a reasoning model: it spends the
> token budget thinking, returns `content: null` with `finish_reason: length`,
> and takes ~1.2s to do it. The `-conversations` variant answers in ~0.3s.

The schema asks for a follow-up **day name** from a fixed enum, never a date —
the model got calendar arithmetic wrong three different ways across three runs.
The date is computed in Python.

### Text to speech — `bulbul:v3`, streaming WebSocket

```python
text_to_speech_streaming.connect(model="bulbul:v3",
                                 send_completion_event="true")
socket.configure(target_language_code="ml-IN", speaker="shreya",
                 speech_sample_rate=8000, output_audio_codec="mulaw")
```

- `send_completion_event="true"` is **required**, or the socket never signals
  completion and the read hangs.
- `output_audio_codec="mulaw"` returns raw μ-law (`content_type:
  "audio/mulaw"`) — Twilio's exact wire format, so no conversion at all.
- Generation measures ~**3.6× realtime**, first chunk ~**0.5s**.
- An idle socket is closed by the server with **408**, so one socket is opened
  per reply rather than held for the call.

> `bulbul:v2` is **deprecated and refused**. Speakers are tied to a model
> version: `shreya` is valid on v3, rejected on v2.

Languages: `bn, en, gu, hi, kn, ml, mr, od, pa, ta, te` (all `-IN`). Anything
else falls back to `SARVAM_LANGUAGE`.

---

## Part 4 — Frappe CRM

REST at `/api/resource/<DocType>`, header:

```
Authorization: token <api_key>:<api_secret>
```

| Purpose | Call |
|---|---|
| Auth check | `GET /api/method/frappe.auth.get_logged_user` |
| Read schema | `GET /api/resource/DocType/AI Voice Agent` |
| Find a call | `GET /api/resource/AI Voice Agent?filters=[["provider_call_id","=","CA…"]]` |
| Create a call | `POST /api/resource/AI Voice Agent` |
| Match a lead | `GET /api/resource/CRM Lead?filters=[["CRM Lead","mobile_no","=","+91…"]]` |
| Create a lead | `POST /api/resource/CRM Lead` |
| Telecaller pool | `GET /api/resource/User?filters=[["Has Role","role","=","Sales User"],["enabled","=",1]]` |
| Follow-up task | `POST /api/resource/CRM Task` |
| Attach a file | `POST /api/method/upload_file` *(multipart)* |

### Attachments

`transcript_reference` and `recording_reference` are **Attach** fields — they
hold a *file URL* in a short column, so content cannot be written directly.

```
POST /api/method/upload_file
  file=<multipart>, is_private=1,
  doctype=AI Voice Agent, docname=<name>, fieldname=transcript_reference
→ { "message": { "file_url": "/private/files/…" } }
```

`upload_file` links the file to the record but **does not set the field** — that
needs a follow-up `PUT` with the returned `file_url`.

### Traps

- **`Task` does not exist** in Frappe CRM. The doctype is **`CRM Task`**, and
  its due field is `due_date`, not `exp_end_date`.
- **Select fields are validated server-side.** An unlisted value fails the whole
  write with `417`.
- **Datetime fields reject nulls.** Omit the key entirely instead.
- **Filters need a literal date.** `["due_date","<=","Today"]` returns `500`
  through the REST API; use `dynamic_filters_json` with
  `frappe.datetime.get_today()` on dashboard cards.
- **`currency` is auto-filled** on new Number Cards and Dashboard Charts from
  System Settings, rendering plain counts as `₹`. Clear it — and note Frappe
  reapplies the default on *create*, so a new card must be saved twice.

---

## Part 5 — Failure behaviour

Everything after the caller hangs up runs in a background task with nobody left
to hand an exception to, so each stage degrades rather than raises.

| If this fails | Then |
|---|---|
| Sarvam STT socket | the call continues; nothing is transcribed |
| Sarvam TTS mid-reply | that reply is abandoned; the call continues |
| LLM extraction | a placeholder record is written with `review_flag = true` |
| Lead lookup | the call is still recorded, with no lead linked |
| Attachment upload | the record survives without it |
| Frappe unreachable | logged; the call is lost from the CRM |
| Sarvam key missing | the call connects and logs, but the agent never speaks |

Two deliberate choices worth naming: a call that produces **no conversation** is
still recorded (a missed lead is information), and a **failed turn never kills
the session** — the worker logs it and takes the next one.
