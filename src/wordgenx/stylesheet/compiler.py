"""Compile parsed style sheets into IR styles, numbering definitions and document defaults.

Every property is validated through the property registry; problems become
diagnostics pointing at the exact key or value, and compilation continues.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from wordgenx.ir import (
    NUMBER_FORMATS,
    Document,
    Numbering,
    NumberingLevel,
    NumberingRef,
    Style,
    StyleKind,
)
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import (
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    REGISTRY,
    PropertySet,
    Target,
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

    kind: str  # "style", "paragraph-style" or "numbering"
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
    _source: Source | None = None

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
        return CompiledStyles(
            tuple(self._styles.values()),
            tuple(self._numbering.values()),
            self._run,
            self._paragraph,
            tuple(self._diagnostics),
        )

    # --- declarations -----------------------------------------------------------------

    def _properties(
        self,
        items: Iterable[Declaration | Block],
        targets: Sequence[Target],
        meta: Sequence[str] = (),
        context: str = "",
    ) -> tuple[dict[str, object], dict[str, object], dict[str, Declaration]]:
        """Split declarations into run values, paragraph values and meta declarations."""
        run: dict[str, object] = {}
        paragraph: dict[str, object] = {}
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
            for key, value, span in self._expand(item):
                self._assign(key, value, span, item, targets, meta, run, paragraph)
        return run, paragraph, meta_found

    def _expand(self, decl: Declaration) -> list[tuple[str, object, Span]]:
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
        run: dict[str, object],
        paragraph: dict[str, object],
    ) -> None:
        for target, bucket in ((Target.RUN, run), (Target.PARAGRAPH, paragraph)):
            if key not in REGISTRY[target]:
                continue
            if target not in targets:
                kind = "run" if target is Target.RUN else "paragraph"
                self._report(
                    Code.UNKNOWN_PROPERTY,
                    f"`{key}` is a {kind} property and is not allowed here",
                    decl.key_span,
                    help="character styles take run properties only",
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
            return EMPTY_RUN if target is Target.RUN else EMPTY_PARAGRAPH

    # --- rules -----------------------------------------------------------------------

    def _defaults(self, rule: DefaultsRule) -> None:
        run, paragraph, _ = self._properties(
            rule.items, (Target.RUN, Target.PARAGRAPH), context="defaults"
        )
        self._run = self._run.merged(self._property_set(Target.RUN, run, rule.span))
        self._paragraph = self._paragraph.merged(
            self._property_set(Target.PARAGRAPH, paragraph, rule.span)
        )

    def _style_rule(self, rule: StyleRule) -> None:
        if rule.kind == "table":
            self._report(
                Code.UNSUPPORTED,
                "table styles are not supported yet; this rule is ignored",
                rule.span,
                help="table styles are planned for milestone M2",
                severity=Severity.WARNING,
            )
            return
        name = rule.name.text
        sid = style_id(name)
        kind = StyleKind.CHARACTER if rule.kind == "character" else StyleKind.PARAGRAPH
        existing = self._styles.get(sid)
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

        targets = (Target.RUN,) if kind is StyleKind.CHARACTER else (Target.RUN, Target.PARAGRAPH)
        meta_keys = CHARACTER_META if kind is StyleKind.CHARACTER else STYLE_META
        run, paragraph, meta = self._properties(
            rule.items, targets, meta_keys, f"{rule.kind} style"
        )
        run_props = self._property_set(Target.RUN, run, rule.span)
        para_props = self._property_set(Target.PARAGRAPH, paragraph, rule.span)

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
        run, paragraph, meta = self._properties(
            block.items, (Target.RUN, Target.PARAGRAPH), LEVEL_META, "a list level"
        )
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


def compile_stylesheets(sheets: Iterable[StyleSheet]) -> CompiledStyles:
    compiler = StyleSheetCompiler()
    for sheet in sheets:
        compiler.add(sheet)
    return compiler.result()
