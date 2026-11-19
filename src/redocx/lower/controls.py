r"""Content controls: \control (design §3.17)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.bind import Args
from redocx.lower.common import Format, InlineHandler, Piece, markup
from redocx.lower.fields import format_date
from redocx.lower.media import PLACEHOLDER_PNG
from redocx.props import PropertySet, Target
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.values import Length

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

PLACEHOLDER_STYLE = "Placeholder Text"
PLACEHOLDERS = {
    "text": "Click or tap here to enter text.",
    "rich-text": "Click or tap here to enter text.",
    "dropdown": "Choose an item.",
    "combo": "Choose an item.",
    "date": "Click or tap to enter a date.",
}
"""Word's own placeholder texts."""
CHECKED, UNCHECKED = "☒", "☐"  # ☒ ☐, as Word's checkbox shows them
CHECKBOX_FONT = "MS Gothic"
EMPTY_PICTURE = (Length.cm(4), Length.cm(3))


def control(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\control[type, tag=, title=, placeholder=, lock=, items=, format=, date=, checked]{value}"""
    kind = args.text("type", "text") or "text"
    has_body = bool(markup(command))
    items = tuple(args.items("items", str))
    if kind in ("dropdown", "combo") and not items:
        lw.report(
            Code.INVALID_ARGUMENT,
            f"a {kind} needs its choices: items=(...)",
            command.span,
            help='\\control[dropdown, items=("Draft", "Final")]',
        )
        return []
    content: tuple[ir.Inline, ...] = ()
    if has_body:
        if kind == "checkbox":
            lw.report(
                Code.INVALID_ARGUMENT,
                "a checkbox has no value text; write its label beside it",
                command.span,
                help="\\control[checkbox, checked] I agree",
            )
        else:
            content = lw.assemble(lw.body_inlines(command, fmt))
    moment: datetime | None = None
    picture = args.text("format") or "d MMMM yyyy"
    if (date := args.text("date")) is not None:
        try:
            moment = datetime.fromisoformat(date)
        except ValueError:
            lw.report(
                Code.INVALID_ARGUMENT,
                f"`date`: expected YYYY-MM-DD, got `{date}`",
                args.span_of("date"),
            )
        else:
            content = (ir.Run(format_date(moment, picture), fmt.props, fmt.style),)
    placeholder = False
    if kind == "picture":
        pictures = [i for i in content if isinstance(i, ir.Image)]
        if content and (len(pictures) != 1 or len(content) != 1):
            lw.report(
                Code.INVALID_ARGUMENT,
                "a picture control holds one \\image",
                command.span,
                help="\\control[picture]{\\image[width=4cm]{signature.png}}",
            )
            return []
        if not content:  # an empty frame for the reader's picture
            placeholder = True
            width = args.get("width", Length) or EMPTY_PICTURE[0]
            height = args.get("height", Length) or EMPTY_PICTURE[1]
            empty = ir.Image(PLACEHOLDER_PNG, "png", width, height, "", "Picture")
            content = (empty,)
    elif kind == "checkbox":
        box = CHECKED if args.flag("checked") else UNCHECKED
        font = PropertySet(Target.RUN, {"font": CHECKBOX_FONT})
        content = (ir.Run(box, fmt.props.merged(font), fmt.style),)
    elif not content:
        placeholder = True
        lw.use_style(PLACEHOLDER_STYLE, ir.StyleKind.CHARACTER, command.span)
        text = args.text("placeholder") or PLACEHOLDERS[kind]
        content = (ir.Run(text, fmt.props, PLACEHOLDER_STYLE),)
    made = ir.ContentControl(
        kind,
        content,
        showing_placeholder=placeholder,
        tag=args.text("tag", "") or "",
        title=args.text("title", "") or "",
        lock=args.text("lock"),
        items=items,
        date_format=picture,
        date=moment,
        language=lw.meta.language or "en-US",
        checked=args.flag("checked"),
    )
    lw.has_content = True
    return [Piece("", fmt, "control", control=made)]


INLINES: dict[str, InlineHandler] = {"control": control}
