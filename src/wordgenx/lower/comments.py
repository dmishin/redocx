r"""Comments: \comment (design §3.17)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from wordgenx import ir
from wordgenx.commands.bind import Args
from wordgenx.lower.common import Format, InlineHandler, Piece
from wordgenx.lower.fields import build_time
from wordgenx.syntax import ast
from wordgenx.syntax.diagnostics import Code

if TYPE_CHECKING:
    from wordgenx.lower.lowering import Lowering

TEXT_STYLE = "annotation text"
REFERENCE_STYLE = "annotation reference"
DEFAULT_AUTHOR = "Author"


def comment(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    r"""\comment[text={...}, author=..., initials=..., date=...]{commented text}"""
    if lw.part is not None:
        lw.report(
            Code.MISPLACED,
            f"a comment cannot be in a {lw.part}",
            command.span,
            help="comment on the text in the body",
        )
        return lw.body_inlines(command, fmt)
    text = args.values.get("text")
    with lw.contained(part="comment"):
        if isinstance(text, ast.MarkupBody):
            blocks = lw.blocks(text.blocks)
        else:
            blocks = [ir.Paragraph((ir.Run(str(text or "")),))]
    lw.use_style(TEXT_STYLE, ir.StyleKind.PARAGRAPH, command.span)
    lw.use_style(REFERENCE_STYLE, ir.StyleKind.CHARACTER, command.span)
    content = tuple(
        replace(b, style=TEXT_STYLE) if isinstance(b, ir.Paragraph) and b.style is None else b
        for b in blocks
    )
    author = args.text("author") or lw.meta.author or DEFAULT_AUTHOR
    initials = args.text("initials") or "".join(word[0] for word in author.split() if word)
    moment = build_time()
    if (date := args.text("date")) is not None:
        try:
            moment = datetime.fromisoformat(date)
        except ValueError:
            lw.report(
                Code.INVALID_ARGUMENT,
                f"`date`: expected YYYY-MM-DD or YYYY-MM-DDThh:mm, got `{date}`",
                args.span_of("date"),
            )
    lw.comment_count += 1
    name = f"_Comment{lw.comment_count}"
    made = ir.Comment(name, content, author, initials.upper(), moment)
    lw.has_content = True
    return [
        Piece("", fmt, "mark", comment=ir.CommentStart(made)),
        *lw.body_inlines(command, fmt),
        Piece("", fmt, "mark", comment=ir.CommentEnd(name)),
    ]


INLINES: dict[str, InlineHandler] = {"comment": comment}
