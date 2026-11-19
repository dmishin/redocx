"""An invoice generated from data: the source is written with f-strings and `redocx.escape`,
then compiled in-process. Run it with `uv run python examples/generated/invoice.py`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from redocx import escape
from redocx.compiler import build


@dataclass(frozen=True)
class Line:
    item: str
    quantity: int
    price: Decimal


@dataclass(frozen=True)
class Invoice:
    number: str
    customer: str
    address: str
    lines: tuple[Line, ...]
    note: str = ""


def source(invoice: Invoice) -> str:
    """The invoice as redocx source. Every value from the data goes through `escape`."""
    total = sum((line.price * line.quantity for line in invoice.lines), Decimal(0))
    rows = "\n".join(
        f"  | {escape.inline(line.item)} | {line.quantity} | {line.price:.2f} "
        f"| {line.price * line.quantity:.2f} |"
        for line in invoice.lines
    )
    note = f"\n{escape.text(invoice.note)}\n" if invoice.note else ""
    return f"""\
\\document[lang=en-GB]
\\meta[title={escape.value("Invoice " + invoice.number)}, author="Example Ltd"]
\\section[size=A4, margins=2cm]

\\p[style=Title]{{Invoice {escape.text(invoice.number)}}}

\\b{{{escape.text(invoice.customer)}}}\\br
{escape.text(invoice.address).replace(chr(10), "\\br" + chr(10))}

\\table[header-rows=1, widths=(1fr, 2cm, 3cm, 3cm),
       columns=(left, right, decimal, decimal)]{{
  | Item | Qty | Price | Amount |
{rows}
  | \\b{{Total}} | | | \\b{{{total:.2f}}} |
}}
{note}"""


def render(invoice: Invoice, base_dir: Path) -> bytes:
    """The invoice as .docx bytes; raises with the diagnostics if the source does not compile."""
    data, result = build(source(invoice), base_dir)
    if data is None or result.diagnostics:
        raise ValueError(result.render_diagnostics())
    return data


SAMPLE = Invoice(
    number="2026-117",
    customer="Smith & Sons {Holdings}",
    address="12 High St.\n1. floor, #3\nLeeds",
    lines=(
        Line("Widgets *large* | blue", 4, Decimal("12.50")),
        Line("Support @ 50% off", 1, Decimal("80.00")),
    ),
    note="- Payment due in 30 days.\n= Thank you!",
)

if __name__ == "__main__":
    here = Path(__file__).parent
    target = here / "invoice.docx"
    target.write_bytes(render(SAMPLE, here))
    print(f"wrote {target}")
