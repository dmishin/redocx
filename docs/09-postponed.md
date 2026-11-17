# 9. Postponed Features

redocx is for generating documents and getting their look right. Features that do not serve that goal, or that
cost far more than they give, are listed here instead of on the [roadmap](06-roadmap.md). Nothing here is
scheduled. An item comes back only when a real document needs it.

## 9.1 Postponed indefinitely (decided 2026-10-16)

| Feature | What it would be | Why postponed |
|---------|------------------|---------------|
| Tracked insertions and deletions (`\ins`, `\del`) | Revisions written in the source as Word revisions (`w:ins`, `w:del`) that a reviewer accepts or rejects | Review happens in Word, not in the source. Every construct that writes runs (fields, notes, math, pictures, paragraph marks) would have to work inside a revision. |
| Tracked formatting changes | `w:rPrChange`, `w:pPrChange`: "formatted: bold" revisions | Same as above, and rarer |
| `redocx diff old.rdx new.rdx -o redline.docx` | Compiles two versions and writes one document whose differences are tracked changes | Needs the revisions above, plus block alignment and word-level diffs that survive moved text, table changes and renumbering. Word's own *Compare Documents* already does this. |

The commands `\ins` and `\del` still parse. They warn that they are postponed and keep their text (`\ins`) or
drop it (`\del`). Turning on Word's Track Changes for edits made later in Word (`\settings[track-changes]`) is kept
on the roadmap: it is one setting, not a revision model.

## 9.2 Known gaps in finished features

These are the parts of finished features that were left out to keep them simple. Each is described where the
feature is documented. They are smaller than 9.1 and can be picked up whenever a document needs one.

| Feature | Not done | Where |
|---------|----------|-------|
| Citations | Collapsing ("Smith, 2020a, b"; "[4]–[6]") and name-based disambiguation | [§3.19](03-language-design.md) |
| Index | Page ranges (`XE \r`) and sort keys that differ from the term | [§3.12](03-language-design.md) |
| SVG images | Rasterising the fallback picture: without `fallback=` it is a grey placeholder | [§3.16](03-language-design.md) |
| `\embed-docx` | Looking inside the embedded file: its headings join the contents only after Word updates the field, and `@label` cannot point into it | [§3.16](03-language-design.md) |
| Cross-references | `form=relative` in full-context form; the "Table Table 3" lint | [§3.11](03-language-design.md) |
| Style sheets | The `ooxml.*` escape hatch; formatting `.rdxs` files with `redocx fmt` | [§3.5](03-language-design.md), [§3.21](03-language-design.md) |
| `redocx fmt` | Re-wrapping paragraphs; aligning pipe tables | [§3.21](03-language-design.md) |
| `--emit-ir` | Reading the JSON back (alternate frontends) | [§4.3](04-architecture.md) |
| Templates | The template's own footnote separators | [§4.6](04-architecture.md) |
| Themes | East Asian and complex-script theme fonts; effect and fill styles; loading a `.thmx` | [§3.5](03-language-design.md) |
| Text boxes | A VML fallback for Word 2007; linked text boxes (text flowing from one to the next) | [§3.16](03-language-design.md) |
| Watermarks | Keeping a template's own headers in the first section when it has a watermark | [§3.16](03-language-design.md) |
| Frames | General text frames (`w:framePr` other than drop caps): text boxes do their job | [§3.6](03-language-design.md) |
| Comments | Replies and the resolved state (Word 2013's `commentsExtended.xml`); comments in headers, notes and text boxes | [§3.17](03-language-design.md) |
| Content controls | Repeating sections (Word 2013's `w15:repeatingSection`); rich-text controls around whole paragraphs (block-level `w:sdt`); placeholders from building blocks | [§3.17](03-language-design.md) |
| Import (reader) | Equations (OMML) are dropped; shapes without text; tracked changes are read as accepted (deletions dropped); emphasis marks; Word 2010 text effects (glow, shadow, 3-D, ligatures); embedded and linked objects keep only their picture; content controls are read as their text | [M6](06-roadmap.md) |
| Import (printed source) | Repeated spaces collapse to one; a comment on text across paragraphs becomes a point comment; first-page and even-page headers that Word does not show; a list that skips a level, or resumes an earlier list; turning a column separator off in a later section; formatting a style sheet cannot route back (run shading in a paragraph style, cell borders at the top level of a table style) | [M6](06-roadmap.md) |
| Import (style sheet) | Table styles and list levels are printed in full, not as differences from redocx's; the theme's effects and East Asian or complex-script fonts (`--keep-styles` keeps them) | [M6](06-roadmap.md) |
| Import (sugar) | `-` and `1.` lists outside `\list[Name]` (they need the Bullets and Numbers definitions); labels on other paragraphs, list items and numbered paragraphs (REF `\w`, `\r`); captions that are not `Kind {SEQ Kind}: text` in the Caption style; leaving out direct formatting in tables; `\ix` from XE fields; code spans | [M6](06-roadmap.md) |
| Right to left | Separate complex-script values for bold, italic and size (they follow the Latin ones); `start`/`end` alignment | [§3.4](03-language-design.md) |
