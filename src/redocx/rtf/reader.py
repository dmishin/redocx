r"""Read RTF into the document IR: text, character and paragraph formatting, tables,
pictures, hyperlinks and bookmarks.

The reader keeps the formatting RTF gives; named styles (`\sN`) are not kept, because RTF
writes each style's formatting on the text as well. Lists keep their numbers and bullets as
text (`\listtext`, `\pntext`), which RTF writers add for readers without lists. Footnotes,
headers and footers, comments and shapes are left out, embedded objects keep their picture
and nested tables their text; the report counts each.
"""

from __future__ import annotations

import locale
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from os import PathLike
from pathlib import Path

from redocx import ir
from redocx.ooxml.reader import ImportReport, ImportResult
from redocx.props import PropertySet, Target
from redocx.rtf.pictures import Picture
from redocx.rtf.tables import BorderSpec, RowDef, TableBuilder
from redocx.rtf.tokens import (
    GroupEnd,
    GroupStart,
    Hex,
    Symbol,
    Text,
    Token,
    Word,
    tokenize,
)
from redocx.values import Color, Length, LineSpacing, TabStop

__all__ = ["NotAnRtfError", "is_rtf", "read_rtf"]


class NotAnRtfError(ValueError):
    r"""The data does not start with `{\rtf`."""


