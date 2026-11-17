"""Option value types: how one option value of a command is read (design §3.2).

Each type turns an AST option value into a Python value or raises `OptionError`. Types
know nothing about lowering: they check the form of a value (a word, a length, a tuple
of known fields), not what it refers to (a style that exists, a file that can be read).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from redocx.props import PropertySet, Target, UnknownPropertyError
from redocx.syntax import ast
from redocx.syntax.source import Span
from redocx.values import Border, InvalidValueError, Length, TabStop

WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


class OptionError(Exception):
    """A value of the wrong form."""

    def __init__(self, message: str, span: Span, help: str | None = None, label: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.span = span
        self.help = help
        self.label = label


class ValueType:
    """The form an option value takes."""

    flag = False
    """Whether the bare option name stands for the value true (`\\section[mirror]`)."""

    def parse(self, value: ast.Value) -> object:
        raise NotImplementedError

    def describe(self) -> str:
        """What the value looks like, for help texts and documentation."""
        raise NotImplementedError


def suggestion(word: str, choices: Sequence[str]) -> str:
    """A "did you mean" label for a misspelt `word`, or ""."""
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f"did you mean `{close[0]}`?" if close else ""


def plain_text(body: ast.MarkupBody) -> str | None:
    """The text of a markup value holding only plain text (None if it has markup)."""
    parts: list[str] = []
    for block in body.blocks:
        if not isinstance(block, ast.Paragraph):
            return None
        for inline in block.content:
            if isinstance(inline, ast.Text):
                parts.append(inline.text)
            elif isinstance(inline, ast.SoftBreak | ast.NonBreakingSpace):
                parts.append(" ")
            else:
                return None
        parts.append(" ")
    return WHITESPACE_RE.sub(" ", "".join(parts)).strip()


def value_text(value: ast.Value) -> str:
    """An option value as style-sheet text: ``(all=(single, 0.5pt), top=none)``."""
    if isinstance(value, ast.TupleValue):
        parts = (
            (f"{item.key}=" if item.key else "") + value_text(item.value) for item in value.items
        )
        return "(" + ", ".join(parts) + ")"
    if isinstance(value, ast.BareValue | ast.StringValue):
        return value.text
    if isinstance(value, ast.MarkupBody):
        return plain_text(value) or ""
    return ""


def text_of(value: ast.Value) -> str:
    """The text of a word, a "string" or plain text in braces (the brace form may span
    lines; line breaks become spaces)."""
    if isinstance(value, ast.BareValue | ast.StringValue):
        return value.text
    if isinstance(value, ast.MarkupBody) and (text := plain_text(value)) is not None:
        return text
    if isinstance(value, ast.ParamValue):
        raise OptionError(f"`#{value.name}` is only valid inside \\define", value.span)
    raise OptionError('expected a word, a "string" or plain text in braces', value.span)


def items_of(value: ast.Value) -> tuple[ast.Option, ...]:
    """The items of a tuple value; any other value is a tuple of one positional item."""
    if isinstance(value, ast.TupleValue):
        return value.items
    return (ast.Option(None, value, span=value.span),)


# --- scalars ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Text(ValueType):
    """A word, a "string" or plain text in braces."""

    allow_empty: bool = False

    def parse(self, value: ast.Value) -> str:
        text = text_of(value)
        if not text and not self.allow_empty:
            raise OptionError("expected some text", value.span)
        return text

    def describe(self) -> str:
        return 'text: a word, a "string" or {plain text}'


BOOL_WORDS = {"on": True, "true": True, "yes": True, "off": False, "false": False, "no": False}


@dataclass(frozen=True)
class MarkupValue(ValueType):
    """Text with markup in braces ({Rich *text*.}), or a word or "string"."""

    def parse(self, value: ast.Value) -> ast.MarkupBody | str:
        if isinstance(value, ast.MarkupBody):
            return value
        return text_of(value)

    def describe(self) -> str:
        return 'text: {markup}, a word or a "string"'


@dataclass(frozen=True)
class Bool(ValueType):
    """on/off, true/false or yes/no; the bare option name means on."""

    flag = True

    def parse(self, value: ast.Value) -> bool:
        text = text_of(value)
        if text not in BOOL_WORDS:
            raise OptionError(
                f"expected on or off, got `{text}`",
                value.span,
                label=suggestion(text, list(BOOL_WORDS)),
            )
        return BOOL_WORDS[text]

    def describe(self) -> str:
        return "on | off"


@dataclass(frozen=True)
class Int(ValueType):
    """A whole number, optionally within bounds."""

    min: int | None = None
    max: int | None = None

    def parse(self, value: ast.Value) -> int:
        text = text_of(value)
        if not re.fullmatch(r"-?\d+", text):
            raise OptionError(f"expected a whole number, got `{text}`", value.span)
        number = int(text)
        if (self.min is not None and number < self.min) or (
            self.max is not None and number > self.max
        ):
            raise OptionError(f"expected {self.describe()}, got {number}", value.span)
        return number

    def describe(self) -> str:
        if self.min is not None and self.max is not None:
            return f"a whole number from {self.min} to {self.max}"
        if self.min is not None:
            return f"a whole number of at least {self.min}"
        return "a whole number"


@dataclass(frozen=True)
class Choice(ValueType):
    """One of a set of words, each mapped to the value the option takes."""

    words: Mapping[str, object] | Callable[[], Sequence[str]]
    """Word -> value, or a function listing the words (each its own value)."""
    aliases: Mapping[str, str] = field(default_factory=lambda: {})
    """Other spellings -> a word."""

    @staticmethod
    def of(*words: str, aliases: Mapping[str, str] | None = None) -> Choice:
        return Choice({word: word for word in words}, aliases or {})

    def choices(self) -> Mapping[str, object]:
        if callable(self.words):
            return {word: word for word in self.words()}
        return self.words

    def parse(self, value: ast.Value) -> object:
        text = text_of(value)
        text = self.aliases.get(text, text)
        choices = self.choices()
        if text not in choices:
            raise OptionError(
                f"unknown value `{text}`",
                value.span,
                help=self.describe(),
                label=suggestion(text, list(choices)),
            )
        return choices[text]

    def describe(self) -> str:
        words = list(self.choices())
        shown = " | ".join(words[:12]) + (" | ..." if len(words) > 12 else "")
        return f"one of {shown}"


@dataclass(frozen=True)
class LengthValue(ValueType):
    """A length such as 12pt or 2.5cm."""

    nonnegative: bool = False

    def parse(self, value: ast.Value) -> Length:
        try:
            length = Length.parse(text_of(value))
        except InvalidValueError as e:
            raise OptionError(str(e), value.span) from None
        if self.nonnegative and length.emu < 0:
            raise OptionError("expected a length of at least 0", value.span)
        return length

    def describe(self) -> str:
        return "a length such as 12pt or 2.5cm"


@dataclass(frozen=True)
class Percent:
    """A percentage of the text width."""

    value: Decimal


@dataclass(frozen=True)
class PercentValue(ValueType):
    """A percentage such as 12.5%, from `min` to `max`."""

    min: Decimal = Decimal(0)
    max: Decimal = Decimal(100)

    def parse(self, value: ast.Value) -> Decimal:
        text = text_of(value)
        try:
            number = Decimal(text.removesuffix("%")) if text.endswith("%") else None
        except InvalidOperation:
            number = None
        if number is None:
            raise OptionError(f"expected a percentage such as 10%, got `{text}`", value.span)
        if not self.min <= number <= self.max:
            raise OptionError(f"expected {self.describe()}, got {text}", value.span)
        return number

    def describe(self) -> str:
        return f"a percentage from {self.min}% to {self.max}%"


@dataclass(frozen=True)
class PositionValue(ValueType):
    """An offset (2cm) or an alignment word (right)."""

    aligns: Sequence[str]

    def parse(self, value: ast.Value) -> Length | str:
        text = text_of(value)
        if text in self.aligns:
            return text
        try:
            return Length.parse(text)
        except InvalidValueError:
            raise OptionError(
                f"expected a length or one of {', '.join(self.aligns)}, got `{text}`",
                value.span,
                label=suggestion(text, list(self.aligns)),
            ) from None

    def describe(self) -> str:
        return f"a length or {' | '.join(self.aligns)}"


@dataclass(frozen=True)
class SizeValue(ValueType):
    """A length, or a percentage of the text width (50%)."""

    def parse(self, value: ast.Value) -> Length | Percent:
        text = text_of(value)
        if text.endswith("%"):
            try:
                return Percent(Decimal(text[:-1]))
            except InvalidOperation:
                raise OptionError(f"invalid percentage `{text}`", value.span) from None
        return LengthValue().parse(value)

    def describe(self) -> str:
        return "a length (12cm) or a percentage of the text width (50%)"


@dataclass(frozen=True)
class PropValue(ValueType):
    """The value of one registry property (`\\u[double]` is the run property underline)."""

    target: Target
    name: str

    def parse(self, value: ast.Value) -> object:
        try:
            return PropertySet(self.target, {self.name: value_text(value)})[self.name]
        except (InvalidValueError, UnknownPropertyError) as e:
            raise OptionError(str(e), value.span) from None

    def describe(self) -> str:
        from redocx.props.registry import REGISTRY

        return REGISTRY[self.target][self.name].values


# --- compound values -------------------------------------------------------------------


@dataclass(frozen=True)
class ListOf(ValueType):
    """A tuple of positional values ``(a, b, c)``; a single value is a list of one.
    With `split_text`, a single text value is split at commas (``keywords="a, b"``)."""

    item: ValueType
    split_text: bool = False

    def parse(self, value: ast.Value) -> tuple[object, ...]:
        if self.split_text and not isinstance(value, ast.TupleValue):
            return tuple(word.strip() for word in text_of(value).split(",") if word.strip())
        out: list[object] = []
        for item in items_of(value):
            if item.key is not None:
                raise OptionError("expected a list of values: (a, b, c)", item.span)
            out.append(self.item.parse(item.value))
        if not out:
            raise OptionError("expected at least one value", value.span)
        return tuple(out)

    def describe(self) -> str:
        return f"a list of {self.item.describe()}"


@dataclass(frozen=True)
class Record(ValueType):
    """A tuple of named fields: ``(count=2, gap=1cm, separator)``. A bare value is the
    `shorthand` field (``columns=2``); a bare field name sets a Bool field."""

    fields: Mapping[str, ValueType]
    shorthand: str | None = None
    required: tuple[str, ...] = ()

    def parse(self, value: ast.Value) -> Fields:
        if not isinstance(value, ast.TupleValue) and self.shorthand is None:
            raise OptionError("expected a tuple", value.span, help=self.describe())
        values: dict[str, object] = {}
        spans: dict[str, Span] = {}
        for item in items_of(value):
            name = item.key
            if name is None and isinstance(item.value, ast.BareValue):
                flag = self.fields.get(item.value.text)
                if flag is not None and flag.flag:
                    name = item.value.text
                    values[name], spans[name] = True, item.span
                    continue
            if name is None:
                name = self.shorthand
                if name is None or name in values:
                    word = item.value.text if isinstance(item.value, ast.BareValue) else ""
                    raise OptionError(
                        f"unexpected value `{word}`" if word else "unexpected value",
                        item.span,
                        help=self.describe(),
                        label=suggestion(word, list(self.fields)) if word else "",
                    )
            if name not in self.fields:
                raise OptionError(
                    f"unknown field `{name}`",
                    item.span,
                    help=self.describe(),
                    label=suggestion(name, list(self.fields)),
                )
            if name in values:
                raise OptionError(f"`{name}` is given twice", item.span)
            values[name] = self.fields[name].parse(item.value)
            spans[name] = item.value.span
        missing = [name for name in self.required if name not in values]
        if missing:
            raise OptionError(f"`{missing[0]}` is missing", value.span, help=self.describe())
        return Fields(values, spans, value.span)

    def describe(self) -> str:
        parts = [name if t.flag else f"{name}=..." for name, t in self.fields.items()]
        return f"({', '.join(parts)})"


@dataclass(frozen=True)
class Toggle(ValueType):
    """on, off, or a tuple of settings that also means on: ``line-numbers=(count-by=5)``.
    Off is None; on is the record's fields (empty for plain on)."""

    record: Record
    flag = True

    def parse(self, value: ast.Value) -> Fields | None:
        if isinstance(value, ast.TupleValue):
            return self.record.parse(value)
        if self.record.shorthand is not None and text_of(value) not in BOOL_WORDS:
            return self.record.parse(value)  # drop-cap=4: the shorthand field
        on = Bool().parse(value)
        return Fields({}, {}, value.span) if on else None

    def describe(self) -> str:
        return f"on | off | {self.record.describe()}"


