"""Smart punctuation: dashes and typographic quotes (language design §3.2)."""

from __future__ import annotations

EN_DASH = "–"
EM_DASH = "—"
NBSP = " "

OPENING_CONTEXT = frozenset(f" \t\r\n([{{{EM_DASH}{EN_DASH}/{NBSP}-")
"""A quote after one of these, or after an opening quote, or at the start opens; otherwise it
closes."""

ENGLISH = "“”‘’"
QUOTE_STYLES = {
    "en": ENGLISH,
    "de": "„“‚‘",
    "cs": "„“‚‘",
    "sk": "„“‚‘",
    "fr": "«»“”",
    "it": "«»“”",
    "es": "«»“”",
    "ru": "«»„“",
    "uk": "«»„“",
    "be": "«»„“",
    "pl": "„”«»",
    "nl": "„”‘’",
    "sv": "””’’",
    "fi": "””’’",
}
"""Primary language subtag -> the quotes `"` and `'` become: outer open, outer close, inner
open, inner close. Other languages use English quotes; Chinese and Japanese input methods type
their own marks. No spaces are added: French spacing is the writer's."""


def quotes_for(language: str | None, override: str | None = None) -> str:
    """The four quote characters for text in `language`, with `override` (`\\document[quotes=]`:
    four characters, or two for the outer pair only) taking precedence."""
    style = QUOTE_STYLES.get((language or "en").split("-")[0].lower(), ENGLISH)
    if override is None:
        return style
    return override if len(override) == 4 else override + style[2:]


def smarten(text: str, previous: str, quotes: str = ENGLISH) -> str:
    """Typographic dashes and quotes. `previous` is the character before `text` ("" at start);
    `quotes` the four characters from `quotes_for`."""
    text = text.replace("---", EM_DASH).replace("--", EN_DASH)
    pairs = {'"': (quotes[0], quotes[1]), "'": (quotes[2], quotes[3])}
    opens = OPENING_CONTEXT | {quotes[0], quotes[2]}
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch == "'" and previous.isalpha() and text[i + 1 : i + 2].isalpha():
            ch = "’"  # an apostrophe, in every language
        elif ch in pairs:
            opening, closing = pairs[ch]
            ch = opening if previous == "" or previous in opens else closing
        out.append(ch)
        previous = ch
    return "".join(out)
