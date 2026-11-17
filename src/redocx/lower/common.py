"""What the lowering modules share: inline pieces, paragraph assembly, handler types."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, get_args

from redocx import ir
from redocx.commands.bind import Args
from redocx.lower.typography import smarten
from redocx.props import EMPTY_RUN, PropertySet
from redocx.syntax import ast
from redocx.syntax.diagnostics import Diagnostic
from redocx.syntax.source import Source

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering

Located = tuple[Source, Diagnostic]
BLOCK_TYPES = get_args(ast.Block)
WHITESPACE_RE = re.compile(r"[ \t\r\n]+")


@dataclass(frozen=True)
class Format:
    props: PropertySet = EMPTY_RUN
    style: str | None = None
    link: tuple[int, str] | None = None
    """(link id, url) of the hyperlink the text belongs to."""


@dataclass(frozen=True)
class Piece:
    text: str
    format: Format
    kind: str = "text"
    """text (smart punctuation, whitespace collapse) | code (verbatim) | tab | line | page
    | column | image | field | mark (a bookmark start or end) | note."""
    image: ir.Image | None = None
    field: ir.Field | None = None
    mark: ir.BookmarkStart | ir.BookmarkEnd | None = None
    note: ir.Note | None = None
    raw: ir.RawXml | None = None
    math: ir.Math | None = None
    citation: ir.Run | None = None
    textbox: ir.TextBox | None = None
    comment: ir.CommentStart | ir.CommentEnd | None = None
    control: ir.ContentControl | None = None


BlockHandler = Callable[["Lowering", ast.Command, Args], list[ir.Block]]
"""Lowers a block command, given its bound options."""
InlineHandler = Callable[["Lowering", ast.Command, Args, Format], list[Piece]]
"""Lowers an inline command in the given formatting, given its bound options."""


# --- command bodies (their form is checked when the command is bound) -----------------


def markup(command: ast.Command) -> tuple[ast.Block, ...]:
    return command.body.blocks if isinstance(command.body, ast.MarkupBody) else ()


def verbatim(command: ast.Command) -> str:
    return command.body.text.strip() if isinstance(command.body, ast.VerbatimBody) else ""


# --- small helpers -------------------------------------------------------------------------


def shown_path(path: Path) -> str:
    """`path` as messages show it: relative to the current folder when it is inside it."""
    try:
        relative = os.path.relpath(path)
    except ValueError:  # another drive (Windows)
        return str(path)
    return str(path) if relative.startswith("..") else relative


def is_blank(item: ast.Inline | ast.Block) -> bool:
    return isinstance(item, ast.SoftBreak) or (isinstance(item, ast.Text) and not item.text.strip())


def marked(name: str, content: Iterable[ir.Inline]) -> tuple[ir.Inline, ...]:
    """`content` inside the bookmark `name`."""
    return (ir.BookmarkStart(name), *content, ir.BookmarkEnd(name))


def bookmarked(name: str, pieces: list[Piece], fmt: Format) -> list[Piece]:
    """`pieces` inside the bookmark `name`."""
    return [
        Piece("", fmt, "mark", mark=ir.BookmarkStart(name)),
        *pieces,
        Piece("", fmt, "mark", mark=ir.BookmarkEnd(name)),
    ]


def with_paragraph_props(
    blocks: Iterable[ir.Block], props: PropertySet, *, override: bool = True
) -> list[ir.Block]:
    """`props` on every paragraph of `blocks`; with `override`, they win over the
    paragraph's own."""
    return list(
        ir.map_paragraphs(
            tuple(blocks),
            lambda p: replace(
                p, props=p.props.merged(props) if override else props.merged(p.props)
            ),
        )
    )


# --- paragraph assembly ------------------------------------------------------------------


def assemble(pieces: list[Piece], smart_quotes: bool) -> tuple[ir.Inline, ...]:
    """Smart punctuation, whitespace collapsing, and grouping into runs and hyperlinks."""
    previous = ""
    smart: list[Piece] = []
    for piece in pieces:
        if piece.kind == "text" and smart_quotes:
            piece = replace(piece, text=smarten(piece.text, previous))
        if piece.text:
            previous = piece.text[-1]
        elif piece.kind in ("page", "column"):
            previous = ""
        smart.append(piece)

    collapsed: list[Piece] = []
    after_space = True  # the start of a paragraph behaves like a space
    for piece in smart:
        if piece.kind == "text":
            text = WHITESPACE_RE.sub(" ", piece.text)
            if after_space:
                text = text.lstrip(" ")
            if text:
                after_space = text.endswith(" ")
            piece = replace(piece, text=text)
        elif piece.kind in ("tab", "line", "page", "column"):
            _trim_trailing(collapsed)
            after_space = True
        elif _takes_no_room(piece):
            pass  # a bookmark or a floating picture
        else:
            after_space = False
        collapsed.append(piece)
    _trim_trailing(collapsed)

    inlines: list[ir.Inline] = []
    link: tuple[int, str] | None = None
    link_runs: list[ir.Run] = []

    def flush_link() -> None:
        nonlocal link, link_runs
        if link is not None and link_runs:
            url = link[1]
            if url.startswith("#"):  # to a bookmark in the document
                inlines.append(ir.Hyperlink("", tuple(link_runs), anchor=url[1:]))
            else:
                inlines.append(ir.Hyperlink(url, tuple(link_runs)))
        link, link_runs = None, []

    for piece in collapsed:
        if piece.kind in ("page", "column"):
            flush_link()
            inlines.append(ir.Break(piece.kind))
            continue
        special = (
            piece.image
            or piece.field
            or piece.mark
            or piece.note
            or piece.raw
            or piece.math
            or piece.citation
            or piece.textbox
            or piece.comment
            or piece.control
        )
        if special is not None:  # not inside hyperlinks (yet)
            flush_link()
            inlines.append(special)
            continue
        if not piece.text:
            continue
        if piece.format.link != link:
            flush_link()
            link = piece.format.link
        target = link_runs if link is not None else inlines
        last = target[-1] if target else None
        if (
            type(last) is ir.Run  # not a placeholder such as a citation
            and last.props == piece.format.props
            and last.style == piece.format.style
        ):
            target[-1] = replace(last, text=last.text + piece.text)
        else:
            target.append(ir.Run(piece.text, piece.format.props, piece.format.style))  # type: ignore[arg-type]
    flush_link()
    return tuple(inlines)


def _takes_no_room(piece: Piece) -> bool:
    """Bookmarks, and pictures that float beside the text rather than in the line."""
    floating = piece.image or piece.textbox
    return piece.kind == "mark" or (floating is not None and floating.anchor is not None)


def _trim_trailing(pieces: list[Piece]) -> None:
    """Drop trailing spaces (looking through what takes no room in the line)."""
    i = len(pieces) - 1
    while i >= 0 and (pieces[i].kind == "text" or _takes_no_room(pieces[i])):
        if _takes_no_room(pieces[i]):
            i -= 1
            continue
        text = pieces[i].text.rstrip(" ")
        if text:
            pieces[i] = replace(pieces[i], text=text)
            return
        del pieces[i]
        i -= 1
