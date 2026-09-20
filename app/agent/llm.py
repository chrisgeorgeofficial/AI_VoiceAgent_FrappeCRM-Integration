"""What the agent says back.

Deliberately the smallest, most isolated piece of the loop: nothing else knows
which model produced the words. If Sarvam's replies turn out weak, this one
file changes and the audio pipeline either side of it does not notice.
"""

from sarvamai import AsyncSarvamAI

from app.config import HISTORY_TURNS, SARVAM_CHAT_MODEL
from app.logging_utils import log

# Sarvam's language codes carry a region; the prompt reads better with a name.
_LANGUAGE_NAMES = {
    "bn-IN": "Bengali",
    "en-IN": "English",
    "gu-IN": "Gujarati",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

# Short replies are not a style choice - a caller waiting through four
# sentences of synthesised speech has no way to interrupt.
SYSTEM_PROMPT = (
    "You are a sales enquiry assistant for a demo business, speaking to a "
    "caller on the phone. Find out what they are interested in, and note their "
    "budget, their timeline, and any objection they raise. Ask one question at "
    "a time. {language_rule} Keep every reply to one or two short sentences, "
    "and write them the way they will be read aloud - no bullet points, no "
    "markdown, no emoji."
)

# Callers switch between languages mid-sentence, and Indian English is full of
# borrowed words either way. Naming the language the caller just used beats
# "reply in the same language", which leaves the model guessing from a
# transcript it may have only half understood.
LANGUAGE_RULE = (
    "The caller is speaking {language}, so reply in {language}. If they mix in "
    "English words, that is normal - mirror how they speak rather than "
    "correcting them."
)
DEFAULT_LANGUAGE_RULE = (
    "Reply in the same language the caller is using, mirroring how they speak "
    "if they mix languages."
)

# Anything longer than this is the model ignoring the instruction above; cut it
# off rather than make the caller sit through it.
MAX_REPLY_TOKENS = 120


def system_message(language_code: str = "") -> dict:
    """The agent's brief, told which language this turn is in if we know."""
    language = _LANGUAGE_NAMES.get(language_code)
    rule = (
        LANGUAGE_RULE.format(language=language) if language
        else DEFAULT_LANGUAGE_RULE
    )
    return {"role": "system", "content": SYSTEM_PROMPT.format(language_rule=rule)}


async def get_ai_reply(
    client: AsyncSarvamAI, history: list[dict], language: str = ""
) -> str:
    """Answer the latest turn, given the conversation so far.

    `history` is the alternating user/assistant messages - the system prompt is
    added here so callers never have to remember it.
    """
    messages = [system_message(language), *history[-HISTORY_TURNS:]]

    response = await client.chat.completions(
        messages=messages,
        model=SARVAM_CHAT_MODEL,
        temperature=0.3,
        max_tokens=MAX_REPLY_TOKENS,
    )
    message = response.choices[0].message
    reply = (message.content or "").strip()

    if not reply and getattr(message, "reasoning_content", None):
        # A reasoning model spent the whole budget thinking and never answered.
        log(
            f"llm: {SARVAM_CHAT_MODEL} returned only reasoning - it is a "
            "reasoning model; use sarvam-105b-conversations for voice"
        )

    log(f"llm: reply: {reply!r}")
    return reply