@dataclass(frozen=True)
class Fields:
    """The fields of a parsed `Record` (and the bound options of a command, `Args`)."""

    values: Mapping[str, object]
    spans: Mapping[str, Span]
    span: Span
    """Where the whole tuple (or command) is."""

    def __contains__(self, name: str) -> bool:
        return name in self.values

    def get[T](self, name: str, kind: type[T], default: T | None = None) -> T | None:
        value = self.values.get(name)
        return value if isinstance(value, kind) else default

    def text(self, name: str, default: str | None = None) -> str | None:
        return self.get(name, str, default)

    def flag(self, name: str) -> bool:
        return self.values.get(name) is True

    def number(self, name: str, default: int | None = None) -> int | None:
        value = self.values.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    def items[T](self, name: str, kind: type[T]) -> tuple[T, ...]:
        value = self.values.get(name)
        if not isinstance(value, tuple):
            return ()
        return tuple(v for v in value if isinstance(v, kind))  # pyright: ignore[reportUnknownVariableType]

    def record(self, name: str) -> Fields | None:
        return self.get(name, Fields)

    def span_of(self, name: str) -> Span:
        return self.spans.get(name, self.span)


# --- specific values -------------------------------------------------------------------


@dataclass(frozen=True)
class PageBorderSpec:
    """`borders=...` of \\section: borders per side (None: no border) and their options."""

    sides: Mapping[str, Border]
    offset_from: str | None = None
    pages: str | None = None


