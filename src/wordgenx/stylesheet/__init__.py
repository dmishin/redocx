"""Style sheets: parse .wgxs text and compile it to IR styles and numbering."""

from wordgenx.stylesheet.compiler import CompiledStyles, StyleSheetCompiler, compile_stylesheets
from wordgenx.stylesheet.syntax import StyleSheet, parse_stylesheet

__all__ = [
    "CompiledStyles",
    "StyleSheet",
    "StyleSheetCompiler",
    "compile_stylesheets",
    "parse_stylesheet",
]
