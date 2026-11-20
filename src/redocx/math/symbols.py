"""The LaTeX commands the math subset knows, and what each becomes (design §3.14)."""

from __future__ import annotations

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ",
    "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "varkappa": "ϰ",
    "varGamma": "Γ", "varDelta": "Δ", "varTheta": "Θ", "varLambda": "Λ", "varXi": "Ξ",
    "varPi": "Π", "varSigma": "Σ", "varUpsilon": "Υ", "varPhi": "Φ", "varPsi": "Ψ",
    "varOmega": "Ω",
}  # fmt: skip
"""Italic Greek letters, like Latin letters: lower case, and the \\var capitals."""

UPPER_GREEK = {
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}  # fmt: skip
"""Upper-case Greek letters (upright, as in LaTeX)."""

SYMBOLS = {
    # binary operators
    "pm": "±", "mp": "∓", "times": "×", "div": "÷", "cdot": "⋅", "ast": "∗", "star": "⋆",
    "circ": "∘", "bullet": "∙", "oplus": "⊕", "ominus": "⊖", "otimes": "⊗", "odot": "⊙",
    "cup": "∪", "cap": "∩", "setminus": "∖", "wedge": "∧", "land": "∧", "vee": "∨", "lor": "∨",
    "neg": "¬", "lnot": "¬", "dagger": "†", "ddagger": "‡", "amalg": "⨿", "wr": "≀",
    "sqcup": "⊔", "sqcap": "⊓", "uplus": "⊎", "oslash": "⊘", "boxplus": "⊞", "boxminus": "⊟",
    "boxtimes": "⊠", "boxdot": "⊡", "intercal": "⊺", "smallsetminus": "∖",
    "dag": "†", "ddag": "‡", "ltimes": "⋉", "rtimes": "⋊",
    # relations
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝", "ll": "≪", "gg": "≫",
    "prec": "≺", "succ": "≻", "preceq": "⪯", "succeq": "⪰", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "in": "∈", "notin": "∉", "ni": "∋", "perp": "⊥",
    "parallel": "∥", "mid": "∣", "vdash": "⊢", "models": "⊨", "doteq": "≐", "asymp": "≍",
    "leqslant": "⩽", "geqslant": "⩾", "lesssim": "≲", "gtrsim": "≳", "coloneqq": "≔",
    "eqqcolon": "≕", "sqsubset": "⊏", "sqsupset": "⊐", "sqsubseteq": "⊑", "sqsupseteq": "⊒",
    "dashv": "⊣", "lessapprox": "⪅", "gtrapprox": "⪆", "nleq": "≰", "ngeq": "≱", "nless": "≮",
    "ngtr": "≯", "nsim": "≁", "ncong": "≇", "nmid": "∤", "nparallel": "∦", "nsubseteq": "⊈",
    "nsupseteq": "⊉", "Vdash": "⊩", "Vvdash": "⊪", "nprec": "⊀", "nsucc": "⊁",
    "preccurlyeq": "≼", "succcurlyeq": "≽", "Coloneqq": "⩴", "vartriangleleft": "⊲",
    "vartriangleright": "⊳", "trianglelefteq": "⊴", "trianglerighteq": "⊵",
    "vDash": "⊨", "triangleq": "≜", "dblcolon": "∷", "nvdash": "⊬", "nvDash": "⊭", "approxeq": "≊",
    "backsim": "∽", "backsimeq": "⋍", "bowtie": "⋈", "Join": "⨝", "eqsim": "≂",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⟹", "impliedby": "⟸",
    "iff": "⟺", "mapsto": "↦", "uparrow": "↑", "downarrow": "↓", "updownarrow": "↕",
    "longrightarrow": "⟶", "longleftarrow": "⟵", "longleftrightarrow": "⟷", "longmapsto": "⟼",
    "hookrightarrow": "↪", "hookleftarrow": "↩", "nearrow": "↗", "searrow": "↘",
    "rightleftharpoons": "⇌", "leftrightharpoons": "⇋", "Uparrow": "⇑", "Downarrow": "⇓",
    "Updownarrow": "⇕", "nrightarrow": "↛", "nleftarrow": "↚", "nRightarrow": "⇏",
    "nLeftarrow": "⇍", "nLeftrightarrow": "⇎", "nleftrightarrow": "↮", "leadsto": "⇝",
    "swarrow": "↙", "nwarrow": "↖", "rightsquigarrow": "⇝", "twoheadrightarrow": "↠",
    "twoheadleftarrow": "↞", "Longrightarrow": "⟹", "Longleftarrow": "⟸", "Longleftrightarrow": "⟺",
    "rightarrowtail": "↣", "leftarrowtail": "↢", "curvearrowright": "↷", "curvearrowleft": "↶",
    "circlearrowleft": "↺", "circlearrowright": "↻",
    # miscellaneous
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃", "nexists": "∄",
    "emptyset": "∅", "varnothing": "∅", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "hbar": "ℏ",
    "ell": "ℓ", "wp": "℘", "angle": "∠", "triangle": "△", "prime": "′", "ldots": "…",
    "cdots": "⋯", "vdots": "⋮", "ddots": "⋱", "dots": "…", "top": "⊤", "bot": "⊥",
    "square": "□", "Box": "□", "checkmark": "✓", "degree": "°", "therefore": "∴",
    "because": "∵", "colon": ":", "imath": "ı", "jmath": "ȷ", "hslash": "ℏ", "Bbbk": "𝕜",
    "measuredangle": "∡", "sphericalangle": "∢", "blacksquare": "■", "maltese": "✠",
    "clubsuit": "♣", "diamondsuit": "♢", "heartsuit": "♡", "spadesuit": "♠", "backprime": "‵",
    "dotsb": "⋯", "dotsm": "⋯", "dotsi": "⋯", "dotsc": "…", "dotso": "…",
    "ulcorner": "⌜", "urcorner": "⌝", "llcorner": "⌞", "lrcorner": "⌟",
    "digamma": "ϝ", "beth": "ℶ", "gimel": "ℷ", "daleth": "ℸ", "Diamond": "◇",
    "circledR": "®", "flat": "♭", "natural": "♮", "sharp": "♯", "surd": "√",
    "circledast": "⊛", "circledcirc": "⊚", "circleddash": "⊝", "lgroup": "⟮", "rgroup": "⟯",
    "lozenge": "◊", "blacklozenge": "⧫", "yen": "¥", "S": "§", "P": "¶",
    "divideontimes": "⋇", "lmoustache": "⎰", "rmoustache": "⎱",
    "bigstar": "★", "complement": "∁",
    "blacktriangle": "▲", "blacktriangledown": "▼", "eth": "ð", "copyright": "©",
    "triangledown": "▽", "mho": "℧", "pounds": "£",
    # delimiters as symbols
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "lvert": "|", "rvert": "|", "vert": "|", "lVert": "‖", "rVert": "‖", "Vert": "‖",
    "lbrace": "{", "rbrace": "}", "backslash": "\\",
}  # fmt: skip