PAGE_BORDER_GROUPS: dict[str, tuple[str, ...]] = {
    "top": ("top",),
    "left": ("left",),
    "bottom": ("bottom",),
    "right": ("right",),
    "all": ("top", "left", "bottom", "right"),
}


@dataclass(frozen=True)
class PageBordersValue(ValueType):
    """``(single, 1pt)`` for all four sides, or ``(all=..., top=..., from=text, pages=first)``;
    ``none`` removes them."""

    def parse(self, value: ast.Value) -> PageBorderSpec:
        items = items_of(value) if isinstance(value, ast.TupleValue) else ()
        known = {*PAGE_BORDER_GROUPS, "from", "pages"}
        if not any(item.key in known for item in items):
            return PageBorderSpec(dict.fromkeys(PAGE_BORDER_GROUPS["all"], self._border(value)))
        sides: dict[str, Border] = {}
        offset_from = pages = None
        for item in items:
            if item.key == "from":
                offset_from = str(Choice.of("page", "text").parse(item.value))
            elif item.key == "pages":
                pages = str(Choice.of("all", "first", "not-first").parse(item.value))
            elif item.key in PAGE_BORDER_GROUPS:
                border = self._border(item.value)
                for side in PAGE_BORDER_GROUPS[item.key]:
                    sides[side] = border
            else:
                word = item.key or text_of(item.value)
                raise OptionError(
                    f"unknown page border part `{word}`",
                    item.span,
                    help=self.describe(),
                    label=suggestion(word, sorted(known)),
                )
        return PageBorderSpec(sides, offset_from, pages)

    @staticmethod
    def _border(value: ast.Value) -> Border:
        words = [
            (f"{item.key}=" if item.key else "") + text_of(item.value) for item in items_of(value)
        ]
        try:
            return Border.parse(" ".join(words))
        except InvalidValueError as e:
            raise OptionError(str(e), value.span) from None

    def describe(self) -> str:
        return (
            "a border such as (single, 1pt, theme.accent1), or "
            "(all=..., top=..., left=..., bottom=..., right=..., from=page|text, "
            "pages=all|first|not-first), or none"
        )


