# Production Readiness and Known Limitations

An honest assessment of what this system is and is not ready for. Everything
below was verified against the code, not assumed.

**Current status: a complete, working prototype.** The full pipeline runs
end to end — inbound and outbound calls, bilingual conversation, CRM write-back
with transcript and recording, follow-up tasks, and a manager dashboard. It is
suitable for demonstration and evaluation. It is **not** yet suitable for
carrying real customer calls, for the reasons below.

---

## 1. Blockers — fix before any real traffic

### 1.1 Webhook requests are not authenticated

Twilio signs every request with `x-twilio-signature`. **We never check it.**
Anyone who learns the public URL can post fake calls, fabricate transcripts, and
create CRM records.

> Fix: validate the signature on `/voice/incoming` and `/voice/status`. Twilio's
> `RequestValidator` does it in a few lines.

### 1.2 `/voice/trigger-outbound` has no authentication at all

CORS restricts which *browsers* will call it. **CORS is not access control** — a
`curl` or any non-browser client ignores it entirely. Today, anyone who can
reach the URL can make your Twilio account dial any number, at your cost.

> Fix: a shared secret header, or an API key checked server-side, before this
> endpoint is exposed anywhere.

### 1.3 ngrok is not a production transport

A free tunnel drops without warning. When it does, the media WebSocket is reset
mid-call and the caller hears silence — with the server log showing no error at
all. This was the single largest source of lost time in this project.

> Fix: deploy behind real TLS on a stable hostname. Keep the playback probe;
> it is what makes this failure visible.

### 1.4 Secrets live in a plaintext `.env`

Sarvam key, Twilio auth token, and Frappe API secret sit unencrypted on disk.

> Fix: a secret manager, or at minimum environment variables injected by the
> platform and never written to a file.

---

## 2. It can only run as a single process

This is the most important architectural constraint, and it is easy to miss.

Three pieces of state live in module-level memory:

| State | Where | Breaks how |
|---|---|---|
| Call transcripts and recordings | `transcripts.py` `_calls` | the status callback may land on a different worker than the call did — the write-back then finds nothing |
| Round-robin position | `leads.py` `_last_assigned` | each worker rotates independently, so assignment is uneven |
| Sarvam client | `client.py` `_client` | harmless; just one connection pool per worker |

**Running `uvicorn --workers 2` will silently lose call data.** The WebSocket
and the status callback are separate HTTP connections and are load-balanced
independently.

> Fix: move the call store to Redis, keyed by `CallSid`, before scaling out.
> Round-robin should become an atomic counter in the same place.

---

## 3. Capacity

Audio is buffered in RAM for the length of each call:

- A recording is two PCM16 tracks at 8kHz — roughly **32 KB per second**
- The cap is 10 minutes, so **up to ~19 MB per concurrent call**
- Finished calls are held until their status callback arrives, up to **200**

Ten concurrent ten-minute calls is therefore ~190 MB of audio alone, before the
Python overhead. There is no admission control: the eleventh call is accepted
regardless.

**There is no maximum call duration.** A call that never hangs up runs until
Twilio ends it. The recording stops at 10 minutes; the call, the STT socket and
the token spend do not.

> Fix: stream recordings to disk rather than holding them, cap concurrent calls,
> and add a hard call-duration limit.

---

## 4. Data loss paths

| Scenario | Consequence today |
|---|---|
| Server restarts between the call ending and the status callback | that call is never written to the CRM |
| Status callback returns non-2xx (e.g. tunnel down) | Twilio does not retry a 404 — the call is lost |
| Frappe unreachable during write-back | logged, then dropped; no retry, no queue |
| Attachment upload fails | the record survives without the transcript or recording |

The write itself is **idempotent** on `provider_call_id`, so a retry would be
safe — there is simply nothing that retries.

> Fix: persist pending write-backs and retry with backoff. A durable queue would
> close all four rows at once.

---

## 5. Observability

- Logging is `print()` through one helper. No levels, no structure, no rotation.
- No metrics: call volume, turn latency, STT/TTS failure rate, write-back
  success are all invisible except by reading the console.
- No alerting. A silent-call outage is noticed by a human trying a call.
- The one bright spot: every call reports whether Twilio confirmed playing our
  audio, which is genuine ground truth rather than an inference.

> Fix: structured JSON logs with the CallSid as correlation id; counters and
> latency histograms; an alert on "no confirmed playback" rate.

