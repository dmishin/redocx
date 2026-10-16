# 5. Feature Matrix

The tier is the milestone where a feature first ships (see [roadmap](06-roadmap.md)).
**Esc** means it is reachable earlier through an escape hatch (`ooxml.*` options or `\raw`).
Difficulty: ● low · ●● medium · ●●● high. **✓** = implemented.

## 5.1 Characters (run properties, `w:rPr`)

| Feature | OOXML | Tier | Diff. |
|---------|-------|------|-------|
| Font family (ascii / hAnsi / eastAsia / cs), theme fonts | `w:rFonts` | M1 | ● |
| Size, complex-script size | `w:sz`, `w:szCs` | M1 | ● |
| Bold, italic, (cs variants) | `w:b`, `w:i` | M1 | ● |
| Underline (17 styles + color) | `w:u` | M1 | ● |
| Strike, double strike | `w:strike`, `w:dstrike` | M1 | ● |
| Caps, small caps | `w:caps`, `w:smallCaps` | M1 | ● |
| Super/subscript, raised/lowered position | `w:vertAlign`, `w:position` | M1 | ● |
| Color (rgb, theme + tint/shade) | `w:color` | M1 | ● |
| Highlight, shading | `w:highlight`, `w:shd` | M1 | ● |
| Character spacing, scale, kerning | `w:spacing`, `w:w`, `w:kern` | M2 ✓ | ● |
| Hidden, emboss, imprint, outline, shadow | `w:vanish`, … | M2 ✓ | ● |
| Language (spell-check, hyphenation) | `w:lang` | M1 | ● |
| Run border | `w:bdr` | M4 | ● |
| RTL, complex script | `w:rtl`, `w:cs` | M4 | ●● |
| East Asian layout, emphasis marks, ruby | `w:eastAsianLayout`, `w:em`, `w:ruby` | Later | ●● |
| Text effects (glow, reflection, etc. – w14) | `w14:*` | Later / Esc | ●● |

## 5.2 Paragraphs (`w:pPr`)

| Feature | Tier | Diff. |
|---------|------|-------|
| Alignment (left/center/right/justify/distribute) | M1 | ● |
| Indents (left, right, first-line, hanging; mirror) | M1 | ● |
| Spacing before/after, auto spacing, contextual spacing | M1 | ● |
| Line spacing (multiple, at-least, exact) | M1 | ● |
| Keep with next, keep lines, page break before, widow control | M1 | ● |
| Outline level | M1 | ● |
| Tab stops (alignment, leaders, clear) | M2 ✓ | ● |
| Paragraph borders (incl. between, bar), shading | M2 ✓ | ● |
| Suppress line numbers, suppress auto-hyphens | M2 ✓ | ● |
| Drop caps, text frames | M4 | ●● |
| Bidi paragraph, text direction | M4 | ●● |
| East Asian typography (kinsoku, snap to grid, …) | Later / Esc | ● |

## 5.3 Styles & themes

| Feature | Tier | Diff. |
|---------|------|-------|
| Doc defaults (rPrDefault/pPrDefault) | M1 | ● |
| Paragraph & character styles, basedOn, next | M1 ✓ | ● |
| Linked styles | M2 ✓ | ● |
| Table styles with conditional formatting (tblStylePr: firstRow, lastRow, bands, corners…) | M2 ✓ | ●●● |
| Numbering (list) styles | Later (template list styles work) | ●● |
| Latent styles, quick-style gallery flags, priorities | M2 (partly: priority and gallery flag; latent styles come from templates) | ● |
| Modify/replace template styles; localized built-in style names | M2 ✓ (`replace` acts as `modify` on template styles) | ●● |
| Theme colors/fonts references | M1 | ● |
| Custom theme (generate theme1.xml) | M4 | ●● |

## 5.4 Lists & numbering

| Feature | Tier | Diff. |
|---------|------|-------|
| Bullets and simple numbered lists | M1 ✓ | ●● |
| Multilevel definitions (format, text, indent, start, suffix, justification) | M1 ✓ | ●● |
| Restart / continue / start-at semantics | M1 ✓ | ●● |
| Heading numbering (style-linked) | M2 ✓ | ●● |
| Legal numbering, `isLgl`, restart-after-level | M2 ✓ | ●● |
| Picture bullets | Later | ●● |

## 5.5 Sections & page layout

| Feature | Tier | Diff. |
|---------|------|-------|
| Page size, orientation, margins | M1 | ● |
| Multiple sections, break types | M2 ✓ | ●● |
| Gutter, mirror margins | M2 ✓ | ● |
| Columns (equal / custom, separator) | M2 (equal widths, separator) ✓; custom widths M4 | ● |
| Headers/footers: default, first, even; link-to-previous | M2 ✓ | ●● |
| Page numbering format & restart per section | M2 ✓ | ● |
| Line numbering | M2 ✓ | ● |
| Vertical alignment | M2 ✓ | ● |
| Page borders | M4 | ● |
| Watermark (VML/DrawingML in header) | M4 | ●● |
| Page background color | M4 | ● |
| Text direction / RTL section | M4 | ●● |

