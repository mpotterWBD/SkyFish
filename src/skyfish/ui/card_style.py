"""Looks shared by function cards (card_canvas.py) and value cards
(value_card.py), kept apart so neither has to import the other."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

CLOSE_SIZE = 10   # the close X on every card's title bar
CLOSE_GAP = 12    # between the title text and the close X
_CLOSE = QColor("#9a9a9a")


def draw_close(painter: QPainter, rect: QRectF) -> None:
    """The close X on a card's title bar."""
    pen = QPen(_CLOSE, 1.6)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(rect.topLeft(), rect.bottomRight())
    painter.drawLine(rect.topRight(), rect.bottomLeft())
