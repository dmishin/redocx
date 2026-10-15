# 8. Open Questions

Decisions needed before or during M0. Each has a proposed default.

| # | Question | Options | Proposed |
|---|----------|---------|----------|
| Q1 | Implementation language | Python / C# (.NET Open XML SDK) / others | **Python** (see [ADR-0001](02-adr-implementation-language.md)); C# is the runner-up |
| Q2 | Project name | WEX (working name), … | Check PyPI, crates and GitHub for availability before the first release. "wex" is probably taken on PyPI. |
| Q3 | License | MIT / Apache-2.0 / MPL-2.0 | **Apache-2.0** (patent grant matters in an Office-format space) |
| Q4 | Command sigil | `\cmd` (TeX-like) / `#cmd` (Typst-like) / `@cmd` | **`\cmd`**: familiar from TeX, and `#` and `@` are frequent in prose (`#hashtags`, emails) |
| Q5 | Line comment syntax | `%` (TeX) / `%%` / `//` | **`%%`**. Plain `%` is too common in business text, and `//` collides with URLs. |
| Q6 | Emphasis sugar | `*strong*` `_emph_` (Typst) / `**strong**` `*emph*` (Markdown) | **Typst-style** (single characters, no ambiguity with nesting) |
| Q7 | Scripting | none / reserved `#{…}` expression language later / embedded Python | **None in v1, reserve `#{…}`**. The Python API covers programmatic needs. Embedded Python would make compiling untrusted documents unsafe. |
| Q8 | Default for semantic sugar | Character styles (`Strong`, `Emphasis`) vs direct bold and italic | **Character styles**, which give idiomatic Word output |
| Q9 | Equation numbering layout | 3-column borderless table / OMML `#` alignment trick / tab stops | Decide after the M0 reference-XML survey (find out what Word does natively today) |
| Q10 | OOXML flavour | Transitional / Strict | **Transitional** (what Word writes by default and what every consumer reads) |
| Q11 | Minimum Word target | Word 2016+ / 2019+ / 365 | **Word 2016+** compatibility mode (`compatibilityMode=15`), use w14/w15 extensions with `mc:Ignorable` |
| Q12 | Indentation significance | Lists nest by indentation (Markdown-like) vs explicit only | **Indentation for list sugar only**. Everything else uses braces. |
| Q13 | Repository hosting & governance | GitHub org vs personal; CLA vs DCO | GitHub; **DCO** (lighter than a CLA) |
