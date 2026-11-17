"""The command table and the lowering agree."""

from redocx.commands.builtin import COMMANDS, body_kind
from redocx.commands.spec import Placement
from redocx.lower.lowering import BLOCK_HANDLERS, INLINE_HANDLERS
from redocx.syntax.commands import ArgKind


def test_every_command_has_a_handler() -> None:
    for name, spec in COMMANDS.items():
        if spec.planned is not None or spec.placement is Placement.PART:
            continue
        if spec.placement in (Placement.BLOCK, Placement.BOTH):
            assert name in BLOCK_HANDLERS, name
        if spec.placement in (Placement.INLINE, Placement.BOTH):
            assert name in INLINE_HANDLERS, name


def test_every_handler_has_a_spec() -> None:
    for name in BLOCK_HANDLERS:
        assert COMMANDS[name].placement in (Placement.BLOCK, Placement.BOTH), name
    for name in INLINE_HANDLERS:
        assert COMMANDS[name].placement in (Placement.INLINE, Placement.BOTH), name


def test_positional_options_exist() -> None:
    for spec in COMMANDS.values():
        if spec.positional is not None:
            assert spec.option(spec.positional) is not None, spec.name


def test_body_kinds() -> None:
    assert body_kind("include") is ArgKind.STRING
    assert body_kind("field") is ArgKind.RAW
    assert body_kind("m") is ArgKind.MATH
    assert body_kind("styles") is ArgKind.STYLESHEET
    assert body_kind("p") is ArgKind.MARKUP
    assert body_kind("section") is ArgKind.MARKUP  # takes none; a stray one is reported
    assert body_kind("my-macro") is ArgKind.MARKUP