## 5.6 Tables

| Feature | Tier | Diff. |
|---------|------|-------|
| Simple tables, pipe syntax | M1 ✓ | ● |
| Column widths (fixed, %, fr, auto), layout fixed/auto | M1 ✓ | ●● |
| Cell block content (multiple paragraphs, lists) | M1 ✓ | ● |
| Horizontal / vertical merges | M2 ✓ | ●● |
| Borders (table, cell, inside), shading | M2 ✓ (theme colours too) | ●● |
| Cell margins, vertical alignment, text direction | M2 ✓ | ● |
| Row height, can't-split, repeat header rows | M2 ✓ | ● |
| Table alignment, indent, look flags | M2 ✓ | ● |
| Nested tables | M2 ✓ | ● |
| Floating tables (tblpPr) | M4 | ●● |
| Diagonal cell borders | Later | ● |

## 5.7 References, fields & generated content

| Feature | Tier | Diff. |
|---------|------|-------|
| Hyperlinks (external, internal anchor) | M1 (partly: external done) | ● |
| Bookmarks | M2 ✓ | ● |
| Cross-references (REF, PAGEREF, with pre-computed cached text) | M2 ✓ | ●●● |
| Captions (SEQ, chapter numbering) | M2 ✓ | ●● |
| TOC (with pre-rendered entries), list of figures/tables | M2 ✓ | ●● |
| Simple fields (PAGE, NUMPAGES, SECTIONPAGES, DATE, TITLE, AUTHOR, STYLEREF…) | M2 ✓ | ● |
| Arbitrary / nested fields | M2 ✓ | ●● |
| Footnotes, endnotes (numbering format, restart, position) | M2 ✓ | ●● |
| Index (XE + INDEX) | M3 ✓ | ●● |
| Citations & bibliography (`\cite`, CSL rendering via citeproc) | M3 ✓ | ●● |
| Custom counters (`\seq`, SEQ fields) | M2 ✓ | ● |
| Native Word citations (sources.xml + CITATION field) | Later | ●●● |
| Document properties (core, app, custom) + DOCPROPERTY | M2 ✓ | ● |

## 5.8 Media, drawing, math, code

| Feature | Tier | Diff. |
|---------|------|-------|
| Inline images (PNG/JPEG/GIF; size, alt text) | M1 ✓ | ●● |
| SVG images (with PNG fallback, `asvg:svgBlip`) | M3 ✓ | ●● |
| Floating images (anchor, wrap modes, positioning) | M4 | ●●● |
| Cropping, rotation, borders on images | M4 | ●● |
| Text boxes, basic shapes (wps) | M4 | ●●● |
| Math: LaTeX subset → OMML (inline & display) | M3 ✓ | ●●● |
| Equation numbering & references | M3 ✓ | ●● |
| Code blocks with syntax highlighting | M3 ✓ | ●● |
| Native charts (chart parts + embedded xlsx) | Later | ●●● |
| Charts as images (matplotlib, via API) | M3 | ● |

## 5.9 Collaboration, forms, protection

| Feature | Tier | Diff. |
|---------|------|-------|
| Comments (anchored ranges, replies, resolved state) | M5 | ●● |
| Tracked insertions/deletions | Postponed (§9.1) | ●● |
| Tracked formatting changes | Postponed (§9.1) | ●●● |
| `wordgenx diff` → redline docx | Postponed (§9.1) | ●●● |
| Content controls (text, rich text, dropdown, combo, date, checkbox, picture) | M5 | ●● |
| Repeating-section content controls | M5 | ●● |
| Content-control data binding to custom XML | Later | ●●● |
| Legacy form fields | Later | ●● |
| Document protection (read-only, forms, comments, tracked changes; no password) | M2 ✓ (password: M5) | ● |

## 5.10 Document-level

| Feature | Tier | Diff. |
|---------|------|-------|
| Templates (.dotx/.docx base); writing .dotx | M2 ✓ | ●●● |
| Compatibility mode settings | M1 | ● |
| updateFields on open | M2 ✓ | ● |
| Embedded fonts | Later | ●● |
| altChunk (embed docx) | M3 ✓ | ● |
| Glossary / building blocks | Later | ●● |
| Finalize (update fields via Word / LibreOffice), PDF export | M6 | ●● |

## 5.11 Will not do

SmartArt, OLE objects, ActiveX, VBA/macros, legacy `.doc` output, ink annotations.
