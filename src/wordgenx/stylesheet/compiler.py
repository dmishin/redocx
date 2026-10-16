"""Compile parsed style sheets into IR styles, numbering definitions and document defaults.

Every property is validated through the property registry; problems become
diagnostics pointing at the exact key or value, and compilation continues.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from wordgenx.ir import (
    NUMBER_FORMATS,
    Document,
    Numbering,
    NumberingLevel,
    NumberingRef,
    Style,
    StyleKind,
    TableCondition,
)
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import (
    EMPTY_CELL,
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    EMPTY_TABLE,
    REGISTRY,
    SHORTHANDS,
    PropertySet,
    Target,
    expand_shorthand,
)
from wordgenx.stylesheet.syntax import (
    Block,
    Declaration,
    DefaultsRule,
    NumberingRule,
    StyleRule,
    StyleSheet,
)
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity, render
from wordgenx.syntax.source import Source, Span
from wordgenx.values import InvalidValueError, Length, LineSpacing

# Bare words that stand for a value of another property: `justify;` means `align: justify;`.
FLAG_ALIASES: dict[str, tuple[str, str]] = {
    value: ("align", value) for value in ("left", "center", "right", "justify", "distribute")
}
STYLE_META = ("next", "numbering", "ui-priority", "quick-format")
CHARACTER_META = ("ui-priority", "quick-format")
LEVEL_META = tuple("format text start style legal bullet suffix marker-align restart-after".split())
INDENT_KEYS = {
    "left": "indent-left",
    "right": "indent-right",
    "first-line": "first-line-indent",
    "hanging": "hanging-indent",
}
TABLE_META = ("ui-priority", "quick-format")
TABLE_PARTS: dict[str, str] = {
    "header-row": "firstRow",
    "first-row": "firstRow",
    "last-row": "lastRow",
    "first-column": "firstCol",
    "last-column": "lastCol",
    "top-left-cell": "nwCell",
    "top-right-cell": "neCell",
    "bottom-left-cell": "swCell",
    "bottom-right-cell": "seCell",
}
"""Table-style blocks -> w:tblStylePr types."""
BANDS: dict[str, tuple[str, str]] = {
    "banded-rows": ("band1Horz", "band2Horz"),
    "banded-columns": ("band1Vert", "band2Vert"),
}
"""Banding blocks -> (odd, even) w:tblStylePr types."""
CONDITION_TARGETS = (Target.RUN, Target.CELL, Target.PARAGRAPH)
"""In table styles, borders and shading are the table's and the cells', not paragraphs'."""
NUMBERING_REF_RE = re.compile(r'\s*(?:"([^"]*)"|(\S+))(?:\s+level\s+(\d))?\s*')


