"""Value cards: a value typed in for one of a function card's inputs.

Drag a function card's blue input dot out onto the canvas and a value card
appears there, wired to that input. It knows the parameter's type
(analysis/values.py), checks what's typed as it goes ("Too big for uint16_t
(0 to 65,535)"), and when the value is good, stores it on the function card,
which shows it on the input's row: const char *KEY = "TEMP".

Like function cards, a value card moves only by its title bar.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QLineEdit,
)

from skyfish.analysis.functions import VariableInfo
from skyfish.ui.card_style import CLOSE_GAP, CLOSE_SIZE, draw_close as _draw_close
from skyfish.analysis.values import OTHER, InvalidValue, ValueType, describe, parse_value, value_type_for

# Same palette as the function cards.
_CARD_BG = QColor("#1e1e1e")
_TITLE_BG = QColor("#1f3340")   # a blue tint: an input, not a function
_BORDER = QColor("#3d3d3d")
_SELECTED_BORDER = QColor("#50BFFF")
_PORT = QColor("#50BFFF")       # inputs are blue on function cards too
_NAME = QColor("#9CDCFE")
_TYPE = QColor("#4EC9B0")
_GOOD = QColor("#35C053")
_BAD = QColor("#F86464")
_QUIET = QColor("#9a9a9a")
_WIRE = QColor("#50BFFF")

VALUE_CARD_WIDTH = 300
PADDING = 10
PORT_RADIUS = 4.5

# Embedded widgets live outside the main window, so its theme.qss doesn't
# reach them; they get their own look here, matching it.
_EDIT_STYLE = """
QLineEdit {
    background-color: #141414; color: #D4D4D4; border: 1px solid #3d3d3d; border-radius: 3px;
    padding: 3px 5px; font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 13px;
    selection-background-color: #2f4a5c;
}
QLineEdit:focus { border: 1px solid #50BFFF; }
QLineEdit:disabled { color: #6a6a6a; }
"""
_CHECK_STYLE = """
QCheckBox { color: #e0e0e0; background: transparent; spacing: 6px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #7a7a7a; border-radius: 3px;
                       background-color: #141414; }
QCheckBox::indicator:checked { background-color: #35C053; border: 1px solid #2a9843; }
"""


def _font(point_size: int = 10) -> QFont:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(point_size)
    return font


class ValueCard(QGraphicsObject):
    """A value for one input (parameter) of a function card. Its port (right
    edge) is wired to that input's port."""

    def __init__(self, function_card, parameter: VariableInfo, language: str | None) -> None:
        super().__init__()
        self.function_card = function_card
        self.parameter = parameter
        self.value_type: ValueType = value_type_for(parameter.type, language)
        self.value = None          # the parsed value, when good
        self.error: str | None = None
        self.on_moved = None       # set by the canvas: redraw the wire
        self.on_close = None       # set by the canvas: the title bar's X was clicked
        self._dragging = False
        self._font = _font()
        self._metrics = QFontMetricsF(self._font)
        self._line_h = self._metrics.height() + 2
        self._title_h = self._line_h + PADDING

        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

        y = self._title_h + PADDING
        self.editor = QLineEdit()
        self.editor.setStyleSheet(_EDIT_STYLE)
        self.editor.setPlaceholderText(self.value_type.hint())
        self.editor.setToolTip(self.value_type.hint())
        self._editor_proxy = QGraphicsProxyWidget(self)
        self._editor_proxy.setWidget(self.editor)
        self._editor_proxy.setGeometry(QRectF(PADDING, y, VALUE_CARD_WIDTH - 2 * PADDING, 26))
        self._port_y = y + 13
        y += 26 + 6

        self.null_box: QCheckBox | None = None
        if self.value_type.pointer:
            self.null_box = QCheckBox("NULL")
            self.null_box.setStyleSheet(_CHECK_STYLE)
            proxy = QGraphicsProxyWidget(self)
            proxy.setWidget(self.null_box)
            proxy.setPos(PADDING, y)
            y += 22
        self._message_y = y + self._line_h / 2
        self._height = y + self._line_h + PADDING / 2

        if self.value_type.kind == OTHER:
            # No way to type one in yet: only NULL or "set" makes sense.
            self.editor.setEnabled(False)
        self.editor.textChanged.connect(self._check)
        if self.null_box is not None:
            self.null_box.toggled.connect(self._check)
        self._check()

    # --- the value --------------------------------------------------------

    @property
    def is_null(self) -> bool:
        return self.null_box is not None and self.null_box.isChecked()

    def set_text(self, text: str) -> None:
        self.editor.setText(text)

    def _check(self) -> None:
        """Re-reads the field after every change: stores a good value on the
        function card, or shows what's wrong."""
        self.editor.setEnabled(not self.is_null and self.value_type.kind != OTHER)
        self.value, self.error = None, None
        if self.is_null:
            shown = "NULL"
        elif self.value_type.kind == OTHER:
            self.error = f"No value entry for {self.value_type.type_name} yet"
            shown = None
        else:
            try:
                self.value = parse_value(self.value_type, self.editor.text())
                shown = describe(self.value_type, self.value)
            except InvalidValue as problem:
                self.error = str(problem) if self.editor.text().strip() else None
                shown = None
        self.function_card.set_input_value(self.parameter.name, shown)
        self.update()

    @property
    def has_value(self) -> bool:
        return self.is_null or (self.error is None and self.value is not None)

    # --- geometry -----------------------------------------------------------

    def port_scene_pos(self) -> QPointF:
        return self.mapToScene(QPointF(VALUE_CARD_WIDTH, self._port_y))

    def port_local_pos(self) -> QPointF:
        return QPointF(VALUE_CARD_WIDTH, self._port_y)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override
        return QRectF(-1, -1, VALUE_CARD_WIDTH + PORT_RADIUS + 2, self._height + 2)

    def itemChange(self, change, value):  # noqa: N802 - Qt override
        if change == QGraphicsItem.ItemPositionHasChanged and self.on_moved is not None:
            self.on_moved()
        return super().itemChange(change, value)

    # --- painting -----------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(_SELECTED_BORDER if self.isSelected() else _BORDER, 1.5))
        painter.setBrush(QBrush(_CARD_BG))
        painter.drawRoundedRect(QRectF(0, 0, VALUE_CARD_WIDTH, self._height), 6, 6)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_TITLE_BG))
        title = QRectF(0.75, 0.75, VALUE_CARD_WIDTH - 1.5, self._title_h)
        painter.drawRoundedRect(title, 5, 5)
        painter.drawRect(QRectF(title.left(), self._title_h / 2, title.width(), self._title_h / 2 + 0.75))
        painter.setPen(QPen(_BORDER, 1))
        painter.drawLine(QPointF(0, self._title_h), QPointF(VALUE_CARD_WIDTH, self._title_h))

        painter.setFont(self._font)
        text_rect = QRectF(PADDING, PADDING / 2, VALUE_CARD_WIDTH - 2 * PADDING, self._line_h)
        painter.setPen(_NAME)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.parameter.name)
        painter.setPen(_TYPE)
        type_rect = QRectF(text_rect.left(), text_rect.top(), text_rect.width() - CLOSE_SIZE - CLOSE_GAP,
                           text_rect.height())
        painter.drawText(type_rect, Qt.AlignRight | Qt.AlignVCenter, self.value_type.type_name)
        _draw_close(painter, self.close_rect())

        if self.error:
            colour, message = _BAD, self.error
        elif self.has_value:
            colour, message = _GOOD, "✓ " + describe(self.value_type, None if self.is_null else self.value)
        else:
            colour, message = _QUIET, "Type a value"
        painter.setPen(colour)
        message = self._metrics.elidedText(message, Qt.ElideRight, VALUE_CARD_WIDTH - 2 * PADDING)
        painter.drawText(QRectF(PADDING, self._message_y - self._line_h / 2, VALUE_CARD_WIDTH, self._line_h),
                         Qt.AlignLeft | Qt.AlignVCenter, message)

        painter.setPen(QPen(_CARD_BG, 1.5))
        painter.setBrush(QBrush(_PORT))
        painter.drawEllipse(self.port_local_pos(), PORT_RADIUS, PORT_RADIUS)

    # --- moving by the title bar only -----------------------------------------

    def close_rect(self) -> QRectF:
        return QRectF(VALUE_CARD_WIDTH - PADDING - CLOSE_SIZE, (self._title_h - CLOSE_SIZE) / 2,
                      CLOSE_SIZE, CLOSE_SIZE)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton and self.close_rect().contains(event.pos()):
            if self.on_close is not None:
                self.on_close(self)
            event.accept()
            return
        if event.button() == Qt.LeftButton and event.pos().y() <= self._title_h:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            self.scene().clearSelection()
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._dragging:
            super().mouseMoveEvent(event)
        else:
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._dragging:
            self._dragging = False
            super().mouseReleaseEvent(event)
            self.setCursor(Qt.OpenHandCursor)
        else:
            event.accept()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.close_rect().contains(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.OpenHandCursor if event.pos().y() <= self._title_h else Qt.ArrowCursor)


class Wire(QGraphicsPathItem):
    """The curve from a value card's port to its function card's input port."""

    def __init__(self, value_card: ValueCard) -> None:
        super().__init__()
        self.value_card = value_card
        pen = QPen(_WIRE, 1.6)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(-1)  # under the cards
        self.update_path()

    def update_path(self) -> None:
        start = self.value_card.port_scene_pos()
        end = self.value_card.function_card.input_port_scene_pos(self.value_card.parameter.name)
        if end is None:
            self.setPath(QPainterPath())
            return
        bend = max(40.0, abs(end.x() - start.x()) / 2)
        path = QPainterPath(start)
        path.cubicTo(start + QPointF(bend, 0), end - QPointF(bend, 0), end)
        self.setPath(path)
