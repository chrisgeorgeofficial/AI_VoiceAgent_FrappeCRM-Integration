"""Single chokepoint for the debug output.

Everything prints through `log()` so the whole app can be switched over to
the stdlib `logging` module later by changing this one function.

It also forces stdout to UTF-8. On Windows the console defaults to cp1252,
which cannot encode a rupee sign - never mind Devanagari or Tamil. Since
transcripts are exactly what this app logs, an un-encodable character would
otherwise raise mid-call and take the STT reader down with it.
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # stdout replaced or already detached
    pass


def log(*parts) -> None:
    try:
        print(*parts, flush=True)
    except UnicodeEncodeError:
        # Last resort: never let a logging call break the call it is logging.
        safe = " ".join(
            str(p).encode("ascii", "backslashreplace").decode("ascii") for p in parts
        )
        print(safe, flush=True)