ESCAPED = {"{": "{", "}": "}", "|": "‖", "%": "%", "$": "$", "#": "#", "&": "&", "_": "_"}
"""\\{ \\} \\| \\% ... : the character itself (\\| is the double bar)."""

SPACES = {",": " ", ":": " ", ">": " ", ";": " ", " ": " ",
          "quad": " ", "qquad": "  ", "!": "",
          "thinspace": " ", "medspace": " ", "thickspace": " ",
          "enspace": " ", "negthinspace": "", "negmedspace": "",
          "negthickspace": ""}  # fmt: skip
"""Spacing commands: thin, medium, thick, en, quad; negative spaces are dropped."""

NARY = {
    "sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "iiint": "∭",
    "oint": "∮", "bigcup": "⋃", "bigcap": "⋂", "bigvee": "⋁", "bigwedge": "⋀",
    "bigoplus": "⨁", "bigotimes": "⨂", "bigodot": "⨀", "biguplus": "⨄", "bigsqcup": "⨆",
    "iiiint": "⨌", "oiint": "∯", "oiiint": "∰", "ointctrclockwise": "∳",
    "varointclockwise": "∲", "sqint": "⨖", "fint": "⨏",
}  # fmt: skip
INTEGRALS = frozenset(
    "int iint iiint iiiint oint oiint oiiint ointctrclockwise varointclockwise sqint fint".split()
)
"""Big operators whose limits go beside them (subSup); the others go above and below."""

FUNCTIONS = frozenset(
    "sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth log ln lg exp det dim "
    "ker gcd lcm arg deg hom Pr max min sup inf lim liminf limsup".split()
)
LIMIT_FUNCTIONS = frozenset(
    {"lim", "liminf", "limsup", "max", "min", "sup", "inf", "det", "gcd", "Pr"}
)
"""Functions whose subscript goes underneath (\\lim_{x\\to 0})."""

