"""Single chokepoint for the debug output.

Everything prints through `log()` so the whole app can be switched over to
the stdlib `logging` module later by changing this one function.
"""


def log(*parts) -> None:
    print(*parts, flush=True)