@dataclass(frozen=True)
class PageSize:
    width: Length
    height: Length


@dataclass(frozen=True)
class PageSizeValue(ValueType):
    """A named paper size (A4) or ``(width, height)``."""

    sizes: Mapping[str, PageSize]

    def parse(self, value: ast.Value) -> PageSize:
        if isinstance(value, ast.TupleValue):
            items = value.items
            if len(items) != 2 or any(item.key is not None for item in items):
                raise OptionError("expected (width, height)", value.span)
            width, height = (LengthValue().parse(item.value) for item in items)
            return PageSize(width, height)
        size = Choice(self.sizes).parse(value)
        assert isinstance(size, PageSize)
        return size

    def describe(self) -> str:
        return f"{' | '.join(self.sizes)} | (width, height)"


@dataclass(frozen=True)
class TabStopValue(ValueType):
    """A tab stop: ``(8cm, center)`` or ``(16cm, right, leader=dot)``."""

    def parse(self, value: ast.Value) -> TabStop:
        words = [
            (f"{item.key}=" if item.key else "") + text_of(item.value) for item in items_of(value)
        ]
        try:
            stops = TabStop.parse_list(" ".join(words))
        except InvalidValueError as e:
            raise OptionError(str(e), value.span) from None
        if len(stops) != 1:
            raise OptionError("expected one tab stop: (8cm, center)", value.span)
        return stops[0]

    def describe(self) -> str:
        return "a tab stop such as (8cm, center) or (16cm, right, leader=dot)"