def is_rtf(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{\\rtf")


def read_rtf(source: bytes | str | PathLike[str]) -> ImportResult:
    """Read an RTF file into the IR, with a report of what was left out."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if not is_rtf(data):
        raise NotAnRtfError("not an RTF file: it does not start with `{\\rtf`")
    reader = _Reader()
    reader.read(data)
    return ImportResult(reader.document(), reader.report())


# --- encodings ------------------------------------------------------------------------------

_CHARSETS = {
    77: "mac_roman",
    128: "cp932",
    129: "cp949",
    130: "cp1361",
    134: "cp936",
    136: "cp950",
    161: "cp1253",
    162: "cp1254",
    163: "cp1258",
    177: "cp1255",
    178: "cp1256",
    186: "cp1257",
    204: "cp1251",
    222: "cp874",
    238: "cp1250",
    254: "cp437",
    255: "cp850",
}
r"""`\fcharsetN` -> codec; 0 is cp1252, 1 the document's `\ansicpg`, 2 a symbol font."""
_SYMBOL = "symbol"
"""Symbol fonts: byte b is U+F000 + b, the private-use character Word maps the font to."""
_ANSI = {"ansi": "cp1252", "mac": "mac_roman", "pc": "cp437", "pca": "cp850"}


def _codec_of_page(codepage: int) -> str | None:
    name = "mac_roman" if codepage == 10000 else f"cp{codepage}"
    try:
        "".encode(name)
    except LookupError:
        return None
    return name


def _language(lcid: int) -> str | None:
    """A language tag from a Windows language id (`\\lang1033` -> en-US)."""
    name = locale.windows_locale.get(lcid)
    return name.replace("_", "-") if name else None


# --- control words --------------------------------------------------------------------------

_RUN_TOGGLES = {
    "b": "bold",
    "i": "italic",
    "strike": "strike",
    "striked": "double-strike",
    "caps": "caps",
    "scaps": "small-caps",
    "v": "hidden",
    "outl": "outline",
    "shad": "shadow",
    "embo": "emboss",
    "impr": "imprint",
}
_UNDERLINES = {
    "ul": "single",
    "uld": "dotted",
    "uldash": "dash",
    "uldashd": "dot-dash",
    "uldashdd": "dot-dot-dash",
    "uldb": "double",
    "ulth": "thick",
    "ulw": "words",
    "ulwave": "wave",
    "ulhwave": "wavy-heavy",
    "ululdbwave": "wavy-double",
    "ulldash": "dash-long",
    "ulthd": "dotted-heavy",
    "ulthdash": "dashed-heavy",
    "ulthldash": "dash-long-heavy",
    "ulthdashd": "dash-dot-heavy",
    "ulthdashdd": "dash-dot-dot-heavy",
}
_HIGHLIGHTS = {
    "000000": "black",
    "0000FF": "blue",
    "00FFFF": "cyan",
    "00FF00": "green",
    "FF00FF": "magenta",
    "FF0000": "red",
    "FFFF00": "yellow",
    "FFFFFF": "white",
    "000080": "dark-blue",
    "008080": "dark-cyan",
    "008000": "dark-green",
    "800080": "dark-magenta",
    "800000": "dark-red",
    "808000": "dark-yellow",
    "808080": "dark-gray",
    "C0C0C0": "light-gray",
}
"""Word's 16 highlight colours by RGB; other colours become run shading."""
_ALIGNMENTS = {"ql": "left", "qc": "center", "qr": "right", "qj": "justify", "qd": "distribute"}
_PARAGRAPH_TOGGLES = {
    "keep": "keep-lines",
    "keepn": "keep-with-next",
    "pagebb": "page-break-before",
}
_PARAGRAPH_BORDERS = {
    "brdrt": "top",
    "brdrb": "bottom",
    "brdrl": "left",
    "brdrr": "right",
    "brdrbtw": "between",
    "brdrbar": "bar",
}
_TAB_ALIGNMENTS = {"tqr": "right", "tqc": "center", "tqdec": "decimal"}
_TAB_LEADERS = {
    "tldot": "dot",
    "tlhyph": "hyphen",
    "tlul": "underscore",
    "tlth": "heavy",
    "tlmdot": "middle-dot",
    "tleq": "none",
}
_CHARACTERS = {
    "emdash": chr(0x2014),
    "endash": chr(0x2013),
    "emspace": chr(0x2003),
    "enspace": chr(0x2002),
    "qmspace": chr(0x2005),
    "bullet": chr(0x2022),
    "lquote": chr(0x2018),
    "rquote": chr(0x2019),
    "ldblquote": chr(0x201C),
    "rdblquote": chr(0x201D),
    "zwj": chr(0x200D),
    "zwnj": chr(0x200C),
    "ltrmark": chr(0x200E),
    "rtlmark": chr(0x200F),
    "tab": "\t",
    "line": "\n",
}
_SYMBOLS = {"~": chr(0x00A0), "-": chr(0x00AD), "_": chr(0x2011)}
_PAGE = {
    "paperw": "width",
    "paperh": "height",
    "margl": "margin_left",
    "margr": "margin_right",
    "margt": "margin_top",
    "margb": "margin_bottom",
    "gutter": "gutter",
    "headery": "header_distance",
    "footery": "footer_distance",
}
_META = {
    "title": "title",
    "subject": "subject",
    "author": "author",
    "keywords": "keywords",
    "doccomm": "description",
    "operator": "last_modified_by",
}
_SKIPPED = frozenset(
    "stylesheet listtable listoverridetable revtbl rsidtbl filetbl pn pnseclvl nonshppict "
    "nonesttables objdata objclass objname xe tc tcn txe rxe bxe template do fchars lchars "
    "pgdsctbl latentstyles themedata colorschememapping datastore xmlnstbl mmathPr "
    "generator userprops shprslt".split()
)
"""Destinations without text to read: tables of definitions, fallbacks for readers that do
not know the newer form, index entries."""
_LEFT_OUT = {
    "footnote": "footnote",
    "annotation": "comment",
    **{
        f"{part}{kind}": "header or footer"
        for part in ("header", "footer")
        for kind in ("", "l", "r", "f")
    },
}
"""Destinations with text that the reader leaves out, by their name in the report."""
_READ = {
    "fldrslt": None,
    "shppict": None,
    "result": None,
    "listtext": "list (its numbers kept as text)",
    "pntext": "list (its numbers kept as text)",
    "shp": "shape (a picture in it is kept, in the text)",
    "object": "embedded object (its picture kept)",
}
"""Destinations whose content is read as body text, and what the report says of them."""
_KNOWN = frozenset(
    {*_READ, *_META, "fonttbl", "colortbl", "info", "upr", "ud", "pict", "field", "fldinst"}
    | {"bkmkstart", "bkmkend", "shpinst"}
)
r"""Destinations the reader reads; any other after `\*` is skipped."""
_HYPERLINK = re.compile(r'"([^"]*)"|(\S+)')
_SHAPE_BOX = {"shpleft": "left", "shpright": "right", "shptop": "top", "shpbottom": "bottom"}


# --- state ----------------------------------------------------------------------------------


@dataclass
class _State:
    """What a group sets: the destination, and character and paragraph formatting."""

    destination: str = "body"
    """body, skip, fonttbl, colortbl, info, upr, pict, fldinst or collect."""
    owns: str | None = None
    """The destination this group started, finished when the group ends (collect ones by
    what they collect: bkmkstart, bkmkend, meta:title, ...)."""
    starred: bool = False
    r"""The last token was `\*`: an unknown destination word after it is skipped."""
    run: dict[str, object] = field(default_factory=dict[str, object])
    font: int | None = None
    paragraph: dict[str, object] = field(default_factory=dict[str, object])
    borders: dict[str, BorderSpec] = field(default_factory=dict[str, BorderSpec])
    tabs: list[TabStop] = field(default_factory=list[TabStop])
    tab_align: str = "left"
    tab_leader: str = "none"
    line: int = 0
    r"""`\slN`: 0 is single spacing."""
    line_multiple: bool = False
    in_table: bool = False
    depth: int = 1
    r"""`\itapN`: 2 or more in a nested table."""
    skip_count: int = 1
    r"""`\ucN`: how many characters after a `\u` stand in for it in older readers."""

    def child(self) -> _State:
        return replace(
            self,
            owns=None,
            starred=False,
            run=dict(self.run),
            paragraph=dict(self.paragraph),
            borders=dict(self.borders),
            tabs=list(self.tabs),
        )

    def reset_paragraph(self) -> None:
        self.paragraph, self.borders, self.tabs = {}, {}, []
        self.tab_align, self.tab_leader = "left", "none"
        self.line, self.line_multiple = 0, False
        self.in_table, self.depth = False, 1


@dataclass
class _Field:
    depth: int
    """The stack depth inside the field's group."""
    instruction: list[str] = field(default_factory=list[str])
    paragraph: int = -1
    """The paragraph its result starts in, by number."""
    start: int = 0
    """Where its result starts in that paragraph's content."""


@dataclass
class _FontEntry:
    index: int | None = None
    charset: int | None = None
    codepage: int | None = None
    name: list[str] = field(default_factory=list[str])
    real_name: list[str] = field(default_factory=list[str])
    r"""`{\*\fname Arial;}`: the font's name when `name` is one for a charset (Arial Greek)."""


@dataclass(frozen=True)
class _Font:
    name: str
    codec: str | None


# --- the reader -----------------------------------------------------------------------------


class _Reader:
    def __init__(self) -> None:
        self.state = _State()
        self.stack: list[_State] = []
        self.ansi = "cp1252"
        self.fonts: dict[int, _Font] = {}
        self.default_font: int | None = None
        self.default_lang: str | None = None
        self.colors: list[Color] = []
        self.color: list[int | None] = [None, None, None]
        self.font_entry = _FontEntry()
        self.widow_control = False
        self.page: dict[str, Length] = {}
        self.tab_stop: Length | None = None
        self.meta: dict[str, str] = {}
        self.collected: list[str] = []
        """Text of the destination being collected (a bookmark name, a document property)."""
        self.blocks: list[ir.Block] = []
        self.content: list[ir.Inline] = []
        self.paragraph_number = 0
        self.table: TableBuilder | None = None
        self.row = RowDef()
        self.border: BorderSpec | None = None
        """The border the `\\brdr*` words describe: the last one named."""
        self.fields: list[_Field] = []
        self.picture: Picture | None = None
        self.boundary = -1
        """Where a field's result starts in the paragraph: text is not merged across it."""
        self.shape_box: dict[str, int] = {}
        r"""The shape being read: `\shpleft`, ... in twips."""
        self.pending = bytearray()
        """Text bytes not decoded yet: a double-byte character can span two `\\'hh`."""
        self.skip = 0
        self.high_surrogate: int | None = None
        self.counts: Counter[str] = Counter()
        self.left_out: Counter[str] = Counter()
        self._run_sets: dict[tuple[tuple[str, object], ...], PropertySet] = {}

    # --- the token loop

    def read(self, data: bytes) -> None:
        tokens = tokenize(data)
        for token in tokens:
            if self.state.destination == "skip":
                self._skip_group(token, tokens)
            elif isinstance(token, Text | Hex):
                self._bytes(token)
            else:
                self._flush()
                if isinstance(token, GroupStart):
                    self.stack.append(self.state)
                    self.state = self.state.child()
                    self.skip = 0
                elif isinstance(token, GroupEnd):
                    self._end_group()
                elif isinstance(token, Word):
                    self._word(token.name, token.param)
                elif isinstance(token, Symbol):
                    self._symbol(token.char)
                elif self.picture is not None and self.state.destination == "pict":
                    self.picture.binary.extend(token.data)
        self._flush()
        while self.stack:
            self._end_group()
        self._end_paragraph(final=True)
        self._close_table()

    def _skip_group(self, token: Token, tokens: Iterator[Token]) -> None:
        """Skip to the end of the group being skipped (`token` is the first one in it)."""
        if isinstance(token, GroupEnd):
            self._end_group()
            return
        depth = 1 if isinstance(token, GroupStart) else 0
        for token in tokens:
            if isinstance(token, GroupStart):
                depth += 1
            elif isinstance(token, GroupEnd):
                if depth == 0:
                    self._end_group()
                    return
                depth -= 1

    def _end_group(self) -> None:
        if not self.stack:
            return  # a closing brace too many
        ended = self.state
        self.state = self.stack.pop()
        self.skip = 0
        if ended.owns is not None:
            self._end_destination(ended.owns)
        while self.fields and self.fields[-1].depth > len(self.stack):
            self._end_field(self.fields.pop())

    # --- text

    def _bytes(self, token: Text | Hex) -> None:
        if self.state.destination == "pict":
            if self.picture is not None and isinstance(token, Text):
                self.picture.text(token.data)
            return
        data = token.data if isinstance(token, Text) else bytes((token.value,))
        if self.skip:
            dropped = min(self.skip, len(data))
            self.skip -= dropped
            data = data[dropped:]
        self.pending.extend(data)

    def _flush(self) -> None:
        if not self.pending:
            return
        data, self.pending = bytes(self.pending), bytearray()
        codec = self._codec()
        if codec == _SYMBOL:
            text = "".join(chr(0xF000 + b) if b >= 0x20 else chr(b) for b in data)
        else:
            text = data.decode(codec or self.ansi, errors="replace")
        self._text(text)

    def _codec(self) -> str | None:
        if self.state.destination in ("fonttbl", "fname"):
            codec = self._font_codec(self.font_entry)
            return None if codec == _SYMBOL else codec  # the name of a symbol font is text
        font = self.fonts.get(self.state.font) if self.state.font is not None else None
        return font.codec if font is not None else None

    @staticmethod
    def _font_codec(entry: _FontEntry) -> str | None:
        if entry.codepage is not None:
            return _codec_of_page(entry.codepage)
        if entry.charset is None or entry.charset == 1:
            return None
        if entry.charset == 0:
            return "cp1252"
        if entry.charset == 2:
            return _SYMBOL
        return _CHARSETS.get(entry.charset)

    def _unicode(self, code: int) -> None:
        if code < 0:
            code += 65536
        self.skip = self.state.skip_count
        if 0xD800 <= code < 0xDC00:
            self.high_surrogate = code
            return
        if 0xDC00 <= code < 0xE000:
            high, self.high_surrogate = self.high_surrogate, None
            if high is None:
                return
            code = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)
        self.high_surrogate = None
        self._text(chr(code))

    def _text(self, text: str) -> None:
        destination = self.state.destination
        if destination == "body":
            self._add_text(text)
        elif destination == "fonttbl":
            self._font_text(text)
        elif destination == "fname":
            self.font_entry.real_name.append(text.replace(";", ""))
        elif destination == "colortbl":
            for _ in range(text.count(";")):
                self.colors.append(_color(self.color))
                self.color = [None, None, None]
        elif destination == "fldinst":
            if self.fields:
                self.fields[-1].instruction.append(text)
        elif destination == "collect":
            self.collected.append(text)

    def _add_text(self, text: str) -> None:
        if not text:
            return
        props = self._run_props()
        last = self.content[-1] if self.content else None
        if isinstance(last, ir.Run) and last.props == props and len(self.content) != self.boundary:
            self.content[-1] = ir.Run(last.text + text, props)
        else:
            self.content.append(ir.Run(text, props))
            self.counts["run"] += 1

    def _run_props(self) -> PropertySet:
        defaults = self._run_defaults()
        values = {k: v for k, v in self.state.run.items() if defaults.get(k) != v}
        key = tuple(sorted(values.items(), key=lambda item: item[0]))
        found = self._run_sets.get(key)
        if found is None:
            found = self._run_sets[key] = self._property_set(Target.RUN, values, "run")
        return found

    def _property_set(self, target: Target, values: dict[str, object], where: str) -> PropertySet:
        try:
            return PropertySet(target, values)
        except ValueError:
            pass
        valid: dict[str, object] = {}
        for key, value in values.items():
            try:
                PropertySet(target, {key: value})
            except ValueError:
                self.left_out[f"{where} property `{key}`"] += 1
            else:
                valid[key] = value
        return PropertySet(target, valid)

    # --- control words and destinations

    def _symbol(self, char: str) -> None:
        if char == "*":
            self.state.starred = True
        elif char in _SYMBOLS and self.state.destination == "body":
            self._add_text(_SYMBOLS[char])

    def _word(self, name: str, param: int | None) -> None:
        state = self.state
        starred, state.starred = state.starred, False
        if name == "u" and param is not None:
            self._unicode(param)
            return
        if name == "uc" and param is not None:
            state.skip_count = max(0, min(param, 16))
            return
        if self._destination(name, starred):
            return
        destination = state.destination
        if destination == "body":
            self._body_word(name, param)
        elif destination == "fonttbl":
            self._font_word(name, param)
        elif destination == "colortbl":
            if name in ("red", "green", "blue") and param is not None:
                self.color[("red", "green", "blue").index(name)] = max(0, min(255, param))
        elif destination == "pict" and self.picture is not None:
            self.picture.word(name, param)
        elif destination == "shpinst" and name in _SHAPE_BOX and param is not None:
            self.shape_box[_SHAPE_BOX[name]] = param

    def _destination(self, name: str, starred: bool) -> bool:
        """Start the destination the word names; False when it is not one."""
        state = self.state
        destination = state.destination
        if name == "fname" and destination == "fonttbl":
            state.destination = "fname"
            return True
        if starred and name not in _KNOWN:  # {\*\panose ...}, {\*\blipuid ...}, ...
            state.destination = "skip"
            return True
        if destination == "info":
            if name in _META:
                self._own(f"meta:{_META[name]}", "collect")
            elif starred or name not in ("yr", "mo", "dy", "hr", "min", "sec"):
                state.destination = "skip"
            return True
        if name == "ud" and destination == "upr":
            state.destination = "body"
            return True
        if destination == "shpinst":  # a shape's properties: its picture is the `pib` one
            if name == "pict":
                self.picture = Picture()
                self._own("pict")
            elif name == "shptxt":
                self.left_out["text box"] += 1
                state.destination = "skip"
            return name in ("pict", "shptxt")
        if name == "shpinst" and destination == "body":
            state.destination = "shpinst"
            self.shape_box = {}
            return True
        if destination not in ("body", "upr"):
            return False
        if name in _SKIPPED:
            state.destination = "skip"
        elif name in _LEFT_OUT:
            self.left_out[_LEFT_OUT[name]] += 1
            state.destination = "skip"
        elif name in _READ:
            if note := _READ[name]:
                self.left_out[note] += 1
            state.destination = "body"
            if name == "fldrslt" and self.fields:
                self.fields[-1].paragraph = self.paragraph_number
                self.fields[-1].start = self.boundary = len(self.content)
        elif name in ("fonttbl", "colortbl", "info"):
            self._own(name)
        elif name == "upr":
            state.destination = "upr"
        elif name == "pict":
            self.picture = Picture()
            self._own("pict")
        elif name == "field":
            self.fields.append(_Field(len(self.stack)))
        elif name == "fldinst":
            state.destination = "fldinst"
        elif name in ("bkmkstart", "bkmkend"):
            self._own(name, "collect")
        elif starred or destination == "upr":
            state.destination = "skip"
        else:
            return False
        return True

    def _own(self, name: str, destination: str | None = None) -> None:
        self.state.destination = destination or name
        self.state.owns = name
        self.collected = []

    def _end_destination(self, name: str) -> None:
        if name == "fonttbl":
            self._end_font()
        elif name == "pict":
            picture, self.picture = self.picture, None
            image = picture.image() if picture is not None else None
            box = self.shape_box
            if image is not None and self.state.destination == "shpinst" and len(box) == 4:
                # A shape's picture is shown at the size of the shape.
                width, height = box["right"] - box["left"], box["bottom"] - box["top"]
                if width > 0 and height > 0:
                    image = replace(image, width=Length.twips(width), height=Length.twips(height))
            if image is not None:
                self.content.append(image)
                self.counts["image"] += 1
            elif picture is not None:
                self.left_out[f"picture ({picture.kind or 'a format not read'})"] += 1
        elif name in ("bkmkstart", "bkmkend") or name.startswith("meta:"):
            text = "".join(self.collected).strip()
            self.collected = []
            if not text:
                return
            if name == "bkmkstart":
                self.content.append(ir.BookmarkStart(text))
                self.counts["bookmark"] += 1
            elif name == "bkmkend":
                self.content.append(ir.BookmarkEnd(text))
            else:
                self.meta[name.removeprefix("meta:")] = text

    # --- fonts

    def _font_word(self, name: str, param: int | None) -> None:
        entry = self.font_entry
        if name == "f" and param is not None:
            if entry.index is not None and entry.name:
                self._end_font()
            self.font_entry = _FontEntry(param)
        elif name == "fcharset" and param is not None:
            entry.charset = param
        elif name == "cpg" and param is not None:
            entry.codepage = param

    def _font_text(self, text: str) -> None:
        entry = self.font_entry
        if ";" not in text:
            entry.name.append(text)
            return
        before, _, after = text.partition(";")
        entry.name.append(before)
        self._end_font()
        if after.strip():
            self.font_entry.name.append(after)

    def _end_font(self) -> None:
        entry = self.font_entry
        name = "".join(entry.real_name).strip() or "".join(entry.name).strip()
        if entry.index is not None and name:
            self.fonts[entry.index] = _Font(name, self._font_codec(entry))
        self.font_entry = _FontEntry()

    # --- the body

    def _body_word(self, name: str, param: int | None) -> None:
        state, run, value = self.state, self.state.run, param if param is not None else 1
        if name in _RUN_TOGGLES:
            _toggle(run, _RUN_TOGGLES[name], value != 0)
        elif name in _CHARACTERS:
            self._add_text(_CHARACTERS[name])
        elif name == "par":
            self._end_paragraph()
        elif name == "plain":
            run.clear()
            state.font = self.default_font
            self._set_font(self.default_font)
        elif name == "f" and param is not None:
            state.font = param
            self._set_font(param)
        elif name == "fs" and param is not None and param > 0:
            run["size"] = Length(param * 6350)
        elif name == "cf" and param is not None:
            _set(run, "color", self._color(param))
        elif name == "highlight" and param is not None:
            color = self._color(param)
            run.pop("shading", None)
            run.pop("highlight", None)
            if color is not None and color.rgb in _HIGHLIGHTS:
                run["highlight"] = _HIGHLIGHTS[color.rgb]
            elif color is not None:
                run["shading"] = color
        elif name in ("cb", "chcbpat") and param is not None:
            _set(run, "shading", self._color(param))
        elif name in _UNDERLINES:
            _set(run, "underline", _UNDERLINES[name] if value != 0 else None)
        elif name in ("ulnone", "ul0"):
            run.pop("underline", None)
        elif name in ("super", "sub"):
            run["vertical-align"] = "superscript" if name == "super" else "subscript"
        elif name == "nosupersub":
            run.pop("vertical-align", None)
        elif name in ("up", "dn"):
            raise_by = value if name == "up" else -value
            _set(run, "position", Length(raise_by * 6350) if raise_by else None)
        elif name == "expnd" and param is not None:
            _set(run, "character-spacing", Length.twips(param * 5) if param else None)
        elif name == "expndtw" and param is not None:
            _set(run, "character-spacing", Length.twips(param) if param else None)
        elif name == "charscalex" and param is not None:
            _set(run, "scale", f"{param}%" if param not in (0, 100) else None)
        elif name == "kerning" and param is not None:
            _set(run, "kerning", Length(param * 6350) if param > 0 else None)
        elif name == "lang" and param is not None:
            _set(run, "lang", _language(param))
        elif name in ("rtlch", "ltrch"):
            _set(run, "rtl", True if name == "rtlch" else None)
        elif name == "pard":
            state.reset_paragraph()
            self.border = None
        elif name in _ALIGNMENTS:
            state.paragraph["align"] = _ALIGNMENTS[name]
        elif name in ("li", "ri", "sb", "sa") and param is not None:
            key = {"li": "indent-left", "ri": "indent-right", "sb": "space-before"}.get(
                name, "space-after"
            )
            state.paragraph[key] = Length.twips(param)
        elif name == "fi" and param is not None:
            state.paragraph.pop("first-line-indent", None)
            state.paragraph.pop("hanging-indent", None)
            if param > 0:
                state.paragraph["first-line-indent"] = Length.twips(param)
            elif param < 0:
                state.paragraph["hanging-indent"] = Length.twips(-param)
        elif name == "sl" and param is not None:
            state.line = param
        elif name == "slmult" and param is not None:
            state.line_multiple = param == 1
        elif name in _PARAGRAPH_TOGGLES:
            _toggle(state.paragraph, _PARAGRAPH_TOGGLES[name], value != 0)
        elif name in ("widctlpar", "nowidctlpar"):
            state.paragraph["widow-control"] = name == "widctlpar"
        elif name == "outlinelevel" and param is not None:
            _set(state.paragraph, "outline-level", param + 1 if 0 <= param < 9 else None)
        elif name == "cbpat" and param is not None:
            _set(state.paragraph, "shading", self._color(param))
        elif name in _PARAGRAPH_BORDERS:
            self.border = state.borders[_PARAGRAPH_BORDERS[name]] = BorderSpec()
        elif name == "box":
            self.border = BorderSpec()
            for side in ("top", "left", "bottom", "right"):
                state.borders[side] = self.border
        elif name in _TAB_ALIGNMENTS:
            state.tab_align = _TAB_ALIGNMENTS[name]
        elif name in _TAB_LEADERS:
            state.tab_leader = _TAB_LEADERS[name]
        elif name in ("tx", "tb") and param is not None:
            align = "bar" if name == "tb" else state.tab_align
            state.tabs.append(TabStop(Length.twips(max(0, param)), align, state.tab_leader))
            state.tab_align, state.tab_leader = "left", "none"
        elif name == "intbl":
            state.in_table = True
        elif name == "itap" and param is not None:
            state.depth = param
            state.in_table = param > 0
        elif name in ("cell", "nestcell"):
            self._end_cell(nested=name == "nestcell" or state.depth > 1)
        elif name == "row":
            self._end_row()
        elif name == "nestrow":
            pass
        elif name == "trowd":
            self.row = RowDef()
            self.border = None
        elif name == "page":
            self.content.append(ir.Break("page"))
        elif name == "column":
            self.content.append(ir.Break("column"))
        elif name == "sect":
            self._end_paragraph()
            self.left_out["section break"] += 1
        elif name == "dropcapli":
            self.left_out["drop cap (kept as a paragraph)"] += 1
        elif self._document_word(name, param) or (
            self.border is not None and self.border.word(name, param)
        ):
            pass
        else:
            border = self.row.word(name, param)
            if isinstance(border, BorderSpec):
                self.border = border

    def _document_word(self, name: str, param: int | None) -> bool:
        if name in _ANSI:
            self.ansi = _ANSI[name]
        elif name == "ansicpg" and param is not None:
            self.ansi = _codec_of_page(param) or self.ansi
        elif name == "deff" and param is not None:
            self.default_font = param
            if self.state.font is None:
                self.state.font = param
        elif name == "deflang" and param is not None:
            self.default_lang = _language(param)
        elif name == "widowctrl":
            self.widow_control = True
        elif name == "deftab" and param is not None and param > 0:
            self.tab_stop = Length.twips(param)
        elif name in _PAGE and param is not None and param > 0:
            self.page[_PAGE[name]] = Length.twips(param)
        else:
            return False
        return True

    def _set_font(self, index: int | None) -> None:
        font = self.fonts.get(index) if index is not None else None
        _set(self.state.run, "font", font.name if font is not None else None)

    def _color(self, index: int) -> Color | None:
        if 0 < index < len(self.colors) and self.colors[index].rgb:
            return self.colors[index]
        return None

    # --- paragraphs and tables

    def _paragraph(self) -> ir.Paragraph:
        state = self.state
        values = dict(state.paragraph)
        if state.line:
            if state.line_multiple and state.line > 0:
                values["line-spacing"] = LineSpacing.lines(Decimal(state.line) / 240)
            elif state.line > 0:
                values["line-spacing"] = LineSpacing.at_least(Length.twips(state.line))
            elif not state.line_multiple:
                values["line-spacing"] = LineSpacing.exact(Length.twips(-state.line))
        if state.tabs:
            values["tabs"] = tuple(state.tabs)
        for side, spec in state.borders.items():
            values[f"border-{side}"] = spec.border(self.colors)
        defaults = self._paragraph_defaults()
        values = {k: v for k, v in values.items() if defaults.get(k) != v}
        props = self._property_set(Target.PARAGRAPH, values, "paragraph")
        content, self.content, self.boundary = tuple(self.content), [], -1
        self.paragraph_number += 1
        self.counts["paragraph"] += 1
        return ir.Paragraph(content, props=props)

    def _end_paragraph(self, *, final: bool = False) -> None:
        if final and not self.content:
            return
        paragraph = self._paragraph()
        if self.state.in_table and not final:
            if self.state.depth > 1:
                self.left_out["nested table (its text kept)"] += 1
            self._table().content.append(paragraph)
            return
        self._close_table()
        self.blocks.append(paragraph)

    def _table(self) -> TableBuilder:
        if self.table is None:
            self.table = TableBuilder()
        return self.table

    def _end_cell(self, *, nested: bool) -> None:
        if nested:  # a nested table's cell ends a paragraph of the outer cell
            self.left_out["nested table (its text kept)"] += 1
            self._table().content.append(self._paragraph())
            return
        self._table().content.append(self._paragraph())
        self._table().end_cell()

    def _end_row(self) -> None:
        if self.content:  # text after the last \cell
            self._table().content.append(self._paragraph())
        self._table().end_row(self.row)

    def _close_table(self) -> None:
        table, self.table = self.table, None
        if table is None or table.empty:
            return
        built = table.build(self.colors, self.row)
        if built is None:
            return
        self.blocks.append(built)
        self.counts["table"] += 1
        self.counts["cell"] += sum(len(row.cells) for row in built.rows)

    # --- fields

    def _end_field(self, current: _Field) -> None:
        instruction = "".join(current.instruction).strip()
        words = [quoted or bare for quoted, bare in _HYPERLINK.findall(instruction)]
        if not words or words[0].upper() != "HYPERLINK" or current.paragraph < 0:
            if words:
                self.counts["field"] += 1
            return
        if current.paragraph != self.paragraph_number:
            self.left_out["hyperlink across paragraphs (its text kept)"] += 1
            return
        url, anchor = "", None
        rest = iter(words[1:])
        for word in rest:
            if word == "\\l":
                anchor = next(rest, None)
            elif not word.startswith("\\") and not url:
                url = word
        result = self.content[current.start :]
        runs = [item for item in result if isinstance(item, ir.Run)]
        if not runs or len(runs) != len(result):
            return
        del self.content[current.start :]
        self.content.append(ir.Hyperlink(url, tuple(runs), anchor))
        self.counts["hyperlink"] += 1

    # --- the result

    def _paragraph_defaults(self) -> dict[str, object]:
        return {
            "space-before": Length(0),
            "space-after": Length(0),
            "widow-control": self.widow_control,
        }

    def _run_defaults(self) -> dict[str, object]:
        """RTF's defaults: 12 pt in the `\\deff` font and the `\\deflang` language."""
        defaults: dict[str, object] = {"size": Length(24 * 6350)}
        if self.default_font is not None and self.default_font in self.fonts:
            defaults["font"] = self.fonts[self.default_font].name
        if self.default_lang:
            defaults["lang"] = self.default_lang
        return defaults

    def document(self) -> ir.Document:
        default_run = self._run_defaults()
        page = ir.PageSetup()
        for attribute, length in self.page.items():
            page = replace(page, **{attribute: length})
        meta = ir.Meta(
            title=self.meta.get("title"),
            subject=self.meta.get("subject"),
            author=self.meta.get("author"),
            keywords=tuple(self.meta["keywords"].split()) if "keywords" in self.meta else (),
            description=self.meta.get("description"),
            last_modified_by=self.meta.get("last_modified_by"),
        )
        settings = ir.Settings(default_tab_stop=self.tab_stop) if self.tab_stop else ir.Settings()
        return ir.Document(
            body=tuple(self.blocks),
            meta=meta,
            settings=settings,
            default_run=self._property_set(Target.RUN, default_run, "default run"),
            default_paragraph=self._property_set(
                Target.PARAGRAPH, self._paragraph_defaults(), "default paragraph"
            ),
            section=ir.Section(page=page),
        )

    def report(self) -> ImportReport:
        ranked = sorted(self.left_out.items(), key=lambda item: (-item[1], item[0]))
        return ImportReport(
            paragraphs=self.counts["paragraph"],
            runs=self.counts["run"],
            tables=self.counts["table"],
            cells=self.counts["cell"],
            images=self.counts["image"],
            hyperlinks=self.counts["hyperlink"],
            fields=self.counts["field"],
            bookmarks=self.counts["bookmark"],
            unsupported=tuple(ranked),
        )


def _toggle(values: dict[str, object], key: str, on: bool) -> None:
    """Set a toggle; off is the default, so it is dropped rather than kept as False."""
    _set(values, key, True if on else None)


def _set(values: dict[str, object], key: str, value: object) -> None:
    if value is None:
        values.pop(key, None)
    else:
        values[key] = value


def _color(parts: list[int | None]) -> Color:
    if all(part is None for part in parts):
        return Color()
    red, green, blue = (part or 0 for part in parts)
    return Color(rgb=f"{red:02X}{green:02X}{blue:02X}")
