# Project Guide

A plain-English explanation of what this system is, followed by the problems we
actually hit while building it and how each was diagnosed and fixed.

---

## Part 1 — What this is, in plain English

Imagine a salesperson who answers every call, never forgets a customer, speaks
both English and Malayalam, and writes perfect notes afterwards.

That is what this is, except it is software.

### What happens when somebody calls

1. **The phone rings.** A customer dials your business number. Twilio — a phone
   company you talk to over the internet — answers it and opens a live audio
   connection to this software.

2. **The software works out who is calling.** It takes the caller's number and
   looks it up in your CRM. If they have called before, it knows their name and
   what was discussed last time. If they are new, it treats them as new.

3. **It says hello — appropriately.** A stranger hears *"Thanks for calling, how
   can I help?"* A returning customer hears *"Hello Rahul, thanks for calling
   back."* Somebody who is already a customer is not sold to again.

4. **It listens and understands.** The caller's speech is turned into text.
   The system works out by itself whether they are speaking English or
   Malayalam — nobody has to tell it.

5. **It thinks of a reply** and speaks it back in the same language the caller
   used. If the caller starts talking over it, it stops — like a person would.

6. **The call ends, and the notes write themselves.** The whole conversation is
   read back and turned into structured information: what they wanted, how
   interested they were, their objection, what to do next, and when to follow
   up. All of it lands in the CRM.

### What lands in the CRM

For every call:

- A **record** with the summary, the caller's intent, how hot the lead is, their
  objection, and the agreed follow-up time
- The **full transcript** as a file
- A **recording** of the call — both voices
- A **link to the lead**, creating one if the caller is new
- A **telecaller assigned**, sharing new callers out fairly, and leaving an
  existing customer with whoever already owns them
- A **follow-up task**, if a time was agreed or the call needs a human to check

### Calling out, not just in

A salesperson can open a lead in the CRM and press **Call with AI Agent**. The
system rings that person, and — because it already knows who it is calling —
introduces itself, says why it is calling, and asks whether now is a good time.
It does not ask questions it already has answers to.

### The manager's view

A dashboard shows total calls, how many completed, average length, how the calls
split between inbound and outbound, which languages were spoken, how interested
the leads were, the most common objections, who is carrying the workload, and
how many follow-ups are due.

### Why it feels quick

On a phone call, silence feels broken. Three things keep it quick:

- Speech is **generated and sent as it is produced**, so the first word plays
  about 0.7 seconds after the agent decides what to say, rather than waiting
  ~2 seconds for the whole sentence
- The lookup in the CRM happens **while other things are still connecting**, so
  it costs no extra waiting
- The caller can **interrupt at any time**

---

## Part 2 — Problems we hit, and how they were fixed

Every one of these cost real debugging time. They are recorded here because the
symptom rarely pointed at the cause.

---

### The agent said nothing

**Symptom.** You call, hear the Twilio trial message, then silence. The server
log looks perfect — greeting generated, audio sent, no errors.

**How we found it.** The log could only show what we *sent*, never what Twilio
did with it. We added a "playback probe": a marker sent right after the first
piece of audio. Twilio echoes a marker back only once the audio before it has
actually played. So every call now ends with one of:

```
twilio: CONFIRMED PLAYING - playing-1 echoed back
!! agent: Twilio never confirmed playing ANY of our audio
```

That turned an unanswerable question into a one-line answer.

**The cause.** The Twilio console showed, on the failing call only:

```
Error 31921  Stream - WebSocket - Close Error (Connection reset without closing handshake)
Warning 15003  Got HTTP 404 response to .../voice/status
```

The free **ngrok tunnel had dropped**. The audio connection was severed, and a
request 90 seconds later hit a tunnel that was not routing. Our server never saw
either — which is exactly why its log looked healthy.

**The fix.** Restart ngrok before any demo. For anything more than a demo, use a
paid static domain or `cloudflared`. This is infrastructure, not code.

**Also worth knowing:** running `uvicorn --reload` produces the identical
signature, because saving a file restarts the process and kills the call.

---

### The agent talked, but the caller heard nothing (earlier, different cause)

**Symptom.** Same silence, but consistent rather than intermittent.

**The cause.** Two separate bugs in how audio was sent to Twilio:

1. **Frame alignment.** Twilio's audio runs on a 20-millisecond clock — 160
   bytes at a time. We were forwarding whatever size the speech service produced
   (100 bytes, 600, 1100…), so most messages carried part of a frame.

2. **Pacing.** We sent 3.6 seconds of audio in a few milliseconds. Twilio's
   documentation says it buffers whatever you send; in practice the caller heard
   nothing.

**How we found it.** An **echo test** — bouncing the caller's own audio straight
back. That was audible, which proved the connection worked and the fault was in
*how* we were sending. The only difference was that the echo was naturally paced
at one frame per 20ms.

**The fix.** Send exactly one 20ms frame per message, and feed them at the speed
they play, staying at most 0.4 seconds ahead.

---

### The agent could not hear the caller at all

**Symptom.** The greeting played, then the agent ignored everything said to it.
The log showed thousands of frames "dropped while speaking".