@dataclass(frozen=True)
class Levels:
    first: int
    last: int


@dataclass(frozen=True)
class LevelRange(ValueType):
    """Heading levels ``1-3``, or one level ``2``."""

    def parse(self, value: ast.Value) -> Levels:
        text = text_of(value)
        match = re.fullmatch(r"([1-9])(?:\s*-\s*([1-9]))?", text)
        if match is None:
            raise OptionError(f"expected levels such as 1-3, got `{text}`", value.span)
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        return Levels(min(first, last), max(first, last))

    def describe(self) -> str:
        return "levels such as 1-3"


@dataclass(frozen=True)
class SeqRestart(ValueType):
    """Where a counter restarts: after each ``heading-N``, or at the number ``N``.
    The value is the SEQ field switch (``\\s 1`` or ``\\r 5``)."""

    def parse(self, value: ast.Value) -> str:
        text = text_of(value)
        if match := re.fullmatch(r"heading-([1-9])", text):
            return f"\\s {match.group(1)}"
        if text.isdigit():
            return f"\\r {text}"
        raise OptionError(f"expected heading-N or a number, got `{text}`", value.span)

    def describe(self) -> str:
        return "heading-N | N"


@dataclass(frozen=True)
class RowHeight:
    height: Length
    exact: bool


@dataclass(frozen=True)
class RowHeightValue(ValueType):
    """``1cm`` (at least) or ``(1cm, exact)`` / ``(1cm, at-least)``."""

    def parse(self, value: ast.Value) -> RowHeight:
        values = [item.value for item in items_of(value) if item.key is None]
        if not 1 <= len(values) <= 2 or len(values) != len(items_of(value)):
            raise OptionError("expected a height: 1cm or (1cm, exact)", value.span)
        height = LengthValue().parse(values[0])
        rule = Choice.of("exact", "at-least").parse(values[1]) if len(values) == 2 else ""
        return RowHeight(height, rule == "exact")

    def describe(self) -> str:
        return "1cm | (1cm, exact) | (1cm, at-least)"


@dataclass(frozen=True)
class ColumnWidth:
    kind: str
    """length | percent | fraction"""
    amount: Length | Decimal


@dataclass(frozen=True)
class ColumnWidthValue(ValueType):
    """A column width: a length (4cm), a percentage of the text width (25%), or a
    fraction of what remains (1fr, auto = 1fr)."""

    def parse(self, value: ast.Value) -> ColumnWidth:
        text = text_of(value).strip()
        try:
            if text == "auto":
                return ColumnWidth("fraction", Decimal(1))
            if text.endswith("fr"):
                return ColumnWidth("fraction", Decimal(text[:-2] or "1"))
            if text.endswith("%"):
                return ColumnWidth("percent", Decimal(text[:-1]))
            return ColumnWidth("length", Length.parse(text))
        except (InvalidValueError, ArithmeticError):
            raise OptionError(
                f"invalid column width `{text}`", value.span, help=self.describe()
            ) from None

    def describe(self) -> str:
        return "a length (4cm), a percentage of the text width (25%), or a fraction (1fr)"


@dataclass(frozen=True)
class FlagSet(ValueType):
    """A tuple of words from a set, or `none` for the empty set: ``look=(header-row)``."""

    words: Sequence[str]
    aliases: Mapping[str, str] = field(default_factory=lambda: {})

    def parse(self, value: ast.Value) -> frozenset[str]:
        if isinstance(value, ast.BareValue) and value.text == "none":
            return frozenset()
        choice = Choice.of(*self.words, aliases=self.aliases)
        return frozenset(str(word) for word in ListOf(choice).parse(value))

    def describe(self) -> str:
        return f"none | a tuple of {', '.join(self.words)}"


@dataclass(frozen=True)
class CustomPropertyValue(ValueType):
    """A custom document property: "text" is text; a bare true/false, whole or decimal
    number is a yes/no or number property."""

    def parse(self, value: ast.Value) -> str | bool | int | float:
        text = text_of(value)
        if not isinstance(value, ast.BareValue):
            return text
        if text in ("true", "false"):
            return text == "true"
        if re.fullmatch(r"-?\d+", text) and -(2**31) <= int(text) < 2**31:
            return int(text)
        if re.fullmatch(r"-?\d+\.\d+", text):
            return float(text)
        return text

    def describe(self) -> str:
        return '"text", a number, or true/false'
