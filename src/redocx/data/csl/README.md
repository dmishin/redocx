# Bundled CSL styles

These citation styles come unchanged from the Citation Style Language styles repository,
<https://github.com/citation-style-language/styles>, at commit
`6ba2e9c6aa6e0dab87d3a20b833ed8ff6500f698`.

| File | Style | `\bibliography[style=...]` |
| --- | --- | --- |
| `apa.csl` | APA Style 7th edition | `apa` |
| `ieee.csl` | IEEE Reference Guide version 11.29.2023 | `ieee` |
| `chicago-author-date.csl` | Chicago Manual of Style 18th edition (author-date) | `chicago`, `chicago-author-date` |

Each file names its authors and contributors in its `<info>` element.

## Licence

The styles are licensed under the Creative Commons Attribution-ShareAlike 3.0 Unported
licence, <https://creativecommons.org/licenses/by-sa/3.0/>, as each file's `<rights>`
element says. That licence covers these three files only; the rest of redocx is under
Apache-2.0.

## Updating

Download the files from a newer commit, update the commit above, and run
`tests/test_bibliography_styles.py`. It renders a fixed set of entries in every style,
including the cases citeproc-py gets wrong without redocx's workarounds (see
`redocx/bibliography.py`). If a style stops rendering correctly, keep the older file.
