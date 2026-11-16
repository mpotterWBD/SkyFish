"""The block-diagram canvas: one card per function or method the user adds
from the list (right-click > Add Card).

A card, top to bottom:
  - title bar: the name
  - variables (Variables box): the locals, written and coloured as in the list
  - logic (Logic box): the function as paths from inputs to outputs -
      inputs   the parameters, each with a port dot on the left edge
      rows     each if / else-if / else branch, indented by nesting; a branch
               that returns has a line to its output, one that doesn't is a
               detour the flow continues after
      outputs  one port dot on the right edge per distinct return value
               ("end" = no value / running off the end), lines from every
               row that exits through it
A card moves only by its title bar. Clicking a word anywhere else marks that
word wherever it appears in the card (the same word again, or a blank spot,
clears it). Empty canvas pans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from skyfish.analysis.functions import FUNCTION, LOCAL, PARAMETER, FunctionInfo
from skyfish.analysis.logic import LogicRow, logic_rows, outputs
from skyfish.ui.highlighter import FUNCTION as FUNCTION_FORMAT
from skyfish.ui.highlighter import colour_spans
from skyfish.ui.value_card import ValueCard, Wire

# Look - same dark palette as theme.qss.
_CANVAS_BG = QColor("#141414")
_GRID_DOT = QColor("#2a2a2a")
_CARD_BG = QColor("#1e1e1e")
_TITLE_BG = QColor("#2d2d2d")
_BORDER = QColor("#3d3d3d")
_SELECTED_BORDER = QColor("#50BFFF")
_INPUT_PORT = QColor("#50BFFF")
_OUTPUT_PORT = QColor("#35C053")
_WIRE = QColor("#7a7a7a")
_DIAMOND = QColor("#C586C0")   # same as if/else in the code
# A clicked word, marked wherever it appears in the card (like VS Code's
# matching-word highlight).
_HIGHLIGHT_FILL = QColor(80, 191, 255, 55)
_HIGHLIGHT_BORDER = QColor(80, 191, 255, 150)
_QUIET_TEXT = QColor("#9a9a9a")
_LINK_BAND = QColor(38, 79, 120, 150)  # the line tied to the code view - the code view's link blue  # "always", "otherwise", "in loop" ...

# Layout knobs (pixels at 100% zoom).
GRID_STEP = 20           # canvas dot spacing
CARD_PADDING = 10        # inside a card, around its text
CARD_MIN_WIDTH = 160
CARD_GAP = 40            # between cards placed automatically
SCENE_SIZE = 10_000      # the canvas area cards can be placed and panned in
ZOOM_MIN, ZOOM_MAX = 0.25, 3.0
ZOOM_STEP = 1.15         # per scroll-wheel notch
INDENT_CHARS = 3         # per nesting level in the logic rows
MAX_CONDITION_CHARS = 56  # longer conditions end in "…"
MAX_OUTPUT_CHARS = 24
MAX_INPUT_VALUE_CHARS = 32  # a typed-in value shown on its input's row
WIRE_GAP = 14            # between a row's text and its wire, and around lanes
LANE_STEP = 10           # between the vertical wires of different outputs
PORT_RADIUS = 4.5


def _card_font() -> QFont:
    font = QFont("Consolas")
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(10)
    return font


_WORD = re.compile(r"[A-Za-z_]\w*")


def _whole_word(word: str) -> re.Pattern:
    """Matches word only as a whole identifier: "line" in "line[0]" but not in "s_line_buf"."""
    return re.compile(rf"(?<![\w]){re.escape(word)}(?![\w])")


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass
class _Row:
    """One drawn line of the logic section."""
    y: float              # centre
    x: float              # where the text starts
    spans: list           # [(text, QColor)]
    text_right: float
    exit: str | None
    diamond: bool         # a branch row (◆) rather than always/otherwise
    logic: LogicRow       # where it came from, with its code spans


class FunctionCard(QGraphicsObject):
    """One function or method as a block - see the module doc for the parts."""

    def __init__(self, function: FunctionInfo, language: str | None) -> None:
        super().__init__()
        self.function = function
        self._language = language
        self._show_variables = True
        self._show_logic = True
        self._font = _card_font()
        self._metrics = QFontMetricsF(self._font)
        self._italic = QFont(self._font)
        self._italic.setItalic(True)
        self._locals = [v for v in function.variables if v.kind == LOCAL]
        self._variable_spans = [colour_spans(v.declaration, language) for v in self._locals]
        self.parameters = [v for v in function.variables if v.kind == PARAMETER]
        self._input_spans = [colour_spans(v.declaration, language) for v in self.parameters]
        # A value typed into a value card for an input, as shown on its row
        # ("TEMP" in quotes, NULL, 12...), by parameter name.
        self.input_values: dict[str, str] = {}
        self.logic_rows = (logic_rows(function.flow, function.end_line, function.end_span)
                           if function.has_body else [])
        self.outputs = outputs(self.logic_rows)
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)  # so wires can follow it
        self.setAcceptHoverEvents(True)  # cursor: hand on the title, pointer on words
        self._dragging = False           # a press on the title bar is moving the card
        self.highlight: str | None = None  # the clicked word, marked everywhere in the card
        # The line lit up because its code was clicked in the code view (an
        # index into _runs), and the hook that tells the canvas a line or word
        # on the card was clicked: on_clicked(card, code spans, word).
        self.linked_run: int | None = None
        self.on_clicked = None
        # Set by the canvas: called when the card moves or changes shape (wires
        # follow), and when an input's dot is dragged out and dropped.
        self.on_moved = None
        self.on_input_dropped = None
        self._input_drag: tuple[str, object] | None = None  # (parameter name, the line being drawn)
        self._layout()

    @property
    def key(self) -> tuple[str, int]:
        return (self.function.name, self.function.line)

    @property
    def size(self) -> tuple[float, float]:
        """(width, height) of the card itself."""
        return self._size

    @property
    def variable_line_count(self) -> int:
        return len(self._variable_spans) if self._show_variables else 0

    @property
    def logic_shown(self) -> bool:
        return self._show_logic

    def set_sections(self, show_variables: bool, show_logic: bool) -> None:
        if (show_variables, show_logic) != (self._show_variables, self._show_logic):
            self.prepareGeometryChange()
            self._show_variables, self._show_logic = show_variables, show_logic
            self._layout()
            self.update()
            self._moved()

    # --- inputs and their values ---------------------------------------------

    def set_input_value(self, name: str, shown: str | None) -> None:
        """Shows a value card's value on the input's row (None clears it)."""
        if shown is None:
            self.input_values.pop(name, None)
        else:
            self.input_values[name] = shown
        self.prepareGeometryChange()
        self._layout()
        self.update()
        self._moved()

    def input_port_scene_pos(self, name: str) -> QPointF | None:
        """Where the input's blue dot is, in canvas coordinates, or None when
        the inputs aren't showing (Logic unticked)."""
        for y, _, param in self._inputs:
            if param == name:
                return self.mapToScene(QPointF(0, y))
        return None

    def input_at(self, pos: QPointF) -> str | None:
        """The parameter whose blue dot is at pos (card coordinates), if any."""
        for y, _, name in self._inputs:
            if (pos.x() ** 2 + (pos.y() - y) ** 2) ** 0.5 <= PORT_RADIUS + 5:
                return name
        return None

    def _moved(self) -> None:
        if self.on_moved is not None:
            self.on_moved()

    def itemChange(self, change, value):  # noqa: N802 - Qt override
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._moved()
        return super().itemChange(change, value)

    # --- layout ---------------------------------------------------------

    def _width_of(self, spans) -> float:
        return sum(self._metrics.horizontalAdvance(t) for t, _ in spans)

    def _layout(self) -> None:
        """Works out every position paint() needs, and the card's size."""
        line_h = self._metrics.height() + 2
        self._line_h = line_h
        self._title_h = line_h + CARD_PADDING
        content_right = CARD_PADDING + self._metrics.horizontalAdvance(self.function.name)
        y = self._title_h

        self._variables_top = y
        if self.variable_line_count:
            y += CARD_PADDING / 2
            for spans in self._variable_spans:
                content_right = max(content_right, CARD_PADDING + self._width_of(spans))
            y += self.variable_line_count * line_h + CARD_PADDING / 2

        self._logic_top = y
        self._inputs: list[tuple[float, list, str]] = []  # (centre y, spans, parameter name)
        self._rows: list[_Row] = []
        self._ports: dict[str, float] = {}   # output -> port centre y
        self._lanes: dict[str, float] = {}   # output -> its vertical wire's x
        if self._show_logic and (self._input_spans or self.logic_rows):
            y += CARD_PADDING / 2
            for param, spans in zip(self.parameters, self._input_spans):
                if param.name in self.input_values:
                    # The typed-in value: " = " quiet, the value coloured as code.
                    spans = [*spans, (" = ", _QUIET_TEXT),
                             *colour_spans(_shorten(self.input_values[param.name], MAX_INPUT_VALUE_CHARS),
                                           self._language)]
                self._inputs.append((y + line_h / 2, spans, param.name))
                content_right = max(content_right, CARD_PADDING + self._width_of(spans))
                y += line_h
            if self._input_spans and self.logic_rows:
                y += line_h / 2  # a little air between inputs and logic
            indent_w = INDENT_CHARS * self._metrics.horizontalAdvance(" ")
            marker_w = self._metrics.horizontalAdvance("  ")
            for row in self.logic_rows:
                x = CARD_PADDING + row.indent * indent_w + marker_w
                if not row.is_branch:  # always / otherwise / "in loop": quiet text
                    spans = [(row.keyword, _QUIET_TEXT)]
                else:
                    label = row.keyword + (" " + _shorten(row.condition, MAX_CONDITION_CHARS) if row.condition else "")
                    spans = colour_spans(label, self._language)
                text_right = x + self._width_of(spans)
                self._rows.append(_Row(y + line_h / 2, x, spans, text_right, row.exit,
                                       row.is_branch, row))
                content_right = max(content_right, text_right)
                y += line_h
            y += CARD_PADDING / 2

            # Outputs: each port sits level with the last row exiting through
            # it; each has its own vertical "lane" so wires don't overlap.
            exits = list(self.outputs)
            for name in exits:
                self._ports[name] = max(r.y for r in self._rows if r.exit == name)
            for k, name in enumerate(exits):
                self._lanes[name] = content_right + WIRE_GAP + k * LANE_STEP
            self._output_spans = {name: colour_spans(_shorten(name, MAX_OUTPUT_CHARS), self._language)
                                  for name in exits}

        width = max(CARD_MIN_WIDTH, content_right + CARD_PADDING)
        if self._ports:
            last_lane = max(self._lanes.values())
            self._port_label_x = last_lane + WIRE_GAP
            label_w = max(self._width_of(s) for s in self._output_spans.values())
            width = max(width, self._port_label_x + label_w + CARD_PADDING + PORT_RADIUS)
        self._size = (width, max(y, self._title_h))

        # Every piece of text on the card as (x, centre y, text, code spans,
        # body span): for finding the word under a click, marking a
        # highlighted word wherever it appears, and tying each line to the
        # code it came from (code spans: what the code view highlights; body
        # span: clicking anywhere inside it in the code finds this line).
        self._output_label_x = {
            name: max(self._port_label_x, width - CARD_PADDING - PORT_RADIUS - self._width_of(spans))
            for name, spans in (self._output_spans.items() if self._ports else [])
        }
        def text(spans) -> str:
            return "".join(t for t, _ in spans)

        runs = [(CARD_PADDING, CARD_PADDING / 2 + line_h / 2, self.function.name, [], None)]
        var_y = self._variables_top + CARD_PADDING / 2 + line_h / 2
        for var, spans in zip(self._locals, self._variable_spans) if self._show_variables else []:
            runs.append((CARD_PADDING, var_y, text(spans), [var.span], None))
            var_y += line_h
        params = {p.name: p for p in self.parameters}
        runs += [(CARD_PADDING, iy, text(spans), [params[name].span], None) for iy, spans, name in self._inputs]
        for r in self._rows:
            # A branch stands for its header and the return it leaves through.
            links = [r.logic.span]
            if r.logic.exit_span is not None and r.logic.exit_span != r.logic.span:
                links.append(r.logic.exit_span)
            runs.append((r.x, r.y, text(r.spans), links, r.logic.body_span))
        for name in self._ports:
            # An output stands for every return that leaves through it.
            returns = [r.logic.exit_span for r in self._rows if r.exit == name and r.logic.exit_span]
            runs.append((self._output_label_x[name], self._ports[name], text(self._output_spans[name]),
                         returns, None))
        self._runs = runs
        if self.linked_run is not None and self.linked_run >= len(runs):
            self.linked_run = None

    # --- clicking words -------------------------------------------------

    @property
    def title_height(self) -> float:
        return self._title_h

    def word_at(self, pos: QPointF) -> str | None:
        """The identifier under pos (card coordinates), or None."""
        for x, y, text, _, _ in self._runs:
            if abs(pos.y() - y) > self._line_h / 2 or pos.x() < x:
                continue
            # Walk the characters: works for any font, monospace or not.
            right = x
            for index, ch in enumerate(text):
                right += self._metrics.horizontalAdvance(ch)
                if pos.x() < right:
                    for m in _WORD.finditer(text):
                        if m.start() <= index < m.end():
                            return m.group(0)
                    return None
        return None

    def set_highlight(self, word: str | None) -> None:
        self.highlight = word
        self.update()

    def highlight_count(self) -> int:
        """How many places the highlighted word is marked (0 when none)."""
        if not self.highlight:
            return 0
        pattern = _whole_word(self.highlight)
        return sum(len(pattern.findall(text)) for _, _, text, _, _ in self._runs)

    def _paint_highlight(self, painter: QPainter) -> None:
        if not self.highlight:
            return
        pattern = _whole_word(self.highlight)
        painter.setPen(QPen(_HIGHLIGHT_BORDER, 1))
        painter.setBrush(QBrush(_HIGHLIGHT_FILL))
        for x, y, text, _, _ in self._runs:
            for m in pattern.finditer(text):
                left = x + self._metrics.horizontalAdvance(text[:m.start()])
                w = self._metrics.horizontalAdvance(m.group(0))
                painter.drawRoundedRect(QRectF(left - 1.5, y - self._line_h / 2 + 1, w + 3, self._line_h - 2), 2, 2)

    # --- tie-ins with the code ----------------------------------------------

    def has_word(self, word: str) -> bool:
        pattern = _whole_word(word)
        return any(pattern.search(text) for _, _, text, _, _ in self._runs)

    def run_at(self, pos: QPointF) -> int | None:
        """The piece of text (index into _runs) at pos, if any - not the
        title, which drags the card. An output's label shares its height with
        the if that exits through it, so of those the one starting nearest
        left of pos wins."""
        best = None
        for index, (x, y, _, _, _) in enumerate(self._runs):
            if not index or abs(pos.y() - y) > self._line_h / 2:
                continue
            if best is None or (x <= pos.x() and (self._runs[best][0] > pos.x() or x > self._runs[best][0])):
                best = index
        return best

    def code_spans_at(self, pos: QPointF) -> list:
        """The code the line at pos stands for, for the code view to highlight."""
        index = self.run_at(pos)
        return [s for s in self._runs[index][3] if s is not None] if index is not None else []

    def show_code_link(self, line: int, col: int) -> bool:
        """Lights up the line on this card that the code at line, col belongs
        to: a declaration, if header or return clicked on directly, or else
        the innermost if whose body holds it. Returns whether one was found."""
        best, best_size = None, None
        for index, (_, _, _, spans, body) in enumerate(self._runs):
            if any(s is not None and s.contains(line, col) for s in spans):
                best = index  # clicked right on it: that wins
                break
            if body is not None and body.contains(line, col) and (best_size is None or body.size < best_size):
                best, best_size = index, body.size
        self.linked_run = best
        self.update()
        return best is not None

    def clear_code_link(self) -> None:
        self.linked_run = None
        self.update()

    def _paint_link(self, painter: QPainter) -> None:
        if self.linked_run is None:
            return
        y = self._runs[self.linked_run][1]
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_LINK_BAND))
        painter.drawRect(QRectF(1, y - self._line_h / 2, self._size[0] - 2, self._line_h))

    def _in_title(self, pos: QPointF) -> bool:
        return pos.y() <= self._title_h

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        input_name = self.input_at(event.pos()) if event.button() == Qt.LeftButton else None
        if input_name is not None:
            # A blue input dot: drag out a line; where it's dropped, a value
            # card appears (see CardCanvas.add_value_card).
            start = self.mapToScene(QPointF(0, next(y for y, _, n in self._inputs if n == input_name)))
            line = QGraphicsLineItem(start.x(), start.y(), start.x(), start.y())
            pen = QPen(_INPUT_PORT, 1.6, Qt.DashLine)
            pen.setCosmetic(True)
            line.setPen(pen)
            self.scene().addItem(line)
            self._input_drag = (input_name, line)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self._in_title(event.pos()):
            # Only the title bar moves the card.
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            # Anywhere else: select the card and mark the clicked word
            # everywhere in it (the same word again, or a blank spot, clears).
            self.scene().clearSelection()
            self.setSelected(True)
            word = self.word_at(event.pos())
            self.set_highlight(None if word == self.highlight else word)
            # Tie it to the code: light this line, and have the code view show
            # where it came from with the word marked.
            self.linked_run = self.run_at(event.pos())
            if self.on_clicked is not None:
                self.on_clicked(self, self.code_spans_at(event.pos()), self.highlight)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._input_drag is not None:
            line = self._input_drag[1]
            start = line.line().p1()
            line.setLine(start.x(), start.y(), event.scenePos().x(), event.scenePos().y())
            event.accept()
        elif self._dragging:
            super().mouseMoveEvent(event)
        else:
            event.accept()  # a press on the body never drags the card

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._input_drag is not None:
            name, line = self._input_drag
            self._input_drag = None
            self.scene().removeItem(line)
            if self.on_input_dropped is not None:
                self.on_input_dropped(self, name, event.scenePos())
            event.accept()
        elif self._dragging:
            self._dragging = False
            super().mouseReleaseEvent(event)
            self.setCursor(Qt.OpenHandCursor)
        else:
            event.accept()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.input_at(event.pos()) is not None:
            self.setCursor(Qt.CrossCursor)  # drag out a value card
        elif self._in_title(event.pos()):
            self.setCursor(Qt.OpenHandCursor)
        elif self.word_at(event.pos()):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # --- painting -------------------------------------------------------

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override
        width, height = self._size
        # +border, and the port dots that sit half outside the edges.
        return QRectF(-PORT_RADIUS - 1, -1, width + 2 * PORT_RADIUS + 2, height + 2)

    def _draw_spans(self, painter: QPainter, x: float, y_centre: float, spans) -> None:
        top = y_centre - self._line_h / 2
        for text, colour in spans:
            painter.setPen(colour)
            painter.drawText(QRectF(x, top, 10_000, self._line_h), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += self._metrics.horizontalAdvance(text)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        width, height = self._size
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(QPen(_SELECTED_BORDER if self.isSelected() else _BORDER, 1.5))
        painter.setBrush(QBrush(_CARD_BG))
        painter.drawRoundedRect(QRectF(0, 0, width, height), 6, 6)

        # Title bar, rounded on top only: a rounded rect plus a square one
        # covering its bottom corners when something sits under it.
        title_h = self._title_h
        has_body = height > title_h + 1
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_TITLE_BG))
        inset = QRectF(0.75, 0.75, width - 1.5, title_h)
        painter.drawRoundedRect(inset, 5, 5)
        if has_body:
            painter.drawRect(QRectF(inset.left(), title_h / 2, inset.width(), title_h / 2 + 0.75))
            painter.setPen(QPen(_BORDER, 1))
            painter.drawLine(QPointF(0, title_h), QPointF(width, title_h))

        self._paint_link(painter)       # under the text: the line tied to the code view
        self._paint_highlight(painter)
        painter.setFont(self._font)
        painter.setPen(FUNCTION_FORMAT.foreground().color())
        painter.drawText(QRectF(CARD_PADDING, CARD_PADDING / 2, width, self._line_h),
                         Qt.AlignLeft | Qt.AlignVCenter, self.function.name)

        y = self._variables_top + CARD_PADDING / 2 + self._line_h / 2
        for spans in self._variable_spans if self._show_variables else []:
            self._draw_spans(painter, CARD_PADDING, y, spans)
            y += self._line_h

        if not (self._inputs or self._rows):
            return
        if self._logic_top > title_h + 1:
            painter.setPen(QPen(_BORDER, 1))
            painter.drawLine(QPointF(0, self._logic_top), QPointF(width, self._logic_top))

        for y, spans, _ in self._inputs:
            self._draw_spans(painter, CARD_PADDING, y, spans)
            self._draw_port(painter, 0, y, _INPUT_PORT)

        for row in self._rows:
            if row.diamond:
                self._draw_diamond(painter, row.x - self._metrics.horizontalAdvance(" ") - 1, row.y)
            painter.setFont(self._italic if not row.diamond else self._font)
            self._draw_spans(painter, row.x, row.y, row.spans)
        painter.setFont(self._font)

        # Wires: row -> its output's lane, down the lane, lane -> port label.
        painter.setPen(QPen(_WIRE, 1.2))
        for name, port_y in self._ports.items():
            lane = self._lanes[name]
            exit_rows = [r for r in self._rows if r.exit == name]
            for row in exit_rows:
                painter.drawLine(QPointF(row.text_right + WIRE_GAP / 2, row.y), QPointF(lane, row.y))
            top = min(r.y for r in exit_rows)
            if port_y > top:
                painter.drawLine(QPointF(lane, top), QPointF(lane, port_y))
            painter.drawLine(QPointF(lane, port_y), QPointF(self._port_label_x - 4, port_y))
        for name, port_y in self._ports.items():
            self._draw_spans(painter, self._output_label_x[name], port_y, self._output_spans[name])
            self._draw_port(painter, width, port_y, _OUTPUT_PORT)

    @staticmethod
    def _draw_port(painter: QPainter, x: float, y: float, colour: QColor) -> None:
        painter.setPen(QPen(_CARD_BG, 1.5))
        painter.setBrush(QBrush(colour))
        painter.drawEllipse(QPointF(x, y), PORT_RADIUS, PORT_RADIUS)

    @staticmethod
    def _draw_diamond(painter: QPainter, x: float, y: float) -> None:
        s = 3.5
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_DIAMOND))
        painter.drawPolygon(QPolygonF([QPointF(x, y - s), QPointF(x + s, y), QPointF(x, y + s), QPointF(x - s, y)]))


