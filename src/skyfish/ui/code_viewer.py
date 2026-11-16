import re
from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit, QWidget

from skyfish.analysis.logic import Span
from skyfish.ui.highlighter import CodeHighlighter

# Colours from theme.qss's palette (a gutter is painted, so QSS can't reach it).
_GUTTER_BG = QColor("#1a1a1a")
_GUTTER_TEXT = QColor("#6a6a6a")
_HIGHLIGHT = QColor("#2f4a5c")
_LINK = QColor("#264f78")             # the code a card row came from (VS Code's selection blue)
_WORD = QColor(80, 191, 255, 70)      # a marked word - the same blue as the cards' word marks

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")

# Layout knob - how many spaces wide a tab is drawn (the firmware is tab-indented).
TAB_WIDTH_SPACES = 4


class _LineNumberArea(QWidget):
    def __init__(self, viewer: "CodeViewer") -> None:
        super().__init__(viewer)
        self._viewer = viewer

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._viewer.line_number_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._viewer.paint_line_numbers(event)


class CodeViewer(QPlainTextEdit):
    """Read-only code display with line numbers. SkyFish analyzes code; it
    never edits it, so text can be selected and copied but not changed.

    Right-click offers "Add Card: <name>" above the usual Copy / Select All
    when card_target (set by the window) names a function for the word and
    line clicked; choosing it emits add_card_requested with that function."""

    add_card_requested = Signal(object)
    # A plain click in the code (not a drag-select): line (1-based), column,
    # and the identifier clicked on ("" when it isn't one).
    code_clicked = Signal(int, int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._line_marks: list = []   # go_to_line's whole-line band
        self._link_marks: list = []   # the code a card row came from
        self._word_marks: list = []   # a word marked everywhere in a function
        self.linked_spans: list[Span] = []
        self.marked_word: str | None = None
        # (word, line) -> the function to offer a card for, or None.
        self.card_target: Callable[[str, int], object | None] | None = None
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._highlighter = CodeHighlighter(self.document())
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    def card_target_at(self, pos: QPoint) -> object | None:
        """The function a right-click at pos (viewport coordinates) would
        offer a card for, or None."""
        if self.card_target is None:
            return None
        cursor = self.cursorForPosition(pos)
        line = cursor.blockNumber() + 1
        cursor.select(QTextCursor.WordUnderCursor)
        return self.card_target(cursor.selectedText(), line)

    def context_menu_for(self, pos: QPoint) -> QMenu:
        """The right-click menu at pos: Add Card (when there's a function
        there) above the standard read-only Copy / Select All."""
        menu = self.createStandardContextMenu(pos)
        target = self.card_target_at(pos)
        if target is not None:
            add = QAction(f"Add Card: {target.name}", menu)
            add.triggered.connect(lambda: self.add_card_requested.emit(target))
            first = menu.actions()[0] if menu.actions() else None
            menu.insertAction(first, add)
            menu.insertSeparator(first)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        menu = self.context_menu_for(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def show_code(self, text: str, suffix: str = "") -> None:
        """Shows text, coloured for the language that suffix (the file's
        extension) names; unknown extensions are shown plain."""
        self._highlighter.set_language_for(suffix)
        # Set here, not in __init__: the code font comes from theme.qss, which
        # is only applied once the window is styled (ensurePolished forces it
        # now, so this measures the code font).
        self.ensurePolished()
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * TAB_WIDTH_SPACES)
        self.setPlainText(text)
        self._line_marks, self._link_marks, self._word_marks = [], [], []
        self.linked_spans: list[Span] = []
        self.marked_word: str | None = None
        self._refresh_marks()

    def go_to_line(self, line: int) -> None:
        """Scrolls line (1-based) near the top and highlights it."""
        block = self.document().findBlockByNumber(line - 1)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self._scroll_to(cursor)
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(_HIGHLIGHT)
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.cursor = cursor
        self._line_marks = [selection]
        self._refresh_marks()

    # --- tie-ins with the cards ---------------------------------------------

    def cursor_for(self, line: int, col: int) -> QTextCursor:
        """A cursor at line (1-based), col (characters, 0-based)."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + min(col, max(0, block.length() - 1)))
        return cursor

    def show_link(self, spans: list[Span], scroll: bool = True) -> None:
        """Highlights exactly the code a card's row came from - a variable's
        declaration, an if's header, a return - and (with scroll) brings the
        first into view. An empty list clears it."""
        self.linked_spans = [s for s in spans if s is not None]
        self._line_marks, self._link_marks = [], []
        for span in self.linked_spans:
            cursor = self.cursor_for(span.line, span.col)
            cursor.setPosition(self.cursor_for(span.end_line, span.end_col).position(), QTextCursor.KeepAnchor)
            mark = QTextEdit.ExtraSelection()
            mark.format.setBackground(_LINK)
            mark.cursor = cursor
            self._link_marks.append(mark)
        if self.linked_spans and scroll:
            first = self.linked_spans[0]
            self._scroll_to(self.cursor_for(first.line, first.col))
        self._refresh_marks()

    def mark_word(self, word: str | None, within: Span | None = None) -> None:
        """Marks every whole-word use of word, inside within (a function's
        span) when given. None clears the marks."""
        self.marked_word = word
        self._word_marks = []
        if word:
            start = self.cursor_for(within.line, within.col).position() if within else 0
            end = self.cursor_for(within.end_line, within.end_col).position() if within else None
            pattern = QRegularExpression(rf"\b{QRegularExpression.escape(word)}\b")
            cursor = self.document().find(pattern, start)
            while not cursor.isNull() and (end is None or cursor.selectionEnd() <= end):
                mark = QTextEdit.ExtraSelection()
                mark.format.setBackground(_WORD)
                mark.cursor = cursor
                self._word_marks.append(mark)
                cursor = self.document().find(pattern, cursor.selectionEnd())
        self._refresh_marks()

    def _refresh_marks(self) -> None:
        # Line first, then links, then words on top.
        self.setExtraSelections([*self._line_marks, *self._link_marks, *self._word_marks])

    def _scroll_to(self, cursor: QTextCursor) -> None:
        # Centred in the view, with the code around it in sight. (Near the
        # very top or bottom of the file it goes as close as it can.)
        self.setTextCursor(cursor)
        self.centerCursor()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and not self.textCursor().hasSelection():
            cursor = self.cursorForPosition(event.position().toPoint())
            line, col = cursor.blockNumber() + 1, cursor.positionInBlock()
            cursor.select(QTextCursor.WordUnderCursor)
            word = cursor.selectedText()
            self.code_clicked.emit(line, col, word if _IDENTIFIER.fullmatch(word) else "")

    # --- line-number gutter ---------------------------------------------

    def line_number_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        r = self.contentsRect()
        self._gutter.setGeometry(QRect(r.left(), r.top(), self.line_number_width(), r.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.setFont(self.font())  # the code's monospace font, not the UI font
        painter.fillRect(event.rect(), _GUTTER_BG)
        painter.setPen(_GUTTER_TEXT)
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        width = self._gutter.width() - 6
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(0, top, width, height, Qt.AlignRight, str(block.blockNumber() + 1))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()
        painter.end()