**The cause.** While the agent speaks, the caller's audio is deliberately
ignored, so the agent does not transcribe its own voice. That mute was only
lifted when Twilio confirmed playback had finished — and that confirmation never
arrived, so the agent stayed deaf for the whole call.

**The fix.** The mute now also lifts on a timer, calculated from the known length
of the audio. The confirmation is still used when it arrives; nothing depends on
it any more.

---

### The greeting was cut off after 2.6 seconds

**Symptom.** On one call the agent stopped mid-greeting although the caller had
said nothing.

**The cause.** Interruption detection listens for sustained loud audio. Line
noise at the very start of a call looked like speech.

**The fix.** A 1.2-second grace period at the start of each spoken clip. Noise
cannot cut the agent off before it has really begun; genuine interruption still
works immediately after.

---

### "₹ 25.00" on the dashboard

**Symptom.** The average call duration — a number of seconds — displayed as a
rupee amount.

**The cause.** Frappe fills in a `currency` field on new number cards and charts
from your System Settings, which is set to India.

**The fix.** Clear `currency` on each card and chart. Note that Frappe reapplies
the default when a card is **created**, so a new one has to be saved twice.

---

### Nothing was written to the CRM

Three different causes, all of which produce the same silence:

| Cause | How to tell |
|---|---|
| Twilio's **status callback URL is not set** on the number | no `POST /voice/status` in the log at all |
| The **DocType name is wrong** | `404` from Frappe in the log |
| A **Select value is not one of the options** | `417` from Frappe, naming the field |

The DocType here is **`AI Voice Agent`**, and Frappe CRM has **no `Task`
doctype** — the one that exists is `CRM Task`, and its due field is `due_date`.

---

### The transcript would not save

**Symptom.** The record saved but the transcript field stayed empty.

**The cause.** `transcript_reference` is an **Attach** field. Attach stores a
*file URL* in a short text column, so a whole conversation cannot fit.

**The fix.** Upload the transcript as a real file attached to the record. Note
that Frappe's upload links the file but does **not** fill the field in — that
takes a second call.

---

### The agent produced nothing but silence in its replies

**Symptom.** The model returned nothing at all, and the log showed
`finish_reason: length`.

**The cause.** `sarvam-105b` is a *reasoning* model. It spent its entire token
budget thinking and never produced an answer.

**The fix.** Use `sarvam-105b-conversations` — four times faster, and it
actually replies.

---

### The voice service rejected everything

**Symptom.** `Model 'bulbul:v2' has been deprecated`, and separately
`Speaker 'shreya' is not compatible with model bulbul:v2`.

**The cause.** Two traps at once: the model was retired, and speakers are tied
to a model version, so a valid-looking speaker name fails at call time.

**The fix.** `bulbul:v3`, where `shreya` is valid.

---

### The server crashed on a rupee sign

**Symptom.** The agent stopped transcribing mid-call, with a
`UnicodeEncodeError`.

**The cause.** Sarvam correctly transcribed "fifty thousand rupees" as
"₹50,000". The Windows console uses an encoding that cannot represent `₹`, so
printing the transcript raised an exception and killed the listener.

**The fix.** Force UTF-8 output. On a system handling Indian languages this was
never optional.

---

### Follow-up dates were wrong, differently each time

**Symptom.** "Call me back Thursday morning" produced 24th, then 23rd, then 25th
across three identical runs.

**The cause.** The model was doing calendar arithmetic. Telling it today's date
did not help; giving it a two-week calendar made it pick the *second* Thursday.

**The fix.** Stop asking it for a date. It now names the **day** (`thursday`,
restricted to a fixed list), and **Python computes the date**. Nine consecutive
runs now give identical, correct answers — and the logic can be tested without
calling anything.

---

### The button in the CRM did nothing

**Symptom.** Clicking **Call with AI Agent** silently failed.

**Two causes.** The CRM runs on port 8000 and the voice agent on 5000, so the
browser blocks the request unless the agent explicitly allows that origin
(CORS). And a trial Twilio account refuses to dial any number not verified as a
caller ID.

---

### A call was assigned to the wrong person

**Symptom.** An outbound call to a lead owned by `telecaller1` was assigned to
somebody else.

**The cause.** Even though we knew which lead we were dialling, the code looked
it up by phone number — and two leads shared that number.

**The fix.** When the lead is already known, read that lead directly. Also, a
lead owned by `Administrator` is treated as unowned, since that is a system
account nobody monitors.

---

## Quick diagnosis

| What you see | Look at |
|---|---|
| No audio from the agent | the last line of the call log — `CONFIRMED PLAYING` or not. If not, restart ngrok |
| Agent ignores you | `dropped while speaking` climbing forever |
| Nothing in the CRM | is `POST /voice/status` in the log at all? |
| Agent replies in the wrong language | `stt: language ... only 0.x confident, ignoring` |
| Agent talks over you | raise `BARGE_IN_RMS`; it is not hearing you as speech |
| Agent stops for no reason | lower `BARGE_IN_RMS`, or raise `BARGE_IN_GRACE_MS` |

**The single most useful habit:** restart ngrok before demonstrating, and run
uvicorn **without** `--reload`. Between them these account for most of the
"it worked yesterday" failures in this project.