class CardCanvas(QGraphicsView):
    """The canvas cards live on. Cards are added with add_card(); which ones
    show, and which of their sections do, follows apply_filters(). The
    scroll wheel zooms around the mouse pointer."""

    cards_changed = Signal(int)    # how many cards there are now
    # A line or word on a card was clicked: (card, code spans it stands for, word or None).
    card_clicked = Signal(object, object, object)
    zoom_changed = Signal(float)   # 1.0 = 100%

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("cardCanvas")
        self.setScene(QGraphicsScene(self))
        # A fixed, roomy area: zooming out and panning need space beyond the
        # cards themselves, and cards are placed from its top-left.
        self.scene().setSceneRect(QRectF(0, 0, SCENE_SIZE, SCENE_SIZE))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)  # drag empty canvas to pan
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)  # zoom toward the pointer
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._cards: dict[tuple[str, int], FunctionCard] = {}
        # Value cards and their wires, by (function card key, parameter name).
        self._value_cards: dict[tuple[tuple[str, int], str], tuple[ValueCard, Wire]] = {}
        self._filters = (True, True, True, True)
        # Where the next automatically placed card goes: left to right, then
        # a new row when it would pass the visible width.
        self._next = QPointF(CARD_GAP / 2, CARD_GAP / 2)
        self._row_height = 0.0

    @property
    def cards(self) -> list[FunctionCard]:
        return list(self._cards.values())

    def add_card(self, function: FunctionInfo, language: str | None) -> FunctionCard:
        """Adds a card for function, or selects its card if it already has one."""
        key = (function.name, function.line)
        card = self._cards.get(key)
        if card is None:
            card = FunctionCard(function, language)
            card.language = language
            card.on_moved = lambda c=card: self._update_wires(c)
            card.on_input_dropped = self.add_value_card
            card.on_clicked = lambda c, spans, word: self.card_clicked.emit(c, spans, word)
            self._cards[key] = card
            self.scene().addItem(card)
            self._apply_to(card)
            self._place(card)
            self.cards_changed.emit(len(self._cards))
        self.scene().clearSelection()
        card.setSelected(True)
        self.ensureVisible(card)
        return card

    def remove_card(self, card: FunctionCard) -> None:
        for value_card in self.value_cards_of(card):
            self.remove_value_card(value_card)
        self._cards.pop(card.key, None)
        self.scene().removeItem(card)
        self.cards_changed.emit(len(self._cards))

    # --- value cards ----------------------------------------------------------

    def card_at_code(self, line: int, col: int) -> FunctionCard | None:
        """The card for the function whose code holds line, col - the
        innermost, for a Python function nested in another."""
        around = [c for c in self.cards if c.function.span is not None and c.function.span.contains(line, col)]
        return min(around, key=lambda c: c.function.span.size) if around else None

    def value_cards_of(self, card: FunctionCard) -> list[ValueCard]:
        return [vc for (key, _), (vc, _) in self._value_cards.items() if key == card.key]

    def add_value_card(self, card: FunctionCard, name: str, drop: QPointF | None = None) -> ValueCard:
        """A value card for card's input name, its port at drop (canvas
        coordinates) - where the dragged dot was let go - or just left of the
        card. If the input already has one, that one is selected instead."""
        existing = self._value_cards.get((card.key, name))
        if existing is not None:
            value_card = existing[0]
        else:
            parameter = next(p for p in card.parameters if p.name == name)
            value_card = ValueCard(card, parameter, card.language)
            self.scene().addItem(value_card)
            port = value_card.port_local_pos()
            if drop is None:
                target = card.input_port_scene_pos(name) or card.scenePos()
                drop = QPointF(target.x() - 60, target.y())
            # Keep it on the canvas (it starts at 0,0).
            value_card.setPos(max(10.0, drop.x() - port.x()), max(10.0, drop.y() - port.y()))
            wire = Wire(value_card)
            self.scene().addItem(wire)
            value_card.on_moved = wire.update_path
            self._value_cards[(card.key, name)] = (value_card, wire)
            wire.update_path()
        self.scene().clearSelection()
        value_card.setSelected(True)
        value_card.editor.setFocus()
        return value_card

    def remove_value_card(self, value_card: ValueCard) -> None:
        key = (value_card.function_card.key, value_card.parameter.name)
        entry = self._value_cards.pop(key, None)
        if entry is not None:
            self.scene().removeItem(entry[1])
        self.scene().removeItem(value_card)
        value_card.function_card.set_input_value(value_card.parameter.name, None)

    def _update_wires(self, card: FunctionCard) -> None:
        """Re-routes card's wires after it moved or changed shape, and hides
        them (and their value cards) while its inputs aren't showing."""
        for (key, _), (value_card, wire) in self._value_cards.items():
            if key == card.key:
                shown = card.isVisible() and card.logic_shown
                value_card.setVisible(shown)
                wire.setVisible(shown)
                wire.update_path()

    def clear_cards(self) -> None:
        for value_card, wire in list(self._value_cards.values()):
            self.scene().removeItem(wire)
            self.scene().removeItem(value_card)
        self._value_cards.clear()
        for card in self.cards:
            self.scene().removeItem(card)
        self._cards.clear()
        self._next = QPointF(CARD_GAP / 2, CARD_GAP / 2)
        self._row_height = 0.0
        self.cards_changed.emit(0)

    def apply_filters(self, show_functions: bool, show_methods: bool, show_variables: bool,
                      show_logic: bool) -> None:
        """Matches the checkboxes: cards of an unticked kind hide, and the
        variables and logic sections show only when their boxes are ticked."""
        self._filters = (show_functions, show_methods, show_variables, show_logic)
        for card in self.cards:
            self._apply_to(card)

    def _kind_shown(self, card: FunctionCard) -> bool:
        show_functions, show_methods, _, _ = self._filters
        return show_functions if card.function.kind == FUNCTION else show_methods

    def _apply_to(self, card: FunctionCard) -> None:
        card.setVisible(self._kind_shown(card))
        card.set_sections(self._filters[2], self._filters[3])
        self._update_wires(card)

    def _place(self, card: FunctionCard) -> None:
        width, height = card.size
        visible_width = max(self.viewport().width() / self.zoom, 400)  # in canvas units
        if self._next.x() > CARD_GAP and self._next.x() + width > visible_width:
            self._next = QPointF(CARD_GAP / 2, self._next.y() + self._row_height + CARD_GAP)
            self._row_height = 0.0
        card.setPos(self._next)
        self._next = QPointF(self._next.x() + width + CARD_GAP, self._next.y())
        self._row_height = max(self._row_height, height)

    # --- zoom -------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, zoom: float) -> None:
        """Zooms to zoom (1.0 = 100%), kept within ZOOM_MIN..ZOOM_MAX, around
        the mouse pointer when it's over the canvas."""
        zoom = min(ZOOM_MAX, max(ZOOM_MIN, zoom))
        if abs(zoom - self.zoom) < 1e-6:
            return
        factor = zoom / self.zoom
        self.scale(factor, factor)
        self.zoom_changed.emit(self.zoom)

    def zoom_by(self, steps: float) -> None:
        """Zooms in (positive) or out by wheel-notch steps around the middle
        of the view - for menu commands, where the mouse isn't on the canvas."""
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.set_zoom(self.zoom * ZOOM_STEP ** steps)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        steps = event.angleDelta().y() / 120  # one notch = 120
        if steps:
            self.set_zoom(self.zoom * ZOOM_STEP ** steps)
        event.accept()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802 - Qt override
        painter.fillRect(rect, _CANVAS_BG)
        pen = QPen(_GRID_DOT, 1.5)
        pen.setCosmetic(True)  # same dot size at every zoom
        painter.setPen(pen)
        # Zoomed far out, double the spacing so the dots don't merge.
        step = GRID_STEP
        while step * self.zoom < 10:
            step *= 2
        left = int(rect.left()) - int(rect.left()) % step
        top = int(rect.top()) - int(rect.top()) % step
        points = [QPointF(x, y)
                  for x in range(left, int(rect.right()) + 1, step)
                  for y in range(top, int(rect.bottom()) + 1, step)]
        painter.drawPoints(points)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        item = self.itemAt(event.pos())
        if isinstance(item, QGraphicsProxyWidget):
            super().contextMenuEvent(event)  # a value card's text box: its own Cut/Copy/Paste menu
            return
        while item is not None and not isinstance(item, (FunctionCard, ValueCard)):
            item = item.parentItem()
        if item is None:
            return
        menu = QMenu(self)
        if isinstance(item, ValueCard):
            remove = menu.addAction("Remove Value Card")
            if menu.exec(event.globalPos()) is remove:
                self.remove_value_card(item)
            return
        remove = menu.addAction("Remove Card")
        if menu.exec(event.globalPos()) is remove:
            self.remove_card(item)
