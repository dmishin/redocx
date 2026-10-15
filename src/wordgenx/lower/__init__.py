"""Lowering a parsed document to the IR (design §4.1, stage 3-4)."""

from __future__ import annotations

import difflib
from dataclasses import replace
from pathlib import Path

from wordgenx import ir
from wordgenx.lower.builtins import BUILTIN_STYLES, DEFAULT_NUMBERING
from wordgenx.lower.lowering import Located, Lowering, StyleUse
from wordgenx.ooxml.parts.numbering import complete_levels
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import PropertySet, Target
from wordgenx.syntax import ParseResult
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity
from wordgenx.syntax.source import Source, Span

_BUILTIN_BY_ID = {style_id(name): name for name in BUILTIN_STYLES}


def lower(result: ParseResult, base_dir: Path | None = None) -> tuple[ir.Document, list[Located]]:
    """Lower a parsed document. Diagnostics include the parse diagnostics; the document is
    only meaningful when none of them is an error."""
    if base_dir is None:
        base_dir = Path(result.source.name).resolve().parent
    lowering = Lowering(result.source, base_dir)
    body = lowering.document(result.ast)
    compiled = lowering.stylesheets.result()
    diagnostics: list[Located] = [(result.source, d) for d in result.diagnostics]
    diagnostics += compiled.diagnostics
    diagnostics += lowering.diagnostics

    def error(
        source_use: StyleUse | tuple[str, Span, Source], message: str, label: str = ""
    ) -> None:
        if isinstance(source_use, StyleUse):
            source, span = source_use.source, source_use.span
        else:
            _, span, source = source_use
        diagnostics.append(
            (source, Diagnostic(Severity.ERROR, Code.UNDEFINED_STYLE, message, span, label))
        )  # type: ignore[arg-type]

    # Lists: default definitions for "-" and "1." lists, then styles and continuation indents.
    numbering = {n.name: n for n in compiled.numbering}
    for use in lowering.list_uses:
        name = use[0]
        if name not in numbering:
            if name in DEFAULT_NUMBERING:
                numbering[name] = DEFAULT_NUMBERING[name]()
            else:
                close = difflib.get_close_matches(name, list(numbering), n=1)
                error(
                    use,
                    f"numbering `{name}` is not defined",
                    f"did you mean `{close[0]}`?" if close else "",
                )

    style_uses = list(lowering.style_uses)
    resolved_body: list[ir.Paragraph] = []
    for paragraph in body:
        ref = paragraph.list_item
        if ref is not None and ref.numbering in numbering:
            level = complete_levels(numbering[ref.numbering])[ref.level - 1]
            style = paragraph.style or level.style or "List Paragraph"
            style_uses.append(StyleUse(style, ir.StyleKind.PARAGRAPH, Span(0, 0), result.source))
            if ref.continuation:
                indent = level.paragraph.get("indent-left")
                props = paragraph.props
                if indent is not None:
                    props = PropertySet(Target.PARAGRAPH, {"indent-left": indent}).merged(props)
                paragraph = replace(paragraph, list_item=None, style=style, props=props)
            else:
                paragraph = replace(paragraph, style=style)
        elif ref is not None:
            paragraph = replace(paragraph, list_item=None)
        resolved_body.append(paragraph)

    # Styles: defined by style sheets, else Word built-ins the markup relies on.
    styles = {style_id(s.name): s for s in compiled.styles}
    for use in style_uses:
        sid = style_id(use.name)
        style = styles.get(sid)
        if style is None and sid in _BUILTIN_BY_ID:
            style = styles[sid] = BUILTIN_STYLES[_BUILTIN_BY_ID[sid]]()
            for related in (style.based_on, style.next):
                if (
                    related is not None
                    and style_id(related) not in styles
                    and style_id(related) in _BUILTIN_BY_ID
                ):
                    styles[style_id(related)] = BUILTIN_STYLES[_BUILTIN_BY_ID[style_id(related)]]()
        if style is None:
            if sid == "Normal" and use.kind is ir.StyleKind.PARAGRAPH:
                continue
            close = difflib.get_close_matches(use.name, [s.name for s in styles.values()], n=1)
            error(use, f"{use.kind.value} style `{use.name}` is not defined",
                  f"did you mean `{close[0]}`?" if close else "")  # fmt: skip
        elif style.kind is not use.kind:
            error(use, f"`{use.name}` is a {style.kind.value} style, not a {use.kind.value} style")

    document = ir.Document(
        body=tuple(resolved_body),
        meta=lowering.meta,
        settings=lowering.settings,
        styles=tuple(styles.values()),
        numbering=tuple(numbering.values()),
        default_run=compiled.default_run,
        default_paragraph=compiled.default_paragraph,
        page=lowering.page,
    )
    return document, diagnostics


__all__ = ["Located", "lower"]
