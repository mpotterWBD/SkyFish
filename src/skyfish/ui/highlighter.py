"""Syntax colouring for the code viewer, in VS Code's "Dark+" palette.

Each line is coloured in two passes: word rules first (functions, types,
keywords, numbers), then strings and comments on top, found with a single
left-to-right scan so a "#" inside a string or a quote inside a comment
can't confuse it. Multi-line Python strings and C /* */ comments carry over
to the next line through QSyntaxHighlighter's block state.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from skyfish.analysis.functions import C_FAMILY_EXTS, PYTHON_EXTS


def _fmt(color: str, italic: bool = False, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if italic:
        f.setFontItalic(True)
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


# Dark+ colours.
CONTROL = _fmt("#C586C0")      # if, for, return, import, #include
DECLARE = _fmt("#569CD6")      # def, class, static, const, True/None
TYPE = _fmt("#4EC9B0")         # class names, uint8_t, int
FUNCTION = _fmt("#DCDCAA")     # name before "(", decorators
STRING = _fmt("#CE9178")
NUMBER = _fmt("#B5CEA8")
COMMENT = _fmt("#6A9955", italic=True)
CONSTANT = _fmt("#4FC1FF")     # ALL_CAPS macros and constants
SELF = _fmt("#9CDCFE")         # self / cls / this

# Text no rule colours (most variable names) - keep the same as the
# QPlainTextEdit#codeViewer colour in theme.qss.
PLAIN_COLOR = QColor("#D4D4D4")


_NUMBER_RE = r"\b(?:0[xX][0-9A-Fa-f_]+|0[bB][01_]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?)[uUlLfFjJ]*\b"
_FUNCTION_RE = r"\b[A-Za-z_]\w*(?=\s*\()"


def _words(words: str) -> str:
    return r"\b(?:" + "|".join(words.split()) + r")\b"


_PYTHON_RULES = [
    (re.compile(_FUNCTION_RE), FUNCTION),
    (re.compile(r"(?<=\bclass\s)\s*[A-Za-z_]\w*"), TYPE),
    (re.compile(r"(?<=\bdef\s)\s*[A-Za-z_]\w*"), FUNCTION),
    (re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"), CONSTANT),
    (re.compile(_words("int float str bool bytes list dict set tuple object type")), TYPE),
    (re.compile(_words(
        "if elif else for while break continue return yield try except finally raise "
        "with import from as pass assert match case await del global nonlocal"
    )), CONTROL),
    (re.compile(_words("def class lambda async and or not in is True False None")), DECLARE),
    (re.compile(_words("self cls")), SELF),
    (re.compile(r"^\s*@[\w.]+"), FUNCTION),
    (re.compile(_NUMBER_RE), NUMBER),
]

_C_RULES = [
    (re.compile(_FUNCTION_RE), FUNCTION),
    (re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"), CONSTANT),
    (re.compile(r"\b\w+_t\b"), TYPE),
    (re.compile(_words(
        "void char short int long float double signed unsigned bool _Bool size_t "
        "uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t"
    )), TYPE),
    (re.compile(_words(
        "if else for while do switch case default break continue return goto "
        "try catch throw"
    )), CONTROL),
    (re.compile(_words(
        "const volatile static extern register inline struct union enum typedef sizeof "
        "auto class public private protected virtual override final template typename "
        "namespace using new delete operator friend explicit constexpr noexcept "
        "true false nullptr NULL"
    )), DECLARE),
    (re.compile(_words("this")), SELF),
    (re.compile(_NUMBER_RE), NUMBER),
    # Preprocessor: the directive itself, and <file.h> after #include.
    (re.compile(r"^\s*#\s*\w+"), CONTROL),
    (re.compile(r"(?<=include)\s*<[^>]*>"), STRING),
]

_PY_SCAN = re.compile(
    r"(?P<comment>#.*$)"
    r"|(?P<triple>(?:\b[rRbBfFuU]{1,2})?(?:\"\"\"|'''))"
    r"|(?P<string>(?:\b[rRbBfFuU]{1,2})?(?:\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?))"
)
_C_SCAN = re.compile(
    r"(?P<comment>//.*$)"
    r"|(?P<block>/\*)"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?)"
)

# Block states carried between lines.
_NORMAL = 0
_PY_TRIPLE_DOUBLE = 1
_PY_TRIPLE_SINGLE = 2
_C_BLOCK_COMMENT = 3


_SNIPPET_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")


def language_for(suffix: str) -> str | None:
    """"python", "c" (C and C++) or None (not coloured) for a file extension."""
    suffix = suffix.lower()
    if suffix in PYTHON_EXTS:
        return "python"
    if suffix in C_FAMILY_EXTS:
        return "c"
    return None


def colour_spans(text: str, language: str | None) -> list[tuple[str, QColor]]:
    """text split into (piece, colour) runs, coloured by the same word rules
    the code view uses - so a short snippet such as a variable declaration
    or an if condition can be drawn elsewhere looking just as it does in the
    code. Strings are coloured last, over the word rules, as in the code
    view; snippets never hold comments, so there's no comment scan."""
    colours = [PLAIN_COLOR] * len(text)
    rules = _PYTHON_RULES if language == "python" else _C_RULES if language == "c" else []
    if rules:
        rules = [*rules, (_SNIPPET_STRING, STRING)]
    for pattern, fmt in rules:
        colour = fmt.foreground().color()
        for m in pattern.finditer(text):
            colours[m.start():m.end()] = [colour] * (m.end() - m.start())
    spans: list[tuple[str, QColor]] = []
    for ch, colour in zip(text, colours):
        if spans and spans[-1][1] == colour:
            spans[-1] = (spans[-1][0] + ch, colour)
        else:
            spans.append((ch, colour))
    return spans


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._language: str | None = None

    def set_language_for(self, suffix: str) -> None:
        """Picks Python, C/C++ or no colouring from a file extension. Call
        before the text is set (or rehighlight() after)."""
        self._language = language_for(suffix)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        self.setCurrentBlockState(_NORMAL)
        if self._language is None:
            return
        # Python indexes by code point, Qt by UTF-16 unit: they differ only
        # after characters outside the BMP (emoji), so map just in that case.
        self._u16 = _utf16_map(text)
        if self._language == "python":
            self._highlight(text, _PYTHON_RULES, self._python_strings_comments)
        else:
            self._highlight(text, _C_RULES, self._c_strings_comments)

    def _set(self, start: int, end: int, fmt: QTextCharFormat) -> None:
        if self._u16 is not None:
            start, end = self._u16[start], self._u16[end]
        if end > start:
            self.setFormat(start, end - start, fmt)

    def _highlight(self, text: str, rules, strings_comments) -> None:
        for pattern, fmt in rules:
            for m in pattern.finditer(text):
                self._set(m.start(), m.end(), fmt)
        strings_comments(text)

    def _python_strings_comments(self, text: str) -> None:
        pos = 0
        state = self.previousBlockState()
        if state in (_PY_TRIPLE_DOUBLE, _PY_TRIPLE_SINGLE):
            pos = self._close(text, 0, '"""' if state == _PY_TRIPLE_DOUBLE else "'''", state, STRING)
        while pos < len(text):
            m = _PY_SCAN.search(text, pos)
            if m is None:
                return
            if m.group("comment"):
                self._set(m.start(), m.end(), COMMENT)
                return
            if m.group("triple"):
                delim = m.group("triple")[-3:]
                end = text.find(delim, m.end())
                if end == -1:
                    self._set(m.start(), len(text), STRING)
                    self.setCurrentBlockState(_PY_TRIPLE_DOUBLE if delim == '"""' else _PY_TRIPLE_SINGLE)
                    return
                self._set(m.start(), end + 3, STRING)
                pos = end + 3
            else:
                self._set(m.start(), m.end(), STRING)
                pos = m.end()

    def _c_strings_comments(self, text: str) -> None:
        pos = 0
        if self.previousBlockState() == _C_BLOCK_COMMENT:
            pos = self._close(text, 0, "*/", _C_BLOCK_COMMENT, COMMENT)
        while pos < len(text):
            m = _C_SCAN.search(text, pos)
            if m is None:
                return
            if m.group("comment"):
                self._set(m.start(), m.end(), COMMENT)
                return
            if m.group("block"):
                pos = self._close(text, m.start(), "*/", _C_BLOCK_COMMENT, COMMENT, search_from=m.end())
            else:
                self._set(m.start(), m.end(), STRING)
                pos = m.end()

    def _close(self, text, start, delim, state, fmt, search_from=None) -> int:
        """Colours from start up to and including delim. If delim isn't on
        this line, colours to the end and carries state to the next line.
        Returns where scanning should continue."""
        end = text.find(delim, start if search_from is None else search_from)
        if end == -1:
            self._set(start, len(text), fmt)
            self.setCurrentBlockState(state)
            return len(text)
        self._set(start, end + len(delim), fmt)
        return end + len(delim)


def _utf16_map(text: str) -> list[int] | None:
    if all(ord(c) <= 0xFFFF for c in text):
        return None
    out, n = [], 0
    for c in text:
        out.append(n)
        n += 2 if ord(c) > 0xFFFF else 1
    out.append(n)
    return out
