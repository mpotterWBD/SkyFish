from PySide6.QtGui import QTextDocument

from skyfish.ui import highlighter as hl


def colours(text: str, suffix: str) -> list[dict[str, str]]:
    """For each line, {fragment text: colour name} as the highlighter painted it."""
    doc = QTextDocument()
    h = hl.CodeHighlighter(doc)
    h.set_language_for(suffix)
    doc.setPlainText(text)
    h.rehighlight()  # a bare document (no view) only highlights on the next event-loop pass
    out = []
    block = doc.begin()
    while block.isValid():
        line = {}
        for r in block.layout().formats():
            line[block.text()[r.start:r.start + r.length]] = r.format.foreground().color().name().upper()
        out.append(line)
        block = block.next()
    return out


def colour(fmt) -> str:
    return fmt.foreground().color().name().upper()


def test_python_words_strings_comments():
    lines = colours('def go(self):\n    return "a # not comment"  # real\n', ".py")
    assert lines[0]["def"] == colour(hl.DECLARE)
    assert lines[0]["go"] == colour(hl.FUNCTION)
    assert lines[0]["self"] == colour(hl.SELF)
    assert lines[1]["return"] == colour(hl.CONTROL)
    assert lines[1]['"a # not comment"'] == colour(hl.STRING)
    assert lines[1]["# real"] == colour(hl.COMMENT)


def test_python_triple_quoted_string_spans_lines():
    lines = colours('x = """first\nif while\nend""" + 1\n', ".py")
    assert lines[1] == {"if while": colour(hl.STRING)}
    assert lines[2]['end"""'] == colour(hl.STRING)
    assert lines[2]["1"] == colour(hl.NUMBER)


def test_c_block_comment_and_preprocessor():
    lines = colours('#include <stdio.h>\n/* start\nint x; */ uint8_t f(void) { return 0x1F; }\n', ".c")
    assert lines[0]["#include"] == colour(hl.CONTROL)
    assert lines[0][" <stdio.h>"] == colour(hl.STRING)
    assert lines[1] == {"/* start": colour(hl.COMMENT)}
    assert lines[2]["int x; */"] == colour(hl.COMMENT)
    assert lines[2]["uint8_t"] == colour(hl.TYPE)
    assert lines[2]["f"] == colour(hl.FUNCTION)
    assert lines[2]["0x1F"] == colour(hl.NUMBER)


def test_unknown_extension_is_plain():
    # Two blocks: the line, and the empty one after its trailing newline.
    assert colours("if x: return 1\n", ".txt") == [{}, {}]
