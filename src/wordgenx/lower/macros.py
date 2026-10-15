"""Macros: \\define and \\let, expanded by substitution on the AST (design §3.18).

Parameters are named options; ``#name`` refers to one inside the body and ``#body``
to the call's body. A parameter without a default is optional and expands to
nothing when not given; an option whose value is an unset parameter is dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from wordgenx.syntax import ast
from wordgenx.syntax.source import Source, Span

Binding = ast.Value | None
"""What a parameter is bound to: an option value, the call's body, or nothing."""


@dataclass(frozen=True)
class Macro:
    name: str
    params: Mapping[str, Binding]
    """Parameter -> default (None: no default)."""
    body: tuple[ast.Block, ...]
    span: Span
    source: Source


def define_macro(node: ast.Define, source: Source, report: Callable[[str, Span], None]) -> Macro:
    params: dict[str, Binding] = {}
    for option in node.params.items if node.params else ():
        if option.key is None:
            if isinstance(option.value, ast.BareValue):
                params[option.value.text] = None
            else:
                report("parameters are names, optionally with a default: name=value", option.span)
        else:
            params[option.key] = option.value
    if "body" in params:
        report("`body` is reserved for the call's body; use #body without declaring it", node.span)
    body = node.body.blocks if node.body else ()
    return Macro(node.name, params, body, node.span, source)


def let_macro(node: ast.Let, source: Source) -> Macro:
    return Macro(node.name, {}, node.body.blocks if node.body else (), node.span, source)


class Substitution:
    """Replace #params in a macro body with their bindings."""

    def __init__(
        self,
        bindings: Mapping[str, Binding],
        report: Callable[[str, Span], None],
    ) -> None:
        self.bindings = bindings
        self.report = report

    def blocks(self, blocks: tuple[ast.Block, ...]) -> tuple[ast.Block, ...]:
        return tuple(self._node(block) for block in blocks)  # type: ignore[misc]

    def _bound(self, param: ast.Param | ast.ParamValue) -> tuple[bool, Binding]:
        if param.name not in self.bindings:
            self.report(f"unknown parameter `#{param.name}`", param.span)
            return False, None
        return True, self.bindings[param.name]

    def _param_inlines(self, param: ast.Param) -> list[ast.Inline]:
        known, value = self._bound(param)
        if not known or value is None:
            return []
        if isinstance(value, ast.BareValue | ast.StringValue):
            return [ast.Text(value.text, span=param.span)]
        if isinstance(value, ast.MarkupBody):
            if len(value.blocks) == 1 and isinstance(value.blocks[0], ast.Paragraph):
                return list(value.blocks[0].content)
            return [ast.Group(value.blocks, span=param.span)]
        self.report(
            f"`#{param.name}` is a {type(value).__name__}, which cannot be used as text", param.span
        )
        return []

    def _node(self, node: Any) -> Any:
        if not isinstance(node, ast.AstNode):
            return node
        changes: dict[str, Any] = {}
        for f in fields(node):
            if f.name == "span":
                continue
            value = getattr(node, f.name)
            if isinstance(value, tuple):
                new: list[Any] = []
                for item in value:  # pyright: ignore[reportUnknownVariableType]
                    if isinstance(item, ast.Param):
                        new.extend(self._param_inlines(item))
                    elif isinstance(item, ast.Option) and isinstance(item.value, ast.ParamValue):
                        known, bound = self._bound(item.value)
                        if known and bound is not None:  # an unset parameter drops the option
                            new.append(replace(item, value=bound))
                    else:
                        new.append(self._node(item))
                changes[f.name] = tuple(new)
            elif isinstance(value, ast.AstNode):
                changes[f.name] = self._node(value)
        return replace(node, **changes)