---

## 6. Security and privacy

**Calls are recorded without any spoken notice.** Recording consent is legally
significant in many jurisdictions, including for business calls in India. This
is a compliance question, not a technical one, and it needs answering before
real calls are taken.

- **Transcripts and recordings are stored indefinitely** in Frappe as private
  files. There is no retention or deletion policy.
- **PII is printed to the console** — phone numbers and full transcripts. Those
  logs are usually the least protected thing in a deployment.
- **Caller recognition trusts caller ID**, which is spoofable. A spoofed number
  gets greeted by name and told what was discussed last time — a small but real
  information leak. `CALLER_CONTEXT = false` disables it.
- **No rate limiting** on any endpoint.

---

## 7. Cost exposure

Every call spends money on Twilio minutes and Sarvam tokens, with no ceiling:

- No cap on concurrent calls
- No maximum call duration
- No daily budget guard or spend alert
- The outbound endpoint, being unauthenticated (§1.2), is a direct route to
  running up a Twilio bill

---

## 8. Behaviour limits

**The agent can invent facts.** In a real test call it told the caller *"our
plans start from one lakh rupees"* — a price nobody gave it. For a qualification
bot this is the most commercially dangerous limitation here: it will state
prices, timelines and capabilities that are not true.

> Fix: put the real product and price list in the system prompt, and instruct it
> to say it will check rather than answer anything not listed.

Other limits:

- **No escalation to a human.** The DocType has a `transferred` status and
  nothing implements it. A caller who asks for a person cannot get one.
- **No DTMF** — keypad input is ignored.
- **No answering-machine detection** on outbound, so the agent will happily talk
  to a voicemail greeting.
- **Language detection is per utterance.** Heavily code-switched speech may be
  classified either way; a low-confidence turn continues in the language already
  in use.
- **Barge-in is energy-based**, so a noisy line can interrupt the agent and a
  very quiet caller may fail to. Tunable via `BARGE_IN_RMS` and
  `BARGE_IN_GRACE_MS`, not adaptive.
- **Converted leads are still matched** by phone, so an existing customer links
  to their old lead rather than creating a new enquiry.
- **Only English and Malayalam** are wired into the CRM's language field, though
  Sarvam supports eleven; anything else is recorded as `Mixed`.

---

## 9. Testing

**The test suites are not in the repository.** Twelve suites exist and all pass
— covering framing, pacing, barge-in, the playback probe, the CRM write-back,
lead matching, the telecaller pool and the outbound flow — but they live in a
temporary scratch directory and will be lost.

There is also no CI, and no automated check that the code still matches the
Frappe DocType (`scripts/check_crm.py` does this, but only when run by hand).

> Fix: move the suites into `tests/` as pytest files and run them on every push.
> This is the cheapest item on this page and the one most likely to be regretted.

---

## 10. If you had one week

In the order I would actually do them:

1. **Move the tests into the repo** (half a day) — everything else risks
   breaking what already works
2. **Validate the Twilio signature; authenticate the outbound endpoint**
   (half a day) — closes both open doors
3. **Deploy off ngrok onto stable TLS** (one day) — removes the largest source
   of mystery failures
4. **Move the call store to Redis** (one day) — unblocks running more than one
   worker
5. **Retry the CRM write-back from a durable queue** (one day) — closes every
   data-loss path in §4
6. **Put the real price list in the prompt** (one hour) — stops the agent
   inventing commercial terms
7. **Structured logs and a playback-failure alert** (one day)

Items 1–3 are what separate "a working prototype" from "safe to point at real
customers". Items 4–5 are what separate that from "more than one call at a
time".

---

## 11. What is genuinely solid

Worth stating plainly, since the list above is long:

- The **audio path** is correct and measured: 20ms frame alignment, paced to
  real time, μ-law end to end with no unnecessary conversion
- **Latency** is good for a telephony agent — ~0.5s to first audio, ~1.7s
  end-to-end per turn
- **Language detection and switching** work mid-call, verified in both
  directions
- **The CRM write is idempotent**, so Twilio's retries cannot duplicate records
  or tasks
- **Follow-up dates are computed in Python**, not by the model — deterministic
  and unit-tested, after the model proved unreliable at calendar arithmetic
- **Every failure path degrades rather than crashes**: a bad turn, a dead STT
  socket or an unreachable CRM never takes the call down
- **Playback is verified per call**, which most voice agents cannot say
