# 9. Postponed Features

wordgenx is for generating documents and getting their look right. Features that do not serve that goal, or that
cost far more than they give, are listed here instead of on the [roadmap](06-roadmap.md). Nothing here is
scheduled. An item comes back only when a real document needs it.

## 9.1 Postponed indefinitely (decided 2026-10-16)

| Feature | What it would be | Why postponed |
|---------|------------------|---------------|
| Tracked insertions and deletions (`\ins`, `\del`) | Revisions written in the source as Word revisions (`w:ins`, `w:del`) that a reviewer accepts or rejects | Review happens in Word, not in the source. Every construct that writes runs (fields, notes, math, pictures, paragraph marks) would have to work inside a revision. |
| Tracked formatting changes | `w:rPrChange`, `w:pPrChange`: "formatted: bold" revisions | Same as above, and rarer |
| `wordgenx diff old.wgx new.wgx -o redline.docx` | Compiles two versions and writes one document whose differences are tracked changes | Needs the revisions above, plus block alignment and word-level diffs that survive moved text, table changes and renumbering. Word's own *Compare Documents* already does this. |

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
| Style sheets | The `ooxml.*` escape hatch; formatting `.wgxs` files with `wordgenx fmt` | [§3.5](03-language-design.md), [§3.21](03-language-design.md) |
| `wordgenx fmt` | Re-wrapping paragraphs; aligning pipe tables | [§3.21](03-language-design.md) |
| `--emit-ir` | Reading the JSON back (alternate frontends) | [§4.3](04-architecture.md) |
| Templates | The template's own footnote separators | [§4.6](04-architecture.md) |
| Themes | East Asian and complex-script theme fonts; effect and fill styles; loading a `.thmx` | [§3.5](03-language-design.md) |
