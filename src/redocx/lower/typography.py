"""Smart punctuation: dashes and typographic quotes (language design §3.2)."""

from __future__ import annotations

EN_DASH = "–"
EM_DASH = "—"
NBSP = " "

OPENING_CONTEXT = frozenset(f" \t\r\n([{{{EM_DASH}{EN_DASH}/{NBSP}-")
"""A quote after one of these (or at the start) opens; otherwise it closes."""

QUOTES = {'"': ("“", "”"), "'": ("‘", "’")}


def smarten(text: str, previous: str) -> str:
    """Typographic dashes and quotes. `previous` is the character before `text` ("" at start)."""
    text = text.replace("---", EM_DASH).replace("--", EN_DASH)
    out: list[str] = []
    for ch in text:
        if ch in QUOTES:
            opening, closing = QUOTES[ch]
            ch = opening if previous == "" or previous in OPENING_CONTEXT else closing
        out.append(ch)
        previous = ch
    return "".join(out)
