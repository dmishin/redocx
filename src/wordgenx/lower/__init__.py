"""Lowering a parsed document to the IR (design §4.1, stage 3-4)."""

from __future__ import annotations

import difflib
from dataclasses import replace
from pathlib import Path

from wordgenx import ir
from wordgenx.lower.builtins import BUILTIN_STYLES, DEFAULT_NUMBERING
from wordgenx.lower.lowering import Located, Lowering, StyleUse
from wordgenx.lower.references import report_to, resolve
from wordgenx.ooxml.parts.numbering import complete_levels
from wordgenx.ooxml.style_names import style_id
from wordgenx.props import PropertySet, Target
from wordgenx.syntax import ParseResult
from wordgenx.syntax.diagnostics import Code, Diagnostic, Severity
from wordgenx.syntax.source import Source, Span
from wordgenx.values import Length, TabStop

_BUILTIN_BY_ID = {style_id(name): name for name in BUILTIN_STYLES}


def lower(
    result: ParseResult, base_dir: Path | None = None, template_path: Path | None = None
) -> tuple[ir.Document, list[Located]]:
    """Lower a parsed document. Diagnostics include the parse diagnostics; the document is
    only meaningful when none of them is an error. `template_path` (a .dotx or .docx) takes
    precedence over a \\document[template=...] option."""
    if base_dir is None:
        base_dir = Path(result.source.name).resolve().parent
    lowering = Lowering(result.source, base_dir)
    if template_path is not None:
        lowering.use_template(template_path, Span(0, 0), from_command_line=True)
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
    # A list definition only the template has is used as it is (the writer adds instances).
    template = lowering.template
    numbering = {n.name: n for n in compiled.numbering}
    template_lists: dict[str, ir.Numbering] = {}
    for use in lowering.list_uses:
        name = use[0]
        if name not in numbering and name not in template_lists:
            if template is not None and name in template.lists:
                template_lists[name] = template.lists[name][1]
            elif name in DEFAULT_NUMBERING:
                numbering[name] = DEFAULT_NUMBERING[name]()
            else:
                known = [*numbering, *(template.lists if template is not None else ())]
                close = difflib.get_close_matches(name, known, n=1)
                error(
                    use,
                    f"numbering `{name}` is not defined",
                    f"did you mean `{close[0]}`?" if close else "",
                )

    style_uses = list(lowering.style_uses)

    def resolve_list_item(paragraph: ir.Paragraph) -> ir.Paragraph:
        ref = paragraph.list_item
        if ref is None:
            return paragraph
        definition = numbering.get(ref.numbering) or template_lists.get(ref.numbering)
        if definition is None:
            return replace(paragraph, list_item=None)
        level = complete_levels(definition)[ref.level - 1]
        style = paragraph.style or level.style or "List Paragraph"
        style_uses.append(StyleUse(style, ir.StyleKind.PARAGRAPH, Span(0, 0), result.source))
        if not ref.continuation:
            return replace(paragraph, style=style)
        indent = level.paragraph.get("indent-left")
        props = paragraph.props
        if indent is not None:
            props = PropertySet(Target.PARAGRAPH, {"indent-left": indent}).merged(props)
        return replace(paragraph, list_item=None, style=style, props=props)

    resolved_body = ir.map_paragraphs(tuple(body), resolve_list_item)
    resolved_section = ir.map_section(lowering.section, resolve_list_item)
    first_page = next(
        (b.section.page for b in body if isinstance(b, ir.SectionBreak)), lowering.section.page
    )

    # Styles: defined by style sheets, else the template's, else Word built-ins the markup
    # relies on. A built-in the template has is the template's (unchanged built-ins that
    # style sheets brought along are dropped).
    def in_template(name: str) -> bool:
        return template is not None and template.style(name) is not None

    styles = {
        style_id(s.name): s
        for s in compiled.styles
        if not (in_template(s.name) and s.name in BUILTIN_STYLES and s == BUILTIN_STYLES[s.name]())
    }
    for use in style_uses:
        sid = style_id(use.name)
        style = styles.get(sid)
        if style is None and template is not None and (found := template.style(use.name)):
            if found.kind != use.kind.value:
                error(use, f"`{use.name}` is a {found.kind} style in the template, not a "
                      f"{use.kind.value} style")  # fmt: skip
            continue
        if style is None and sid in _BUILTIN_BY_ID:
            style = styles[sid] = _builtin(_BUILTIN_BY_ID[sid], first_page)
            for related in (style.based_on, style.next):
                if (
                    related is not None
                    and style_id(related) not in styles
                    and style_id(related) in _BUILTIN_BY_ID
                    and not in_template(related)
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

    # Built-ins that style sheets refer to (`table X : "Table Grid"`, `style: "Heading 1"`).
    added = True
    while added:
        added = False
        referenced = [r for s in styles.values() for r in (s.based_on, s.next, s.link)]
        referenced += [lvl.style for n in numbering.values() for lvl in n.levels]
        for name in referenced:
            if (
                name is not None
                and style_id(name) not in styles
                and style_id(name) in _BUILTIN_BY_ID
                and not in_template(name)
            ):
                styles[style_id(name)] = _builtin(_BUILTIN_BY_ID[style_id(name)], first_page)
                added = True

    # Cross-references, counters and tables of contents, now that styles and numbering are
    # final. The template's definitions count too (its headings are numbered by its lists).
    known_styles, known_numbering = dict(styles), dict(numbering)
    if template is not None:
        known_styles = {style_id(s.name): s for s in template.definitions.styles} | styles
        known_numbering = {n.name: n for n in template.definitions.numbering} | numbering
    resolved_body, toc_styles, needs_update = resolve(
        resolved_body,
        known_styles,
        known_numbering,
        lowering.settings,
        lowering.references,
        report_to(diagnostics),
        first_page.text_width,
    )
    for name in sorted(toc_styles):
        if style_id(name) not in styles and not in_template(name):
            styles[style_id(name)] = _builtin(name, first_page)
    settings = lowering.settings
    if lowering.even_headers:
        settings = replace(settings, even_and_odd_headers=True)
    if needs_update:
        settings = replace(settings, update_fields_on_open=True)

    document = ir.Document(
        body=tuple(resolved_body),
        meta=lowering.meta,
        settings=settings,
        styles=tuple(styles.values()),
        numbering=tuple(numbering.values()),
        default_run=compiled.default_run,
        default_paragraph=compiled.default_paragraph,
        section=resolved_section,
        template=template,
    )
    return document, diagnostics


def _builtin(name: str, page: ir.PageSetup) -> ir.Style:
    """A built-in style; Header and Footer get centre and right tab stops for the page, as
    Word's own Header and Footer styles have, and TOC entries a dotted right tab stop."""
    style = BUILTIN_STYLES[name]()
    width = page.text_width
    tabs: tuple[TabStop, ...] = ()
    if name in ("Header", "Footer"):
        tabs = (TabStop(Length(width.emu // 2), "center"), TabStop(width, "right"))
    elif name.startswith("TOC ") or name == "table of figures":
        tabs = (TabStop(width, "right", "dot"),)  # page numbers at the right margin
    if tabs:
        tab_props = PropertySet(Target.PARAGRAPH, {"tabs": tabs})
        style = replace(style, paragraph=style.paragraph.merged(tab_props))
    return style


__all__ = ["Located", "lower"]
