"""Text: headings, lists, \\p, \\align, \\list, \\tabs, inline formatting, links, breaks
and raw XML (design §3.4, §3.6-3.7, §3.20)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from redocx import ir
from redocx.commands.bind import Args
from redocx.lower.common import (
    BlockHandler,
    Format,
    InlineHandler,
    Piece,
    marked,
    markup,
    verbatim,
    with_paragraph_props,
)
from redocx.ooxml.constants import NS
from redocx.ooxml.xml import Element, parse_fragment
from redocx.props import EMPTY_PARAGRAPH, PropertySet, Target
from redocx.syntax import ast
from redocx.syntax.diagnostics import Code
from redocx.values import InvalidValueError, Length, LineSpacing, TabStop

if TYPE_CHECKING:
    from redocx.lower.lowering import Lowering


# --- headings and lists ------------------------------------------------------------------


def heading(lw: Lowering, node: ast.Heading) -> ir.Paragraph:
    name = f"Heading {node.level}"
    lw.use_style(name, ir.StyleKind.PARAGRAPH, node.span)
    content = lw.assemble(lw.inlines(node.content, Format()))
    if node.label is not None:
        label = lw.register_label(node.label.name, node.label.span, "heading")
        content = marked(label.bookmark, content)
    return ir.Paragraph(content, name, unnumbered=not node.numbered)


@dataclass
class ListContext:
    numbering: str
    instance: int
    level: int
    ordered: bool
    named: bool
    start: int | None


@dataclass
class ListState:
    """Word list instances: each separate list counts from its start."""

    instances: int = 0
    last: dict[str, tuple[int, int | None]] = field(default_factory=lambda: {})
    """Numbering -> (its latest instance, that instance's start): what `continue` continues."""

    def new_instance(self, numbering: str, start: int | None) -> int:
        self.instances += 1
        self.last[numbering] = (self.instances, start)
        return self.instances


def list_block(lw: Lowering, block: ast.ListBlock, parent: ListContext | None) -> list[ir.Block]:
    if parent is not None and (parent.named or parent.ordered == block.ordered):
        context = replace(parent, level=parent.level + 1, ordered=block.ordered)
    else:
        name = "Numbers" if block.ordered else "Bullets"
        lw.list_uses.append((name, block.span, lw.source_of(block.span)))
        level = parent.level + 1 if parent else 1
        instance = lw.lists.new_instance(name, None)
        context = ListContext(name, instance, level, block.ordered, False, None)
    if context.level > 9:
        lw.report(Code.INVALID_ARGUMENT, "lists nest at most 9 levels deep", block.span)
        context = replace(context, level=9)
    out: list[ir.Block] = []
    for item in block.items:
        out.extend(_list_item(lw, item, context))
    return out


def _list_item(lw: Lowering, item: ast.ListItem, context: ListContext) -> list[ir.Block]:
    ref = ir.ListItemRef(context.numbering, context.instance, context.level, context.start)
    out: list[ir.Block] = []
    numbered = False
    for block in item.blocks:
        if isinstance(block, ast.ListBlock):
            if not numbered:
                out.append(ir.Paragraph(list_item=ref))
                numbered = True
            out.extend(list_block(lw, block, context))
            continue
        for lowered in lw.block(block, context):
            if not isinstance(lowered, ir.Paragraph):
                if not numbered:  # an item starting with a table still gets its number
                    out.append(ir.Paragraph(list_item=ref))
                    numbered = True
                out.append(lowered)
            elif not numbered:
                out.append(replace(lowered, list_item=ref))
                numbered = True
            elif isinstance(block, ast.Paragraph):
                out.append(replace(lowered, list_item=replace(ref, continuation=True)))
            else:
                out.append(lowered)
    if not numbered:
        out.append(ir.Paragraph(list_item=ref))
    if item.label is not None:
        label = lw.register_label(item.label.name, item.label.span, "item")
        for i, lowered in enumerate(out):
            if isinstance(lowered, ir.Paragraph) and lowered.list_item == ref:
                out[i] = replace(lowered, content=marked(label.bookmark, lowered.content))
                break
    return out


# --- block commands ------------------------------------------------------------------------


def p(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    lw.has_content = True
    style = args.text("style")
    if style is not None:
        lw.use_style(style, ir.StyleKind.PARAGRAPH, args.span_of("style"))
    content = lw.paragraph_content(command)
    if (label := args.text("id")) is not None:
        content = marked(
            lw.register_label(label, args.span_of("id"), "paragraph").bookmark, content
        )
    paragraph = ir.Paragraph(content, style, args.props or EMPTY_PARAGRAPH)
    if (drop := args.record("drop-cap")) is not None:
        split = drop_cap(paragraph, drop.number("lines", 3) or 3, drop.flag("in-margin"))
        if split is None:
            lw.report(
                Code.INVALID_ARGUMENT,
                "a drop cap needs the paragraph to start with a letter",
                args.span_of("drop-cap"),
            )
            return [paragraph]
        return list(split)
    return [paragraph]


# Word's drop caps, measured from what Word writes for an 11pt single-spaced body: the
# frame's exact line height is 311 twips per line, the letter is 2.227 x lines - 1.09
# times the body size, lowered by 6 half-points per line less 10 (all scaled by size).
_TWIPS_PER_LINE_AT_11PT = Decimal(311)
_DEFAULT_BODY_SIZE = Length.pt(11)


def size_drop_caps(
    blocks: tuple[ir.Block, ...],
    styles: dict[str, ir.Style],
    default_run: PropertySet,
    default_paragraph: PropertySet,
) -> tuple[ir.Block, ...]:
    """Size each drop cap from the body text it stands beside, as Word does."""
    by_name = {s.name: s for s in styles.values()}

    def lookup(style: str | None, target: str, key: str) -> object:
        seen: set[str] = set()
        while style is not None and style not in seen and style in by_name:
            seen.add(style)
            entry = by_name[style]
            props = entry.run if target == "run" else entry.paragraph
            if key in props:
                return props[key]
            style = entry.based_on
        defaults = default_run if target == "run" else default_paragraph
        return defaults.get(key)

    def size(paragraph: ir.Paragraph) -> ir.Paragraph:
        drop = paragraph.drop_cap
        if drop is None:
            return paragraph
        body = lookup(paragraph.style, "run", "size")
        body_size = body if isinstance(body, Length) else _DEFAULT_BODY_SIZE
        points = Decimal(body_size.emu) / 12700
        spacing = paragraph.props.get("line-spacing") or lookup(
            paragraph.style, "paragraph", "line-spacing"
        )
        line = _TWIPS_PER_LINE_AT_11PT * points / 11
        if isinstance(spacing, LineSpacing):
            if spacing.multiple is not None:
                line *= spacing.multiple
            elif spacing.height is not None:
                line = Decimal(spacing.height.in_twips)
        frame_line = Length.twips(round(line * drop.lines))
        letter = Length.pt(
            (points * (Decimal("2.227") * drop.lines - Decimal("1.09")) * 2).to_integral() / 2
        )
        lowered = Length.pt(points / 11 * (10 - 6 * drop.lines) / 2)
        run_props = {"size": letter, "position": lowered}
        content = tuple(
            replace(item, props=item.props.merged(PropertySet(Target.RUN, run_props)))
            if isinstance(item, ir.Run)
            else item
            for item in paragraph.content
        )
        exact = PropertySet(Target.PARAGRAPH, {"line-spacing": LineSpacing.exact(frame_line)})
        return replace(paragraph, content=content, props=paragraph.props.merged(exact))

    return ir.map_paragraphs(blocks, size)


def drop_cap(
    paragraph: ir.Paragraph, lines: int, in_margin: bool
) -> tuple[ir.Paragraph, ir.Paragraph] | None:
    """The paragraph's first letter as a drop-cap paragraph, and the rest. The letter is
    sized when the styles are known (`size_drop_caps`)."""
    content = list(paragraph.content)
    for i, item in enumerate(content):
        if isinstance(item, ir.BookmarkStart | ir.BookmarkEnd):
            continue
        if isinstance(item, ir.Image | ir.TextBox) and item.anchor is not None:
            continue  # floats beside the text; stays anchored in the paragraph
        if not isinstance(item, ir.Run) or not item.text.strip():
            return None
        text = item.text.lstrip()
        letter, rest = text[0], text[1:]
        content[i] = replace(item, text=rest)
        if not rest:
            del content[i]
        props = PropertySet(Target.PARAGRAPH, {"keep-with-next": True, "space-after": Length(0)})
        frame = ir.Paragraph(
            (ir.Run(letter, item.props, item.style),),
            paragraph.style,
            paragraph.props.merged(props),
            drop_cap=ir.DropCap(lines, in_margin),
        )
        return frame, replace(paragraph, content=tuple(content))
    return None


def align(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    paragraphs = lw.blocks(markup(command))
    alignment = args.text("align")
    if alignment is None:
        return paragraphs
    return with_paragraph_props(paragraphs, PropertySet(Target.PARAGRAPH, {"align": alignment}))


def numbered_list(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    """\\list[Name, continue, restart=5]{...}: a list with a named numbering definition."""
    name = args.text("numbering")
    if name is None:
        return lw.blocks(markup(command))
    lw.list_uses.append((name, command.span, lw.source_of(command.span)))
    start = args.number("restart")
    if args.flag("continue") and start is None and name in lw.lists.last:
        instance, start = lw.lists.last[name]
    else:
        instance = lw.lists.new_instance(name, start)
    context = ListContext(name, instance, 0, ordered=True, named=True, start=start)
    out: list[ir.Block] = []
    for block in markup(command):
        if isinstance(block, ast.ListBlock):
            lw.has_content = True
            out.extend(list_block(lw, block, context))
        else:
            out.extend(lw.block(block))
    return out


def tabs(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    """\\tabs[(8cm, center), (16cm, right, leader=dot)]{...}: tab stops for the paragraphs."""
    paragraphs = lw.blocks(markup(command))
    stops = args.items("stops", TabStop)
    if not stops:
        return paragraphs
    try:
        props = PropertySet(Target.PARAGRAPH, {"tabs": stops})
    except InvalidValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), args.span_of("stops"))
        return paragraphs
    return with_paragraph_props(paragraphs, props, override=False)


def block_break(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    lw.has_content = True
    return [ir.Paragraph((ir.Break("page" if command.name == "pagebreak" else "column"),))]


# --- inline formatting -----------------------------------------------------------------------

SIMPLE_FORMATS: dict[str, dict[str, object]] = {
    "b": {"bold": True},
    "i": {"italic": True},
    "s": {"strike": True},
    "sup": {"vertical-align": "superscript"},
    "sub": {"vertical-align": "subscript"},
    "hidden": {"hidden": True},
}
VALUE_FORMATS = {
    "u": ("underline", "single"),
    "hl": ("highlight", "yellow"),
    "lang": ("lang", None),
}
"""Command -> (run property, default value) for \\u[double], \\hl[green], \\lang[de-DE]."""
STYLE_COMMANDS = {"strong": "Strong", "emph": "Emphasis", "code": "Code"}


def formatting(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    """\\b, \\i, \\u, \\font, ...: the body with direct run formatting."""
    name = command.name
    props = args.props
    if name in SIMPLE_FORMATS:
        props = PropertySet(Target.RUN, SIMPLE_FORMATS[name])
    elif name in VALUE_FORMATS:
        key, default = VALUE_FORMATS[name]
        value = args.values.get(key, default)
        props = PropertySet(Target.RUN, {key: value}) if value is not None else None
    if props is not None:
        fmt = replace(fmt, props=fmt.props.merged(props))
    return lw.body_inlines(command, fmt)


def styled(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    """\\strong, \\emph, \\code and \\style[Name]: the body in a character style."""
    style = STYLE_COMMANDS.get(command.name) or args.text("style")
    if style is not None:
        fmt = lw.with_style(fmt, style, args.span_of("style"))
    return lw.body_inlines(command, fmt)


def link(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    url = args.text("to")
    if url is not None:
        linked = lw.with_link(fmt, url, command.span)
        style = args.text("style")
        if style == "none":  # a link that looks like the text around it
            linked = replace(linked, style=fmt.style)
        elif style is not None:
            linked = lw.with_style(replace(linked, style=fmt.style), style, args.span_of("style"))
        fmt = linked
    return lw.body_inlines(command, fmt)


def url(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    address = verbatim(command)
    if not address:
        return []
    shown = address.removeprefix("mailto:")
    return [Piece(shown, lw.with_link(fmt, address, command.span), "code")]


BREAKS = {"br": ("\n", "line"), "tab": ("\t", "tab"), "pagebreak": ("", "page")}


def inline_break(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    text, kind = BREAKS.get(command.name, ("", "column"))
    return [Piece(text, fmt, kind)]


# --- raw XML -------------------------------------------------------------------------------

_BLOCK_ELEMENTS = frozenset({"p", "tbl"})


def raw_xml(lw: Lowering, command: ast.Command) -> str | None:
    """The body of \\raw[ooxml]{...}, checked: well-formed w: elements, all run-level or
    all block-level (w:p, w:tbl)."""
    if not isinstance(command.body, ast.VerbatimBody) or not command.body.text.strip():
        return None
    try:
        elements = parse_fragment(command.body.text)
    except ValueError as e:
        lw.report(Code.INVALID_ARGUMENT, str(e), command.body.span)
        return None
    kinds = {_element_kind(e) for e in elements}
    if not elements or len(kinds) > 1:
        lw.report(
            Code.INVALID_ARGUMENT,
            "\\raw holds either run-level (w:r, ...) or block-level (w:p, w:tbl) elements",
            command.body.span,
        )
        return None
    from redocx.ooxml.validate import fragment_problems  # loads the schemas: only when used

    problems = fragment_problems(command.body.text, "block" if "block" in kinds else "inline")
    if problems:
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        lw.report(
            Code.INVALID_ARGUMENT,
            f"raw XML that Word would reject: {problems[0]}{more}",
            command.body.span,
            help="\\raw XML is checked against the ECMA-376 schemas; "
            "elements of other namespaces (w14:, ...) are not checked",
        )
        return None
    return command.body.text.strip()


def raw_block(lw: Lowering, command: ast.Command, args: Args) -> list[ir.Block]:
    xml = raw_xml(lw, command)
    if xml is None:
        return []
    lw.has_content = True
    ends = parse_fragment(xml)[-1].tag == f"{{{NS.W}}}tbl"
    return [ir.RawBlock(xml, ends_with_table=ends)]


def raw_inline(lw: Lowering, command: ast.Command, args: Args, fmt: Format) -> list[Piece]:
    xml = raw_xml(lw, command)
    return [Piece("", fmt, "field", raw=ir.RawXml(xml))] if xml is not None else []


def raw_kind(command: ast.Command) -> str:
    """block or inline: where a \\raw command goes (inline if its XML is broken; the error is
    reported when it is lowered)."""
    if not isinstance(command.body, ast.VerbatimBody):
        return "inline"
    try:
        elements = parse_fragment(command.body.text)
    except ValueError:
        return "inline"
    return "block" if elements and _element_kind(elements[0]) == "block" else "inline"


def _element_kind(element: Element) -> str:
    local = str(element.tag).rsplit("}", 1)[-1]
    if local == "sdt":  # a content control: block-level when it holds paragraphs or tables
        content = element.find(f"{{{NS.W}}}sdtContent")
        inner: set[str] = (
            {str(c.tag).rsplit("}", 1)[-1] for c in content} if content is not None else set()
        )
        return "block" if inner & _BLOCK_ELEMENTS else "inline"
    return "block" if local in _BLOCK_ELEMENTS else "inline"


BLOCKS: dict[str, BlockHandler] = {
    "p": p,
    "align": align,
    "list": numbered_list,
    "tabs": tabs,
    "pagebreak": block_break,
    "columnbreak": block_break,
    "raw": raw_block,
}
INLINES: dict[str, InlineHandler] = {
    **dict.fromkeys([*SIMPLE_FORMATS, *VALUE_FORMATS, "font"], formatting),
    **dict.fromkeys([*STYLE_COMMANDS, "style"], styled),
    "link": link,
    "url": url,
    **dict.fromkeys(["br", "tab", "pagebreak", "columnbreak"], inline_break),
    "raw": raw_inline,
}
