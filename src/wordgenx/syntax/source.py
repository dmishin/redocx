"""Source text, spans, and offset <-> line/column mapping."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path


@dataclass(frozen=True, order=True)
class Span:
    """A half-open range [start, end) of character offsets into a source text."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start <= self.end:
            raise ValueError(f"invalid span {self.start}..{self.end}")

    def __len__(self) -> int:
        return self.end - self.start

    def cover(self, other: Span) -> Span:
        return Span(min(self.start, other.start), max(self.end, other.end))


@dataclass(frozen=True)
class Position:
    line: int
    """1-based line number."""
    column: int
    """1-based column, counted in characters."""


@dataclass(frozen=True)
class Source:
    """A named source text (a file, or a string given to the API)."""

    name: str
    text: str
    _line_starts: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        starts = [0]
        starts.extend(i + 1 for i, ch in enumerate(self.text) if ch == "\n")
        object.__setattr__(self, "_line_starts", tuple(starts))

    @classmethod
    def from_path(cls, path: str | PathLike[str]) -> Source:
        path = Path(path)
        # newline="" keeps "\r\n" so offsets match the file; the parser treats "\r" as whitespace.
        with path.open(encoding="utf-8-sig", newline="") as f:
            return cls(str(path), f.read())

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def position(self, offset: int) -> Position:
        if not 0 <= offset <= len(self.text):
            raise ValueError(f"offset {offset} out of range")
        line = bisect_right(self._line_starts, offset) - 1
        return Position(line + 1, offset - self._line_starts[line] + 1)

    def line_text(self, line: int) -> str:
        """The text of a 1-based line, without its line terminator."""
        start = self._line_starts[line - 1]
        end = self._line_starts[line] - 1 if line < len(self._line_starts) else len(self.text)
        return self.text[start:end].rstrip("\r")

    def line_start(self, line: int) -> int:
        return self._line_starts[line - 1]

    def slice(self, span: Span) -> str:
        return self.text[span.start : span.end]
