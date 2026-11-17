"""The memory matrix: a function card's memory, opened with the arrow on the
card's right side.

Deliberately abstract - not the STM32's real memory map. Addresses are 16
bits and start at 0x0000; each row is 8 bytes, then the same bytes as
characters, so a string like "text:100," reads straight off it:

    ADDR    +0 +1 +2 +3 +4 +5 +6 +7  TEXT
    0x0000  74 65 78 74 3A 31 30 30  text:100

Nothing is put in it yet - every byte shows "--". Later the card's variables
go in, and cards tied together share one matrix.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsObject

ADDRESS_BITS = 16
MEMORY_SIZE = 1 << ADDRESS_BITS   # 64 KB: 0x0000 - 0xFFFF
BYTES_PER_ROW = 8
ROW_COUNT = MEMORY_SIZE // BYTES_PER_ROW
EMPTY = "--"                      # a byte nothing has written

_PADDING = 10
_BG = QColor("#1e1e1e")
_TITLE_BG = QColor("#2d2d2d")
_BORDER = QColor("#3d3d3d")
_TITLE_TEXT = QColor("#d4d4d4")
_HEADER_TEXT = QColor("#7a7a7a")
_ADDRESS_TEXT = QColor("#6a9fb5")
_EMPTY_TEXT = QColor("#4a4a4a")
_BYTE_TEXT = QColor("#b5cea8")    # a known byte - same green as numbers in the code
_CHAR_TEXT = QColor("#ce9178")    # its character - same as strings in the code
_ROW_STRIPE = QColor(255, 255, 255, 8)


def _font() -> QFont:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(10)
    return font


def address_text(address: int) -> str:
    return f"0x{address:0{ADDRESS_BITS // 4}X}"


class MemoryMatrix(QGraphicsObject):
    """The matrix panel. Its card places it (a child item, so it moves with
    the card) and sets its height; the scroll wheel over it scrolls the rows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._font = _font()
        self._metrics = QFontMetricsF(self._font)
        self._line_h = self._metrics.height() + 2
        self._title_h = self._line_h + _PADDING
        self.memory: dict[int, int] = {}   # address -> byte (0-255); missing = EMPTY
        self.first_row = 0                 # the row at the top (scrolled to)
        self._height = 200.0
        char_w = self._metrics.horizontalAdvance("0")
        # ADDR, two spaces, 8 cells of "XX ", one more space, 8 characters.
        self._address_w = char_w * (2 + ADDRESS_BITS // 4 + 2)
        self._cell_w = char_w * 3
        self._text_x = _PADDING + self._address_w + BYTES_PER_ROW * self._cell_w + char_w
        self._width = self._text_x + BYTES_PER_ROW * char_w + _PADDING

    # --- size and scrolling ---------------------------------------------------

    @property
    def width(self) -> float:
        return self._width

    def set_height(self, height: float) -> None:
        self.prepareGeometryChange()
        self._height = height
        self.scroll_to(self.first_row)

    @property
    def visible_rows(self) -> int:
        """How many rows fit under the title and column header."""
        return max(1, int((self._height - self._title_h - self._line_h - _PADDING / 2) // self._line_h))

    def scroll_to(self, row: int) -> None:
        self.first_row = max(0, min(row, ROW_COUNT - self.visible_rows))
        self.update()

    def scroll_by(self, rows: int) -> None:
        self.scroll_to(self.first_row + rows)

    # --- painting -------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(-1, -1, self._width + 2, self._height + 2)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(_BORDER, 1.5))
        painter.setBrush(QBrush(_BG))
        painter.drawRoundedRect(QRectF(0, 0, self._width, self._height), 6, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_TITLE_BG))
        painter.drawRoundedRect(QRectF(0.75, 0.75, self._width - 1.5, self._title_h), 5, 5)
        painter.drawRect(QRectF(0.75, self._title_h / 2, self._width - 1.5, self._title_h / 2 + 0.75))
        painter.setPen(QPen(_BORDER, 1))
        painter.drawLine(0, self._title_h, self._width, self._title_h)

        painter.setFont(self._font)
        painter.setPen(_TITLE_TEXT)
        painter.drawText(QRectF(_PADDING, _PADDING / 2, self._width, self._line_h),
                         Qt.AlignLeft | Qt.AlignVCenter, "Memory")
        last = address_text(MEMORY_SIZE - 1)
        painter.setPen(_HEADER_TEXT)
        painter.drawText(QRectF(0, _PADDING / 2, self._width - _PADDING, self._line_h),
                         Qt.AlignRight | Qt.AlignVCenter, f"{address_text(0)} - {last}")

        y = self._title_h + _PADDING / 2
        self._text_row(painter, y, "ADDR", [f"+{i:X}" for i in range(BYTES_PER_ROW)], "TEXT", header=True)
        y += self._line_h
        bottom = self._height - _PADDING / 2
        for row in range(self.first_row, min(ROW_COUNT, self.first_row + self.visible_rows)):
            if y + self._line_h > bottom + 0.5:
                break
            if row % 2:
                painter.fillRect(QRectF(1, y, self._width - 2, self._line_h), _ROW_STRIPE)
            base = row * BYTES_PER_ROW
            values = [self.memory.get(base + i) for i in range(BYTES_PER_ROW)]
            cells = [EMPTY if v is None else f"{v:02X}" for v in values]
            chars = "".join(" " if v is None else (chr(v) if 32 <= v < 127 else ".") for v in values)
            self._text_row(painter, y, address_text(base), cells, chars, values=values)
            y += self._line_h

    def _text_row(self, painter, y, address, cells, chars, header=False, values=None) -> None:
        rect = lambda x, w: QRectF(x, y, w, self._line_h)
        painter.setPen(_HEADER_TEXT if header else _ADDRESS_TEXT)
        painter.drawText(rect(_PADDING, self._address_w), Qt.AlignLeft | Qt.AlignVCenter, address)
        x = _PADDING + self._address_w
        for i, cell in enumerate(cells):
            empty = values is not None and values[i] is None
            painter.setPen(_HEADER_TEXT if header else (_EMPTY_TEXT if empty else _BYTE_TEXT))
            painter.drawText(rect(x, self._cell_w), Qt.AlignLeft | Qt.AlignVCenter, cell)
            x += self._cell_w
        painter.setPen(_HEADER_TEXT if header else _CHAR_TEXT)
        painter.drawText(rect(self._text_x, self._width), Qt.AlignLeft | Qt.AlignVCenter, chars)