ACCENTS = {
    "hat": "̂", "widehat": "̂", "bar": "̅", "tilde": "̃", "widetilde": "̃",
    "vec": "⃗", "dot": "̇", "ddot": "̈", "dddot": "⃛", "check": "̌",
    "breve": "̆", "acute": "́", "grave": "̀", "mathring": "̊", "ddddot": "⃜",
}  # fmt: skip

DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "|": "|", ".": "", "/": "/", "<": "⟨", ">": "⟩",
    "\\{": "{", "\\}": "}", "\\|": "‖", "\\langle": "⟨", "\\rangle": "⟩", "\\lfloor": "⌊",
    "\\rfloor": "⌋", "\\lceil": "⌈", "\\rceil": "⌉", "\\vert": "|", "\\Vert": "‖",
    "\\lvert": "|", "\\rvert": "|", "\\lVert": "‖", "\\rVert": "‖", "\\lbrace": "{",
    "\\rbrace": "}", "\\uparrow": "↑", "\\downarrow": "↓",
    "\\updownarrow": "↕", "\\Uparrow": "⇑", "\\Downarrow": "⇓", "\\Updownarrow": "⇕",
    "\\backslash": "\\", "\\lgroup": "⟮", "\\rgroup": "⟯",
    "\\lmoustache": "⎰", "\\rmoustache": "⎱",
}  # fmt: skip
"""What may follow \\left, \\right and \\big...; `.` is no delimiter."""

SIZED = frozenset(
    "big Big bigg Bigg bigl Bigl biggl Biggl bigr Bigr biggr Biggr bigm Bigm biggm Biggm".split()
)
"""\\big( and friends: Word sizes delimiters itself, so these are plain delimiters."""

FONTS = {
    "mathrm": ("p", None), "mathup": ("p", None), "mathbf": ("b", None), "mathit": ("i", None),
    "boldsymbol": ("bi", None), "bm": ("bi", None), "mathbb": ("p", "double-struck"),
    "mathcal": ("p", "script"), "mathscr": ("p", "script"), "mathfrak": ("p", "fraktur"),
    "mathsf": ("p", "sans-serif"), "mathtt": ("p", "monospace"),
}  # fmt: skip
"""Font commands: (m:sty, m:scr)."""

TEXT = frozenset({"text", "textrm", "textup", "mbox", "textnormal", "textit", "textbf"})

MATRICES = {
    "matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
    "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"), "smallmatrix": ("", ""), "array": ("", ""),
}  # fmt: skip
ARRAYS = frozenset(
    {
        "aligned",
        "align",
        "align*",
        "alignat",
        "alignat*",
        "split",
        "gathered",
        "gather",
        "gather*",
        "eqnarray",
        "eqnarray*",
        "multline",
        "multline*",
    }
)
"""Environments that are rows of an equation (m:eqArr)."""

IGNORED = frozenset(
    {
        "displaystyle",
        "textstyle",
        "scriptstyle",
        "scriptscriptstyle",
        "limits",
        "nolimits",
        "nonumber",
        "notag",
        "left",
        "right",
        "mathstrut",
    }
)
"""Commands with no output of their own (\\left/\\right are handled where they are expected)."""

RELATIONS = frozenset(
    "= < > ≤ ≥ ≠ ≈ ≡ ∼ ≃ ≅ ∝ ≪ ≫ ≺ ≻ ⪯ ⪰ ⊂ ⊃ ⊆ ⊇ ∈ ∉ ∋ ⊥ ∥ ∣ ⊢ ⊨ ≐ ≍ ⩽ ⩾ ≲ ≳ ≔ "
    "→ ← ↔ ⇒ ⇐ ⇔ ⟹ ⟸ ⟺ ↦ ⟶ ⟵ ⟷ , ; ≕ ⊏ ⊐ ⊑ ⊒ ⊣ ⪅ ⪆ ≰ ≱ ≮ ≯ ≁ ≇ ∤ ∦ ⊈ ⊉ "
    "⇋ ⇑ ⇓ ⇕ ↛ ↚ ⇏ ⇍ ⊩ ⊪ ⊀ ⊁ ≼ ≽ ⩴ ⊲ ⊳ ⊴ ⊵ ⇎ ↮ ⇝ ≜ ∷ ↠ ↞ ⊬ ⊭ ≊ ↣ ↢ ∽ ⋍ ⋈ ↷ ↶ ⨝ ≂ ↺ ↻".split()
) | {"+", "−", "±", "∓"}
"""Where the operand of a big operator ends (relations, commas, + and -)."""
