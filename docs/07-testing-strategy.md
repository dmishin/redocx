# 7. Testing Strategy

The main risk is producing files that are *valid XML but wrong in Word*. This shows up as a repair prompt, a silently ignored
property, or a layout different from what was intended. So testing has several layers.

| Layer | What | Runs |
|-------|------|------|
| **Unit** | Parser (incl. property-based tests with Hypothesis: parse ∘ print = id), unit conversion, option validation, counters, LaTeX→OMML | every commit |
| **Golden snapshots** | `.wgx` → canonicalized XML of each part (sorted attributes, normalized whitespace), diffed against committed snapshots | every commit |
| **Schema validation** | Every test output validated against ECMA-376 Transitional XSDs (lxml) **and** OpenXmlValidator (.NET, Office 2019+ file format) | every commit |
| **Structural read-back** | Open output with python-docx and assert high-level facts (style of paragraph N, table cell count) | every commit |
| **Rendering smoke** | LibreOffice headless → PDF → PNG; check it renders without crash; optional perceptual diff against baselines | nightly |
| **Word fidelity** | Windows runner (self-hosted or manual) opens each gallery doc in Word through COM: check no repair prompt, update fields, export PDF, compare | per milestone / release |
| **Reference XML** | For every feature: create it in Word, save, store unzipped XML in `tests/reference/feature-x/`. Our emitter is compared structurally against Word's output | when a feature is implemented |

## Gallery documents

`tests/features/*.wgx` holds one document per feature area. Each shows all variants side by side with expected results described
in the text. These documents are for humans reviewing in Word, and they are the release checklist.

## Determinism

The test suite builds every document twice and asserts the outputs are byte-identical.

## Error messages

Diagnostics have their own snapshot tests. Each error code has at least one test and a docs entry.
