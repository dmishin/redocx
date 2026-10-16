"""Command specifications: what a command accepts (design §3.2, architecture §4.1).

A `CommandSpec` says where a command may appear, which options it takes and of which
type, and what its body is. The lowering binds each command against its spec before it
gives the command meaning, so every command reads its options the same way and reports
the same errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property

from wordgenx.commands.types import ValueType
from wordgenx.props import Target
from wordgenx.syntax.commands import ArgKind


class Placement(Enum):
    BLOCK = "block"
    """Stands alone, as a block of its own."""
    INLINE = "inline"
    """Inside text."""
    BOTH = "both"
    """Inside text; alone it forms a paragraph (\\pagebreak), or the body decides (\\raw)."""
    PART = "part"
    """Only directly inside another command (\\row in \\table, \\cell in \\row)."""


@dataclass(frozen=True)
class OptionSpec:
    name: str
    type: ValueType
    doc: str = ""
    required: bool = False


@dataclass(frozen=True)
class CommandSpec:
    name: str
    placement: Placement
    doc: str
    options: tuple[OptionSpec, ...] = ()
    positional: str | None = None
    """The option a bare value fills when it is not a flag: \\list[Legal] is numbering=Legal."""
    variadic: bool = False
    """The positional option collects every bare value (\\tabs[(8cm, center), (16cm, right)])."""
    props: Target | None = None
    """Any formatting property of this target is an option too (\\p[space-before=6pt])."""
    patterns: Mapping[str, ValueType] = field(default_factory=lambda: {})
    """Option-name prefix -> value type, for open-ended names (\\meta[custom.client=...])."""
    body: ArgKind | None = ArgKind.MARKUP
    """How the body is read; None: the command takes no body."""
    body_required: bool = False
    body_doc: str = "a body"
    """What the body holds, for messages: "a file name", "a formula"."""
    planned: str | None = None
    """The milestone that implements the command; it is ignored with a warning until then."""
    keep_body: bool = False
    """A planned command whose body text is kept (rather than dropped) meanwhile."""
    planned_options: Mapping[str, str] = field(default_factory=lambda: {})
    """Options of the language that are not implemented yet -> their milestone."""
    title: str = ""
    """How messages name it, when it is not a command ("code blocks")."""

    @property
    def shown(self) -> str:
        return self.title or f"`\\{self.name}`"

    @cached_property
    def by_name(self) -> Mapping[str, OptionSpec]:
        return {option.name: option for option in self.options}

    def option(self, name: str) -> OptionSpec | None:
        return self.by_name.get(name)

    def usage(self) -> str:
        """``\\list[numbering, continue, restart=...]{...}``, from the spec."""
        parts: list[str] = []
        for option in self.options:
            if option.name == self.positional:
                parts.insert(0, f"{option.name}, ..." if self.variadic else option.name)
            elif option.type.flag:
                parts.append(option.name)
            else:
                parts.append(f"{option.name}=...")
        parts += [f"{prefix}NAME=..." for prefix in self.patterns]
        if self.props is not None:
            parts.append(f"<{self.props.label} properties>")
        if self.title:  # attributes in braces: ```python {#lst:x, caption=...}
            return f"{{{', '.join(parts)}}}"
        options = f"[{', '.join(parts)}]" if parts else ""
        body = "" if self.body is None else "{...}"
        return f"\\{self.name}{options}{body}"
