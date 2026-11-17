"""Binding: a parsed command checked against its spec, giving typed arguments.

`bind` reads every option the same way:

* a bare word names a flag (a Bool option, or a flag-like formatting property) or, failing
  that, is the positional value;
* ``key=value`` is an option of the spec, a formatting property (when the spec takes
  them) or a pattern option (``custom.NAME``);
* each value is parsed by the option's type; unknown and repeated options are errors,
  options the language plans but does not implement yet are warnings.

The body is checked too: present when required, absent when the command takes none.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from redocx.commands.spec import CommandSpec
from redocx.commands.types import Fields, OptionError, ValueType, suggestion, value_text
from redocx.props import REGISTRY, SHORTHANDS, PropertySet, Target, UnknownPropertyError
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code, Severity
from redocx.syntax.source import Span
from redocx.values import InvalidValueError

_FAILED = object()
"""What `_Binder.parse` returns for a value it reported (None is a valid value: off)."""
ALIGN_WORDS = frozenset({"left", "center", "right", "justify", "distribute"})
"""Bare paragraph alignments: \\p[center]."""


@dataclass(frozen=True)
class Problem:
    code: Code
    message: str
    span: Span
    label: str = ""
    help: str | None = None
    severity: Severity = Severity.ERROR


@dataclass(frozen=True)
class Args(Fields):
    """The bound options of a command."""

    props: PropertySet | None = None
    """The formatting properties given as options (when the spec takes them)."""
    patterns: Mapping[str, tuple[object, Span]] = field(default_factory=lambda: {})
    """Pattern options (``custom.client``) -> (value, span), in the order given."""


def bind(command: ast.Command, spec: CommandSpec) -> tuple[Args, list[Problem]]:
    return _Binder(command, spec).run()


class _Binder:
    def __init__(self, command: ast.Command, spec: CommandSpec) -> None:
        self.command = command
        self.spec = spec
        self.values: dict[str, object] = {}
        self.spans: dict[str, Span] = {}
        self.props: dict[str, object] = {}
        self.patterns: dict[str, tuple[object, Span]] = {}
        self.problems: list[Problem] = []

    def error(self, message: str, span: Span, label: str = "", help: str | None = None) -> None:
        self.problems.append(Problem(Code.INVALID_ARGUMENT, message, span, label, help))

    def run(self) -> tuple[Args, list[Problem]]:
        options = self.command.options
        for option in options.items if options else ():
            if option.key is None:
                self.bare(option)
            else:
                self.keyed(option, option.key)
        self.check_required()
        self.check_body()
        props = self.property_set()
        span = self.command.span
        args = Args(self.values, self.spans, span, props=props, patterns=self.patterns)
        return args, self.problems

    # --- options -------------------------------------------------------------------------

    def bare(self, option: ast.Option) -> None:
        spec = self.spec
        word = option.value.text if isinstance(option.value, ast.BareValue) else None
        if word is not None:
            flag = spec.option(word)
            if flag is not None and flag.type.flag:  # the bare name means on
                value = self.parse(word, flag.type, ast.BareValue("on", span=option.span))
                self.store(word, value, option.span)
                return
            if word in spec.planned_options:
                self.planned(word, option.span)
                return
            if spec.props is not None and self.prop_flag(spec.props, word, option.span):
                return
        if spec.positional is not None and (spec.variadic or spec.positional not in self.values):
            positional = spec.option(spec.positional)
            assert positional is not None
            value = self.parse(positional.name, positional.type, option.value)
            if value is _FAILED:
                return
            if spec.variadic:
                previous = self.values.get(positional.name, ())
                assert isinstance(previous, tuple)
                value = (*previous, value)  # pyright: ignore[reportUnknownVariableType]
                self.values[positional.name] = value
                self.spans.setdefault(positional.name, option.value.span)
            else:
                self.store(positional.name, value, option.value.span)
            return
        self.unknown(word or "", option.span)

    def keyed(self, option: ast.Option, key: str) -> None:
        spec = self.spec
        if key in spec.planned_options:
            self.planned(key, option.span)
            return
        if (known := spec.option(key)) is not None:
            if key in self.values:
                self.error(f"`{key}` is given twice", option.span)
                return
            value = self.parse(key, known.type, option.value)
            if value is not _FAILED:
                self.store(key, value, option.value.span)
            return
        target = spec.props
        if target is not None and (key in REGISTRY[target] or key in SHORTHANDS):
            self.prop(target, key, value_text(option.value), option)
            return
        for prefix, kind in spec.patterns.items():
            if key.startswith(prefix) and len(key) > len(prefix):
                value = self.parse(key, kind, option.value)
                if value is not _FAILED:
                    self.patterns[key] = (value, option.value.span)
                return
        self.unknown(key, option.span)

    def parse(self, name: str, kind: ValueType, value: ast.Value) -> object:
        try:
            return kind.parse(value)
        except OptionError as e:
            self.error(f"`{name}`: {e.message}", e.span, e.label, e.help)
            return _FAILED

    def store(self, name: str, value: object, span: Span) -> None:
        if name in self.values:
            self.error(f"`{name}` is given twice", span)
            return
        self.values[name] = value
        self.spans[name] = span

    def prop_flag(self, target: Target, word: str, span: Span) -> bool:
        """A bare formatting property: an alignment (\\p[center]) or a flag (keep-with-next)."""
        if target is Target.PARAGRAPH and word in ALIGN_WORDS:
            self.props["align"] = word
            return True
        if word in REGISTRY[target]:
            try:
                PropertySet(target, {word: True})
            except InvalidValueError:
                return False
            self.props[word] = True
            return True
        return False

    def prop(self, target: Target, key: str, text: str, option: ast.Option) -> None:
        try:
            PropertySet(target, {key: text})
        except (InvalidValueError, UnknownPropertyError) as e:
            self.error(str(e), option.span)
            return
        if key in self.props:
            self.error(f"`{key}` is given twice", option.span)
            return
        self.props[key] = text

    def property_set(self) -> PropertySet | None:
        target = self.spec.props
        if target is None:
            return None
        try:
            return PropertySet(target, self.props)
        except InvalidValueError as e:  # a conflict between properties valid on their own
            self.error(
                str(e), self.command.options.span if self.command.options else self.command.span
            )
            return PropertySet(target)

    def planned(self, name: str, span: Span) -> None:
        self.problems.append(
            Problem(
                Code.NOT_SUPPORTED_YET,
                f"option `{name}` of {self.spec.shown} is not supported yet; it is ignored",
                span,
                help=f"planned for milestone {self.spec.planned_options[name]}",
                severity=Severity.WARNING,
            )
        )

    def unknown(self, name: str, span: Span) -> None:
        spec = self.spec
        names = [option.name for option in spec.options]
        if spec.props is not None:
            names += [*REGISTRY[spec.props], *SHORTHANDS]
        self.error(
            f"unknown option `{name}` for {spec.shown}" if name else
            f"unexpected value for {spec.shown}",
            span,
            suggestion(name, names) if name else "",
            help=spec.usage(),
        )  # fmt: skip

    def check_required(self) -> None:
        for option in self.spec.options:
            if option.required and option.name not in self.values:
                self.error(
                    f"`\\{self.spec.name}` needs `{option.name}`",
                    self.command.span,
                    help=self.spec.usage(),
                )

    # --- body ----------------------------------------------------------------------------

    def check_body(self) -> None:
        body, spec = self.command.body, self.spec
        if spec.body is None:
            if body is not None and not (isinstance(body, ast.MarkupBody) and not body.blocks):
                self.error(f"`\\{spec.name}` takes no body", body.span)
            return
        if not spec.body_required:
            return
        # An empty markup body is allowed (\footnote{}); an empty file name or URL is not.
        if body is None or (isinstance(body, ast.VerbatimBody) and not body.text.strip()):
            self.error(
                f"`\\{spec.name}` needs {spec.body_doc}", self.command.span, help=spec.usage()
            )