def _kebab_to_camel(text: str) -> str:
    head, *rest = text.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _camel_to_kebab(text: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", text).lower()


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text


def _split_tuple(text: str) -> list[str] | None:
    """``(a, b=c)`` -> ["a", "b=c"]; None if `text` is not a tuple."""
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    parts: list[str] = []
    depth, current = 0, ""
    for ch in text[1:-1]:
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


@dataclass(frozen=True)
class CompiledStyles:
    styles: tuple[Style, ...] = ()
    numbering: tuple[Numbering, ...] = ()
    default_run: PropertySet = EMPTY_RUN
    default_paragraph: PropertySet = EMPTY_PARAGRAPH
    diagnostics: tuple[tuple[Source, Diagnostic], ...] = ()

    @property
    def ok(self) -> bool:
        return not any(d.severity is Severity.ERROR for _, d in self.diagnostics)

    def render_diagnostics(self) -> str:
        return "\n".join(render(d, source) for source, d in self.diagnostics)

    def apply(self, document: Document) -> Document:
        """`document` with these styles, numbering and defaults."""
        return replace(
            document,
            styles=self.styles,
            numbering=self.numbering,
            default_run=self.default_run.merged(document.default_run),
            default_paragraph=self.default_paragraph.merged(document.default_paragraph),
        )


@dataclass
class _Reference:
    """A name that must resolve once all sheets are compiled."""

    kind: str  # "style", "paragraph-style", "table-style" or "numbering"
    name: str
    what: str
    span: Span
    source: Source


@dataclass
class StyleSheetCompiler:
    """Accumulates style sheets in order: later sheets modify or replace earlier rules."""

    _styles: dict[str, Style] = field(default_factory=lambda: {})
    _numbering: dict[str, Numbering] = field(default_factory=lambda: {})
    _run: PropertySet = EMPTY_RUN
    _paragraph: PropertySet = EMPTY_PARAGRAPH
    _diagnostics: list[tuple[Source, Diagnostic]] = field(default_factory=lambda: [])
    _references: list[_Reference] = field(default_factory=lambda: [])
    _level_links: list[tuple[str, str, int, Span, Source]] = field(default_factory=lambda: [])
    """(style, numbering, level, span, source) for each `style:` of a list level."""
    _source: Source | None = None
    builtins: Mapping[str, Callable[[], Style]] = field(default_factory=lambda: {})
    """Word built-in styles by name: `modify` starts from them, and references to them are
    valid (the document gets them when it uses them)."""

    # --- reporting ----------------------------------------------------------------

    def _report(
        self,
        code: Code,
        message: str,
        span: Span,
        label: str = "",
        help: str | None = None,
        severity: Severity = Severity.ERROR,
    ) -> None:
        assert self._source is not None
        self._diagnostics.append(
            (self._source, Diagnostic(severity, code, message, span, label, help))
        )

    # --- entry points ---------------------------------------------------------------

    def add(self, sheet: StyleSheet) -> None:
        self._source = sheet.source
        self._diagnostics.extend((sheet.source, d) for d in sheet.diagnostics)
        for rule in sheet.rules:
            if isinstance(rule, DefaultsRule):
                self._defaults(rule)
            elif isinstance(rule, NumberingRule):
                self._numbering_rule(rule)
            else:
                self._style_rule(rule)

    def result(self) -> CompiledStyles:
        self._check_references()
        self._check_level_links()
        return CompiledStyles(
            (*self._styles.values(), *self._referenced_builtins()),
            tuple(self._numbering.values()),
            self._run,
            self._paragraph,
            tuple(self._diagnostics),
        )

    def _referenced_builtins(self) -> list[Style]:
        """Built-in styles that defined styles are based on (or link to, or follow with),
        so the result is complete on its own: `table X : "Table Grid"` brings Table Grid."""
        known = set(self._styles)
        added: list[Style] = []
        pending = list(self._styles.values())
        while pending:
            style = pending.pop()
            for name in (style.based_on, style.next, style.link):
                if name is not None and style_id(name) not in known and name in self.builtins:
                    builtin = self.builtins[name]()
                    known.add(style_id(name))
                    added.append(builtin)
                    pending.append(builtin)
        return added

    # --- declarations -----------------------------------------------------------------

    def _properties(
        self,
        items: Iterable[Declaration | Block],
        targets: Sequence[Target],
        meta: Sequence[str] = (),
        context: str = "",
    ) -> tuple[dict[Target, dict[str, object]], dict[str, Declaration]]:
        """Split declarations into values per target and meta declarations."""
        values: dict[Target, dict[str, object]] = {target: {} for target in Target}
        meta_found: dict[str, Declaration] = {}
        for item in items:
            if isinstance(item, Block):
                self._report(
                    Code.UNSUPPORTED,
                    f"unexpected block `{item.name}`" + (f" in {context}" if context else ""),
                    item.span,
                )
                continue
            if item.key in meta:
                meta_found[item.key] = item
                continue
            for key, value, span in self._expand(item, targets):
                self._assign(key, value, span, item, targets, meta, values)
        return values, meta_found

    def _expand(
        self, decl: Declaration, targets: Sequence[Target] = ()
    ) -> list[tuple[str, object, Span]]:
        """Composite and shorthand declarations -> (registry key, value, span) triples."""
        value_span = decl.value_span or decl.key_span
        if decl.value is None:
            if decl.key in FLAG_ALIASES:
                key, value = FLAG_ALIASES[decl.key]
                return [(key, value, decl.key_span)]
            return [(decl.key, True, decl.key_span)]
        raw = decl.value
        if decl.key == "indent":
            return self._indent(raw, value_span)
        if decl.key in SHORTHANDS:
            try:
                boxed = (Target.PARAGRAPH, Target.TABLE, Target.CELL)
                target = next((t for t in targets if t in boxed), Target.TABLE)
                expanded = expand_shorthand(decl.key, _unquote(raw), target) or {}
            except InvalidValueError as e:
                self._report(Code.INVALID_VALUE, f"{decl.key}: {e}", value_span, "invalid value")
                return []
            return [(key, value, value_span) for key, value in expanded.items()]
        if decl.key == "line-spacing" and (parts := _split_tuple(raw)) is not None:
            spacing = self._line_spacing(parts, value_span)
            return [("line-spacing", spacing, value_span)] if spacing else []
        return [(decl.key, _unquote(raw), value_span)]

    def _indent(self, raw: str, span: Span) -> list[tuple[str, object, Span]]:
        parts = _split_tuple(raw)
        if parts is not None:
            out: list[tuple[str, object, Span]] = []
            for part in parts:
                key, _, value = part.partition("=")
                key = key.strip()
                if key not in INDENT_KEYS or not value:
                    self._report(
                        Code.INVALID_VALUE,
                        f"invalid indent part `{part}`",
                        span,
                        help="use (left=..., right=..., first-line=..., hanging=...)",
                    )
                    continue
                out.append((INDENT_KEYS[key], value.strip(), span))
            return out
        words = raw.split()
        if len(words) == 2 and words[1] in ("hanging", "first-line"):
            other = "hanging-indent" if words[1] == "hanging" else "first-line-indent"
            return [("indent-left", words[0], span), (other, words[0], span)]
        if len(words) == 1:
            return [("indent-left", words[0], span)]
        self._report(
            Code.INVALID_VALUE,
            f"invalid indent `{raw}`",
            span,
            help="use a length (1cm), `1cm hanging`, or (left=..., hanging=...)",
        )
        return []

    def _line_spacing(self, parts: list[str], span: Span) -> LineSpacing | None:
        try:
            if len(parts) == 2 and parts[1] in ("exact", "at-least"):
                height = Length.parse(parts[0])
                return (
                    LineSpacing.exact(height)
                    if parts[1] == "exact"
                    else LineSpacing.at_least(height)
                )
        except InvalidValueError:
            pass
        self._report(
            Code.INVALID_VALUE,
            "invalid line spacing",
            span,
            help="use a multiple (1.15) or (12pt, exact) / (12pt, at-least)",
        )
        return None

    def _assign(
        self,
        key: str,
        value: object,
        span: Span,
        decl: Declaration,
        targets: Sequence[Target],
        meta: Sequence[str],
        values: dict[Target, dict[str, object]],
    ) -> None:
        # The first listed target that knows the key wins (in table styles, borders are
        # table borders at the top level and cell borders in a header-row block).
        ordered = [*targets, *(t for t in Target if t not in targets)]
        for target in ordered:
            if key not in REGISTRY[target]:
                continue
            bucket = values[target]
            if target not in targets:
                self._report(
                    Code.UNKNOWN_PROPERTY,
                    f"`{key}` is a {target.label} property and is not allowed here",
                    decl.key_span,
                    help="character styles take run properties only; table and cell "
                    "properties belong in table styles",
                )
                return
            try:
                PropertySet(target, {key: value})
            except InvalidValueError as e:
                self._report(Code.INVALID_VALUE, str(e), span, "invalid value")
                return
            bucket[key] = value
            return
        known = [name for t in targets for name in REGISTRY[t]] + list(meta) + list(FLAG_ALIASES)
        close = difflib.get_close_matches(decl.key, known, n=1)
        self._report(
            Code.UNKNOWN_PROPERTY,
            f"unknown property `{decl.key}`",
            decl.key_span,
            f"did you mean `{close[0]}`?" if close else "",
        )

    def _property_set(self, target: Target, values: dict[str, object], span: Span) -> PropertySet:
        try:
            return PropertySet(target, values)
        except InvalidValueError as e:
            self._report(Code.INVALID_VALUE, str(e), span)
            return {
                Target.RUN: EMPTY_RUN,
                Target.PARAGRAPH: EMPTY_PARAGRAPH,
                Target.TABLE: EMPTY_TABLE,
                Target.CELL: EMPTY_CELL,
            }[target]

    # --- rules -----------------------------------------------------------------------

    def _defaults(self, rule: DefaultsRule) -> None:
        values, _ = self._properties(rule.items, (Target.RUN, Target.PARAGRAPH), context="defaults")
        self._run = self._run.merged(self._property_set(Target.RUN, values[Target.RUN], rule.span))
        self._paragraph = self._paragraph.merged(
            self._property_set(Target.PARAGRAPH, values[Target.PARAGRAPH], rule.span)
        )

    def _style_rule(self, rule: StyleRule) -> None:
        name = rule.name.text
        sid = style_id(name)
        kind = {
            "character": StyleKind.CHARACTER,
            "table": StyleKind.TABLE,
        }.get(rule.kind, StyleKind.PARAGRAPH)
        existing = self._styles.get(sid)
        if existing is None and rule.action == "modify" and name in self.builtins:
            existing = self.builtins[name]()  # modify Word's own definition
        if rule.action == "define" and existing is not None:
            self._report(
                Code.STYLE_DEFINITION,
                f"style `{name}` is already defined",
                rule.name.span,
                help=f'use `modify {rule.kind} "{name}"` to change it, or `replace` to redefine it',
            )
            return
        if sid == "Normal" and kind is not StyleKind.PARAGRAPH:
            self._report(Code.STYLE_DEFINITION, "Normal must be a paragraph style", rule.name.span)
            return
        if rule.action == "modify" and existing is not None and existing.kind is not kind:
            self._report(
                Code.STYLE_DEFINITION,
                f"`{name}` is a {existing.kind.value} style, not {kind.value}",
                rule.name.span,
            )
            return
        base = existing if rule.action == "modify" else None
        if kind is StyleKind.TABLE:
            self._table_style(rule, name, base)
            return

        targets = (Target.RUN,) if kind is StyleKind.CHARACTER else (Target.RUN, Target.PARAGRAPH)
        meta_keys = CHARACTER_META if kind is StyleKind.CHARACTER else STYLE_META
        values, meta = self._properties(rule.items, targets, meta_keys, f"{rule.kind} style")
        run_props = self._property_set(Target.RUN, values[Target.RUN], rule.span)
        para_props = self._property_set(Target.PARAGRAPH, values[Target.PARAGRAPH], rule.span)

        if rule.based_on is not None:
            based_on: str | None = rule.based_on.text
            self._references.append(
                _Reference(
                    "style",
                    based_on,
                    f"style `{name}`: based-on style",
                    rule.based_on.span,
                    self._require_source(),
                )
            )
        elif base is not None:
            based_on = base.based_on
        else:
            based_on = "Normal" if kind is StyleKind.PARAGRAPH and sid != "Normal" else None

        style = base or Style(kind, name, based_on)
        style = replace(
            style,
            based_on=based_on,
            run=style.run.merged(run_props),
            paragraph=style.paragraph.merged(para_props),
            **self._style_meta(meta, name),
        )
        if rule.kind == "linked":
            self._linked(style, rule)
            return
        self._styles[sid] = style

    def _table_style(self, rule: StyleRule, name: str, base: Style | None) -> None:
        """table NAME [: BASE] { borders: ...; header-row { bold; } banded-rows { odd { ... } } }"""
        declarations = [item for item in rule.items if isinstance(item, Declaration)]
        blocks = [item for item in rule.items if isinstance(item, Block)]
        values, meta = self._properties(
            declarations,
            (Target.RUN, Target.TABLE, Target.CELL, Target.PARAGRAPH),
            TABLE_META,
            "a table style",
        )
        style = base or Style(StyleKind.TABLE, name)
        if rule.based_on is not None:
            style = replace(style, based_on=rule.based_on.text)
            self._references.append(
                _Reference(
                    "table-style",
                    rule.based_on.text,
                    f"style `{name}`: based-on style",
                    rule.based_on.span,
                    self._require_source(),
                )
            )
        conditions = {c.kind: c for c in style.conditions}
        repeat = style.repeat_header
        for block in blocks:
            for kind, items, meta_block in self._table_part(block):
                if kind is None:
                    continue
                if meta_block.get("repeat") is not None:
                    flag = self._bool(meta_block["repeat"])
                    repeat = repeat if flag is None else flag
                part_values, _ = self._properties(
                    items, CONDITION_TARGETS, ("repeat",) if kind == "firstRow" else (), block.name
                )
                old = conditions.get(kind, TableCondition(kind))
                conditions[kind] = TableCondition(
                    kind,
                    old.paragraph.merged(
                        self._property_set(
                            Target.PARAGRAPH, part_values[Target.PARAGRAPH], block.span
                        )
                    ),
                    old.run.merged(
                        self._property_set(Target.RUN, part_values[Target.RUN], block.span)
                    ),
                    old.cell.merged(
                        self._property_set(Target.CELL, part_values[Target.CELL], block.span)
                    ),
                )
        style = replace(
            style,
            run=style.run.merged(self._property_set(Target.RUN, values[Target.RUN], rule.span)),
            paragraph=style.paragraph.merged(
                self._property_set(Target.PARAGRAPH, values[Target.PARAGRAPH], rule.span)
            ),
            table=style.table.merged(
                self._property_set(Target.TABLE, values[Target.TABLE], rule.span)
            ),
            cell=style.cell.merged(self._property_set(Target.CELL, values[Target.CELL], rule.span)),
            conditions=tuple(conditions.values()),
            repeat_header=repeat,
            **self._style_meta(meta, name),
        )
        self._styles[style_id(name)] = style

    def _table_part(
        self, block: Block
    ) -> list[tuple[str | None, list[Declaration | Block], dict[str, Declaration]]]:
        """(tblStylePr type, declarations, meta) for a table-style block; banding blocks give
        one entry per odd/even sub-block (declarations directly inside mean odd)."""
        if block.args:
            self._report(Code.STYLESHEET_SYNTAX, f"`{block.name}` takes no arguments", block.span)
        if block.name in TABLE_PARTS:
            meta = {
                item.key: item
                for item in block.items
                if isinstance(item, Declaration) and item.key == "repeat"
            }
            if meta and block.name not in ("header-row", "first-row"):
                self._report(
                    Code.UNKNOWN_PROPERTY, "`repeat` applies to the header row", block.span
                )
                meta = {}
            items = [
                i for i in block.items if not (isinstance(i, Declaration) and i.key == "repeat")
            ]
            return [(TABLE_PARTS[block.name], items, meta)]
        if block.name in BANDS:
            odd, even = BANDS[block.name]
            parts: list[tuple[str | None, list[Declaration | Block], dict[str, Declaration]]] = []
            direct = [i for i in block.items if isinstance(i, Declaration)]
            if direct:
                parts.append((odd, list(direct), {}))
            for sub in block.items:
                if isinstance(sub, Block):
                    if sub.name in ("odd", "even") and not sub.args:
                        parts.append((odd if sub.name == "odd" else even, list(sub.items), {}))
                    else:
                        self._report(
                            Code.STYLESHEET_SYNTAX,
                            f"`{block.name}` contains `odd {{ ... }}` and `even {{ ... }}`",
                            sub.span,
                        )
            return parts
        close = difflib.get_close_matches(block.name, [*TABLE_PARTS, *BANDS], n=1)
        self._report(
            Code.STYLESHEET_SYNTAX,
            f"unknown table part `{block.name}`",
            block.span,
            f"did you mean `{close[0]}`?" if close else "",
            help="header-row, last-row, first-column, last-column, banded-rows, banded-columns, "
            "top-left-cell, top-right-cell, bottom-left-cell, bottom-right-cell",
        )
        return [(None, [], {})]

    def _require_source(self) -> Source:
        assert self._source is not None
        return self._source

    def _style_meta(self, meta: dict[str, Declaration], name: str) -> dict[str, object]:
        changes: dict[str, object] = {}
        source = self._require_source()
        if (decl := meta.get("next")) is not None and decl.value is not None:
            changes["next"] = _unquote(decl.value)
            span = decl.value_span or decl.key_span
            self._references.append(
                _Reference("style", changes["next"], f"style `{name}`: next style", span, source)
            )  # type: ignore[arg-type]
        if (decl := meta.get("numbering")) is not None and decl.value is not None:
            span = decl.value_span or decl.key_span
            m = NUMBERING_REF_RE.fullmatch(decl.value)
            if m is None:
                self._report(
                    Code.INVALID_VALUE,
                    "invalid numbering reference",
                    span,
                    help="write `numbering: Headings level 1;`",
                )
            else:
                ref = NumberingRef(m.group(1) or m.group(2), int(m.group(3) or 1))
                if not 1 <= ref.level <= 9:
                    self._report(Code.INVALID_VALUE, "list levels are 1-9", span)
                else:
                    changes["numbering"] = ref
                    self._references.append(
                        _Reference(
                            "numbering", ref.name, f"style `{name}`: numbering", span, source
                        )
                    )
        if (decl := meta.get("ui-priority")) is not None:
            number = self._int(decl, 0, 99)
            if number is not None:
                changes["ui_priority"] = number
        if (decl := meta.get("quick-format")) is not None:
            flag = self._bool(decl)
            if flag is not None:
                changes["quick_format"] = flag
        return changes

    def _linked(self, paragraph_style: Style, rule: StyleRule) -> None:
        """A linked style: a paragraph style plus its "<name> Char" character style."""
        name = paragraph_style.name
        char_name = f"{name} Char"
        paragraph_style = replace(paragraph_style, link=char_name)
        existing_char = self._styles.get(style_id(char_name))
        char_style = Style(
            StyleKind.CHARACTER,
            char_name,
            run=(existing_char.run if existing_char else EMPTY_RUN).merged(paragraph_style.run),
            link=name,
            quick_format=False,
            ui_priority=paragraph_style.ui_priority,
        )
        self._styles[style_id(name)] = paragraph_style
        self._styles[style_id(char_name)] = char_style

    def _numbering_rule(self, rule: NumberingRule) -> None:
        name = rule.name.text
        if name in self._numbering:
            self._report(
                Code.STYLE_DEFINITION, f"numbering `{name}` is already defined", rule.name.span
            )
            return
        levels: dict[int, NumberingLevel] = {}
        for item in rule.items:
            if isinstance(item, Declaration) or item.name != "level":
                span = item.span
                self._report(
                    Code.STYLESHEET_SYNTAX,
                    "a numbering definition contains `level N { ... }` blocks",
                    span,
                    help='e.g. level 1 { format: decimal; text: "%1."; }',
                )
                continue
            if len(item.args) != 1 or not item.args[0].isdigit() or not 1 <= int(item.args[0]) <= 9:
                self._report(Code.STYLESHEET_SYNTAX, "expected a level number 1-9", item.span)
                continue
            number = int(item.args[0])
            if number in levels:
                self._report(Code.STYLE_DEFINITION, f"level {number} is defined twice", item.span)
                continue
            level = self._level(item, number, name)
            if level is not None:
                levels[number] = level
        self._numbering[name] = Numbering(name, tuple(levels[k] for k in sorted(levels)))

    def _level(self, block: Block, number: int, numbering: str) -> NumberingLevel | None:
        values, meta = self._properties(
            block.items, (Target.RUN, Target.PARAGRAPH), LEVEL_META, "a list level"
        )
        run, paragraph = values[Target.RUN], values[Target.PARAGRAPH]
        fields: dict[str, object] = {}
        if (decl := meta.get("format")) is not None and decl.value is not None:
            fmt = _kebab_to_camel(_unquote(decl.value))
            if fmt not in NUMBER_FORMATS:
                options = ", ".join(sorted(_camel_to_kebab(f) for f in NUMBER_FORMATS))
                self._report(
                    Code.INVALID_VALUE,
                    f"unknown number format `{decl.value}`",
                    decl.value_span or decl.key_span,
                    help=f"one of: {options}",
                )
            else:
                fields["format"] = fmt
        if (decl := meta.get("bullet")) is not None and decl.value is not None:
            fields["format"] = "bullet"
            fields["text"] = _unquote(decl.value)
        if (decl := meta.get("text")) is not None and decl.value is not None:
            fields["text"] = _unquote(decl.value)
        if "text" not in fields:
            fields["text"] = "•" if fields.get("format") == "bullet" else f"%{number}."
        for key, attr, high in (("start", "start", 32767), ("restart-after", "restart_after", 8)):
            decl = meta.get(key)
            value = self._int(decl, 0, high) if decl is not None else None
            if value is not None:
                fields[attr] = value
        decl = meta.get("legal")
        flag = self._bool(decl) if decl is not None else None
        if flag is not None:
            fields["legal"] = flag
        for key, attr in (("suffix", "suffix"), ("marker-align", "marker_align")):
            if (decl := meta.get(key)) is not None and decl.value is not None:
                fields[attr] = _unquote(decl.value)
        if (decl := meta.get("style")) is not None and decl.value is not None:
            fields["style"] = _unquote(decl.value)
            span = decl.value_span or decl.key_span
            self._level_links.append(
                (fields["style"], numbering, number, span, self._require_source())  # type: ignore[arg-type]
            )
            self._references.append(
                _Reference(
                    "paragraph-style",
                    fields["style"],
                    f"numbering `{numbering}` level {number}: style",  # type: ignore[arg-type]
                    decl.value_span or decl.key_span,
                    self._require_source(),
                )
            )
        try:
            return NumberingLevel(
                number,
                paragraph=self._property_set(Target.PARAGRAPH, paragraph, block.span),
                run=self._property_set(Target.RUN, run, block.span),
                **fields,  # type: ignore[arg-type]
            )
        except ValueError as e:
            self._report(Code.INVALID_VALUE, str(e), block.span)
            return None

    def _int(self, decl: Declaration, low: int, high: int) -> int | None:
        text = _unquote(decl.value or "")
        if text.isdigit() and low <= int(text) <= high:
            return int(text)
        self._report(
            Code.INVALID_VALUE,
            f"`{decl.key}` must be a whole number {low}-{high}",
            decl.value_span or decl.key_span,
        )
        return None

    def _bool(self, decl: Declaration) -> bool | None:
        if decl.value is None:
            return True
        text = _unquote(decl.value)
        if text in ("true", "on", "yes"):
            return True
        if text in ("false", "off", "no"):
            return False
        self._report(
            Code.INVALID_VALUE,
            f"`{decl.key}` must be true or false",
            decl.value_span or decl.key_span,
        )
        return None

    # --- cross-references ----------------------------------------------------------------

    def _check_level_links(self) -> None:
        """Word numbers a paragraph style through one list level: a style linked to two
        levels (by two `style:` declarations, or by its own `numbering:`) is an error."""
        linked: dict[str, str] = {}
        for name, numbering, number, span, source in self._level_links:
            sid = style_id(name)
            here = f"{numbering} level {number}"
            style = self._styles.get(sid)
            first = linked.get(sid)
            if style is not None and style.numbering is not None:
                first = f"{style.numbering.name} level {style.numbering.level}"
            if first is not None and first != here:
                self._source = source
                self._report(
                    Code.STYLE_DEFINITION,
                    f"style `{name}` is linked to two list levels: {first} and {here}",
                    span,
                    help="a paragraph style can number only one list level",
                )
            linked.setdefault(sid, here)

    def _check_references(self) -> None:
        for ref in self._references:
            self._source = ref.source
            if ref.kind == "numbering":
                if ref.name not in self._numbering:
                    self._report(
                        Code.STYLE_DEFINITION, f"{ref.what} `{ref.name}` is not defined", ref.span
                    )
                continue
            style = self._styles.get(style_id(ref.name))
            if style is None and ref.name in self.builtins:
                style = self.builtins[ref.name]()
            if style is None and style_id(ref.name) != "Normal":
                close = difflib.get_close_matches(
                    ref.name, [s.name for s in self._styles.values()], n=1
                )
                self._report(
                    Code.STYLE_DEFINITION,
                    f"{ref.what} `{ref.name}` is not defined",
                    ref.span,
                    f"did you mean `{close[0]}`?" if close else "",
                )
            elif (
                ref.kind == "paragraph-style"
                and style is not None
                and style.kind is not StyleKind.PARAGRAPH
            ):
                self._report(
                    Code.STYLE_DEFINITION,
                    f"{ref.what} `{ref.name}` is not a paragraph style",
                    ref.span,
                )
            elif (
                ref.kind == "table-style"
                and style is not None
                and style.kind is not StyleKind.TABLE
            ):
                self._report(
                    Code.STYLE_DEFINITION, f"{ref.what} `{ref.name}` is not a table style", ref.span
                )


def compile_stylesheets(
    sheets: Iterable[StyleSheet], builtins: Mapping[str, Callable[[], Style]] | None = None
) -> CompiledStyles:
    if builtins is None:
        from wordgenx.lower.builtins import BUILTIN_STYLES

        builtins = BUILTIN_STYLES
    compiler = StyleSheetCompiler(builtins=builtins)
    for sheet in sheets:
        compiler.add(sheet)
    return compiler.result()
