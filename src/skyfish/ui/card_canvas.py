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
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsProxyWidget,
    QToolTip,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from skyfish.analysis.functions import FUNCTION, LOCAL, PARAMETER, FunctionInfo
from skyfish.analysis.evaluate import compute_expression
from skyfish.analysis.logic import Action, LogicRow, actions, logic_rows, outputs
from skyfish.analysis.simulate import simulate
from skyfish.analysis.stdlib_c import knows
from skyfish.ui.highlighter import FUNCTION as FUNCTION_FORMAT
from skyfish.ui.highlighter import colour_spans
from skyfish.ui.card_style import CLOSE_GAP, CLOSE_SIZE
from skyfish.ui.card_style import draw_close as _draw_close
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
_STEP_BAR_BG = QColor("#252525")
_BOX_BG = QColor("#191919")  # a call's black box
_STEP_ICON = QColor("#e0e0e0")
_STEP_ICON_OFF = QColor("#5a5a5a")
_OUTPUT_LIT = QColor("#35C053")              # the output a run leaves by
_OUTPUT_LIT_FILL = QColor(53, 192, 83, 60)
# Ctrl+click value overlay: the name faded under the card background, the value in white.
_OVERLAY_FADE = QColor(30, 30, 30, 215)
_OVERLAY_TEXT = QColor("#FFFFFF")
_STALE_CROSS = QColor(248, 100, 100, 190)  # a card whose function is gone from the code
_LINK_BAND = QColor(38, 79, 120, 150)  # the line tied to the code view - the code view's link blue  # "always", "otherwise", "in loop" ...

# Layout knobs (pixels at 100% zoom).
GRID_STEP = 20           # canvas dot spacing
CARD_PADDING = 10        # inside a card, around its text
CARD_MIN_WIDTH = 160
CARD_GAP = 40            # between cards placed automatically
SCENE_SIZE = 10_000      # the canvas area cards can be placed and panned in
ZOOM_MIN, ZOOM_MAX = 0.25, 3.0
ZOOM_STEP = 1.15         # per scroll-wheel notch
FIT_MARGIN = 40          # space around the cards when the view fits them
STEP_BAR_HEIGHT = 30     # the step-through bar under each card
STEP_BUTTON_WIDTH = 44
BOX_PAD = 8              # inside a call's box
BOX_GAP = 18             # between a box's inputs and outputs columns
BOX_MIN_COLUMN = 70      # narrowest a box column gets
MAX_ARG_CHARS = 40       # longer arguments end in "…"
OUT = "out"              # in a step path: the function's output, after its last card line
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


_IDENT = re.compile(r"[A-Za-z_]\w*")
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')  # a "string" in a card line


def _c_value(value) -> str:
    """A run's value written the way C would show it on a card."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.encode("unicode_escape").decode("ascii").replace('"', '\\"') + '"'
    if isinstance(value, (bytes, bytearray)):
        return "{" + ", ".join(f"0x{b:02X}" for b in value) + "}"
    return str(value)


@dataclass
class _Box:
    """A call or assignment line on the card. With a call it's a box: the
    call's name on top, its arguments down the left (with their values when
    known) and what it returns or changes down the right. Without a call
    (x = a + b) it's one line with its result on the right."""
    item: int             # index into the card's items
    action: Action
    x: float
    top: float
    width: float
    height: float
    left_w: float         # the arguments column
    title_y: float
    lines: list           # [(centre y, argument spans)] - with their values
    bare: list            # the same arguments without values, for lines the run hasn't reached
    outputs: list         # [(centre y, output spans)]
    is_box: bool          # a call: drawn boxed; else one line


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

    def __init__(self, function: FunctionInfo, language: str | None, stale: bool = False) -> None:
        super().__init__()
        self.function = function
        # Gone from the code (renamed or deleted) since the diagram was saved:
        # drawn from the saved code, crossed out, and no longer tied to the code.
        self.stale = stale
        self.saved_source = ""  # for a stale card, the code it was drawn from (saved again as is)
        self.on_close = None    # set by the canvas: the title bar's X was clicked
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
        # Everything on the card's logic, in code order: the if / return rows
        # and the calls and assignments between them (black boxes). The step-
        # through walks these. On the same line, the action comes first.
        self._actions = actions(function.flow) if function.has_body else []
        self._items = sorted([("row", r) for r in self.logic_rows] + [("action", a) for a in self._actions],
                             key=lambda item: (item[1].line, 0 if item[0] == "action" else 1))
        self._trace = None     # the latest run with the value cards' inputs (None = out of date)
        self._results: dict = {}  # action span -> (argument values, outputs), from that run
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
        self.on_values_changed = None  # set by the canvas: a value card's value changed
        # Inputs Ctrl+clicked to show their value over their uses on this card.
        self.value_overlays: set[str] = set()
        self.last_note = ""  # what the last Ctrl+click did, as shown at the pointer
        # Step-through (the bar under the card): set by the canvas, the input
        # values to run with ({name: value}, None for NULL) and the file's
        # #define values. step_pos is where you are on the path - None before
        # you start; the path is the card lines a run passes, ending with OUT
        # (its output) - see _step_path().
        self.inputs_provider = None
        self.defines: dict = {}
        self.step_pos: int | None = None
        self._step_path: list | None = None  # worked out when needed; None = out of date
        self.step_note = ""                   # why the run can't go on, when it can't
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
        # New inputs, new run: if you were stepping, start again at the front.
        self._step_path = None
        self._trace = None
        if self.step_pos is not None:
            self.step_to(0)
        self.prepareGeometryChange()
        self._layout()
        self.update()
        self._moved()
        if self.on_values_changed is not None:
            self.on_values_changed()

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
        self._boxes = []
        self._item_y = {}
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
            self._boxes: list[_Box] = []
            self._item_y: dict[int, object] = {}  # item index -> its _Row or _Box
            self._run_results()
            for index, (kind, obj) in enumerate(self._items):
                if kind == "action":
                    box = self._layout_box(index, obj, y, indent_w, marker_w)
                    self._boxes.append(box)
                    self._item_y[index] = box
                    content_right = max(content_right, box.x + box.width)
                    y = box.top + box.height + 4
                    continue
                row = obj
                x = CARD_PADDING + row.indent * indent_w + marker_w
                if not row.is_branch:  # always / otherwise / "in loop": quiet text
                    spans = [(row.keyword, _QUIET_TEXT)]
                else:
                    label = row.keyword + (" " + _shorten(row.condition, MAX_CONDITION_CHARS) if row.condition else "")
                    spans = colour_spans(label, self._language)
                text_right = x + self._width_of(spans)
                self._rows.append(_Row(y + line_h / 2, x, spans, text_right, row.exit,
                                       row.is_branch, row))
                self._item_y[index] = self._rows[-1]
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

        # Room on the title bar for the name and the close X.
        title_right = CARD_PADDING + self._metrics.horizontalAdvance(self.function.name) + CLOSE_GAP + CLOSE_SIZE
        width = max(CARD_MIN_WIDTH, content_right + CARD_PADDING, title_right + CARD_PADDING)
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
        self._item_runs: dict[int, int] = {}  # item index -> its first run
        self._run_boxes: dict[int, _Box] = {}  # run index -> the box it's in
        for index in sorted(self._item_y):
            thing = self._item_y[index]
            self._item_runs[index] = len(runs)
            if isinstance(thing, _Box):
                head = thing.action.call if thing.is_box else text(thing.lines[0][1])
                self._run_boxes[len(runs)] = thing
                runs.append((thing.x + BOX_PAD if thing.is_box else thing.x, thing.title_y, head,
                             [thing.action.span], None))
                for line_y, spans in (thing.lines if thing.is_box else []):
                    self._run_boxes[len(runs)] = thing
                    runs.append((thing.x + BOX_PAD, line_y, text(spans), [thing.action.span], None))
                continue
            r = thing
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
        if self.linked_run is None or self.linked_run in self._run_boxes:
            return  # a call / assignment line lights itself (see _paint_boxes)
        y = self._runs[self.linked_run][1]
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_LINK_BAND))
        painter.drawRect(QRectF(1, y - self._line_h / 2, self._size[0] - 2, self._line_h))

    # --- the run behind the card: black-box results -------------------------------------

    def refresh_run(self) -> None:
        """Runs again (the canvas calls this once it has given the card its
        inputs and #defines) and redraws."""
        self._trace = None
        self._step_path = None
        self.prepareGeometryChange()
        self._layout()
        self.update()

    def _run_results(self):
        """The run with today's inputs, and what each call / assignment did
        in it: {action span: ([(argument, its value or None)], [outputs])}.
        Arguments are valued as they stood when that line ran."""
        if self._trace is None:
            inputs = self.inputs_provider() if self.inputs_provider is not None else {}
            self._trace = simulate(self.function, inputs, self.defines)
            types = {v.name: v.type for v in self.function.variables if v.type}
            state = {**self.defines, **inputs}
            self._results = {}
            for kind, action in self._items:
                if kind != "action":
                    continue
                step = self._trace.steps.get(action.span)
                values = []
                for arg in action.args:
                    computed = compute_expression(arg, state, types)
                    shown = _c_value(computed.value) if computed.known else None
                    if shown is not None and shown.strip('"') == arg.strip('"'):
                        shown = None  # a literal: its value is what's written
                    values.append((arg, shown))
                outs = []
                if step is not None and step.status == "ran":
                    if action.target:
                        if action.target in step.changes:
                            outs.append(f"{action.target} = {_c_value(step.changes[action.target])}")
                    else:
                        if step.known:
                            outs.append(f"returns {_c_value(step.returned)}")
                        outs += [f"{k} ← {_c_value(v)}" for k, v in step.changes.items()]
                    state.update({k: v for k, v in step.changes.items() if _IDENT.fullmatch(k)})
                elif action.call and (not knows(action.call) or (step is not None and step.unknown_call)):
                    outs.append("?")  # not standard C: a black box we can't see into yet
                self._results[action.span] = (values, outs)
        return self._trace

    def _layout_box(self, index: int, action: Action, y: float, indent_w: float, marker_w: float) -> _Box:
        line_h = self._line_h
        # Indented under the if it sits in.
        indent = max((r.indent + 1 for r in self.logic_rows
                      if r.body_span is not None and action.span is not None
                      and r.body_span.contains(action.span.line, action.span.col)), default=0)
        x = CARD_PADDING + indent * indent_w + marker_w
        values, outs = self._results.get(action.span, ([], []))
        out_spans = [colour_spans(o, self._language) if o != "?" else [("?", _QUIET_TEXT)] for o in outs]
        if not action.call:
            # x = a + b: one line, its result on the right.
            spans = colour_spans(_shorten(action.text, MAX_CONDITION_CHARS), self._language)
            result = [colour_spans("= " + o.split(" = ", 1)[1], self._language) if " = " in o else colour_spans(o, self._language)
                      for o in outs]
            left_w = self._width_of(spans)
            right_w = max((self._width_of(r) for r in result), default=0)
            width = left_w + (BOX_GAP + right_w if result else 0)
            centre = y + line_h / 2
            return _Box(index, action, x, y, width, line_h, left_w, centre, [(centre, spans)], [(centre, spans)],
                        [(centre, r) for r in result[:1]], False)
        arg_spans, bare_spans = [], []
        for arg, value in values:
            spans = colour_spans(_shorten(arg, MAX_ARG_CHARS), self._language)
            bare_spans.append(spans)
            if value is not None:
                spans = [*spans, (" = ", _QUIET_TEXT), *colour_spans(_shorten(value, MAX_INPUT_VALUE_CHARS), self._language)]
            arg_spans.append(spans)
        left_w = max([self._metrics.horizontalAdvance(action.call), BOX_MIN_COLUMN,
                      *(self._width_of(sp) for sp in arg_spans)])
        right_w = max([BOX_MIN_COLUMN, *(self._width_of(sp) for sp in out_spans)])
        lines = max(len(arg_spans), len(out_spans), 1)
        top = y + 2
        height = line_h + lines * line_h + BOX_PAD
        title_y = top + line_h / 2 + 2
        first = top + line_h + 2 + line_h / 2
        return _Box(index, action, x, top, BOX_PAD + left_w + BOX_GAP + right_w + BOX_PAD, height, left_w, title_y,
                    [(first + i * line_h, sp) for i, sp in enumerate(arg_spans)],
                    [(first + i * line_h, sp) for i, sp in enumerate(bare_spans)],
                    [(first + i * line_h, sp) for i, sp in enumerate(out_spans)], True)

    def _box_shows_result(self, box: _Box) -> bool:
        """Not stepping: every result shows. Stepping: a line's result shows
        once you've stepped past it, like an if."""
        if not self.stepping:
            return True
        return box.item in self.step_path()[:self.step_pos]

    def _box_reached(self, box: _Box) -> bool:
        """Not stepping, or the run has got to this line (the current one counts)."""
        if not self.stepping:
            return True
        return box.item in self.step_path()[:self.step_pos + 1]

    def _paint_boxes(self, painter: QPainter) -> None:
        current = self.step_path()[self.step_pos] if self.stepping else None
        for box in self._boxes:
            lit = box.item == current or (self.linked_run is not None and self._run_boxes.get(self.linked_run) is box)
            if box.is_box:
                rect = QRectF(box.x, box.top, box.width, box.height)
                painter.setPen(QPen(_SELECTED_BORDER if lit else _BORDER, 1.2))
                painter.setBrush(QBrush(_LINK_BAND if lit else _BOX_BG))
                painter.drawRoundedRect(rect, 4, 4)
                painter.setPen(QPen(_BORDER, 1))
                divider = box.x + BOX_PAD + box.left_w + BOX_GAP / 2
                painter.drawLine(QPointF(box.x, box.top + self._line_h + 2), QPointF(box.x + box.width, box.top + self._line_h + 2))
                painter.drawLine(QPointF(divider, box.top + self._line_h + 2), QPointF(divider, box.top + box.height))
                painter.setFont(self._font)
                painter.setPen(FUNCTION_FORMAT.foreground().color())
                painter.drawText(QRectF(box.x + BOX_PAD, box.title_y - self._line_h / 2, box.width, self._line_h),
                                 Qt.AlignLeft | Qt.AlignVCenter, box.action.call)
                # Arguments get their values once the run has reached this line.
                for line_y, spans in (box.lines if self._box_reached(box) else box.bare):
                    self._draw_spans(painter, box.x + BOX_PAD, line_y, spans)
                out_x = divider + BOX_GAP / 2
            else:
                if lit:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(_LINK_BAND))
                    painter.drawRect(QRectF(1, box.top, self._size[0] - 2, self._line_h))
                self._draw_spans(painter, box.x, box.title_y, box.lines[0][1])
                out_x = box.x + box.left_w + BOX_GAP
            if self._box_shows_result(box):
                for line_y, spans in box.outputs:
                    self._draw_spans(painter, out_x, line_y, spans)

    # --- step-through ---------------------------------------------------------------

    def step_path(self) -> list:
        """The card lines a run passes with today's inputs, in order (indexes
        into the card's items - if / return rows and call / assignment lines),
        ending with OUT when it reaches an output. An if is
        only decided when you step past it, so it's on the path whether it
        was taken or not; lines it skipped aren't. A line the run can't
        decide (an input with no value, a name from a header) ends the path
        there, and step_note says what it needs."""
        if self._step_path is None:
            self._step_path, self.step_note = [], ""
            trace = self._run_results()
            for index, (kind, row) in enumerate(self._items):
                step = trace.steps.get(row.span)
                if step is None or step.status == "skipped":
                    continue
                self._step_path.append(index)
                if step.status == "unknown":
                    blockers = ", ".join(step.unresolved) or "a value it doesn't have"
                    self.step_note = f"can't tell from here - needs {blockers}"
                    break
                if kind == "row" and step.status == "taken" and row.exit is not None:
                    self._step_path.append(OUT)  # it leaves through its output
                    break
            self._step_output = trace.output
        return self._step_path

    @property
    def stepping(self) -> bool:
        return self.step_pos is not None

    def step_to(self, position: int) -> None:
        """Moves to position on the path (clamped), lights that card line
        and shows its code - or, at the end, lights the output it leaves by."""
        path = self.step_path()
        if not path:
            self.step_pos = None
            self.update()
            return
        self.step_pos = max(0, min(position, len(path) - 1))
        here = path[self.step_pos]
        if here == OUT:
            # The return this run actually left by: the one on the card line before OUT.
            self.linked_run = None
            exit_row = self._items[path[self.step_pos - 1]][1] if self.step_pos > 0 else None
            spans = [exit_row.exit_span] if exit_row is not None and exit_row.exit_span else []
        else:
            self.linked_run = self._item_runs.get(here)
            spans = [s for s in self._runs[self.linked_run][3] if s is not None] if self.linked_run is not None else []
        self.update()
        if self.on_clicked is not None:
            self.on_clicked(self, spans, None)  # the code view shows where the run is

    def step_forward(self) -> None:
        self.step_to(0 if self.step_pos is None else self.step_pos + 1)

    def step_back(self) -> None:
        self.step_to(0 if self.step_pos is None else self.step_pos - 1)

    def step_to_end(self) -> None:
        self.step_to(len(self.step_path()) - 1)

    def step_to_start(self) -> None:
        self.step_to(0)

    @property
    def step_output(self) -> str | None:
        """The output the run reached - lit when you've stepped to it."""
        self.step_path()
        return self._step_output if self._step_path and self._step_path[-1] == OUT else None

    @property
    def at_output(self) -> bool:
        path = self.step_path() if self.stepping else []
        return bool(path) and path[self.step_pos] == OUT

    def _first_logic_run(self) -> int:
        shown_locals = len(self._locals) if self._show_variables else 0
        return 1 + shown_locals + len(self._inputs)  # runs: title, variables, inputs, then logic rows

    def has_step_bar(self) -> bool:
        return not self.stale and self._show_logic and bool(self._items)

    def step_bar_rect(self) -> QRectF:
        width, height = self._size
        return QRectF(0, height, width, STEP_BAR_HEIGHT)

    def step_buttons(self) -> list[tuple[str, QRectF]]:
        """(action, rect) for the four buttons, centred on the bar."""
        bar = self.step_bar_rect()
        left = bar.center().x() - 2 * STEP_BUTTON_WIDTH
        return [(name, QRectF(left + i * STEP_BUTTON_WIDTH, bar.top() + 3, STEP_BUTTON_WIDTH, bar.height() - 6))
                for i, name in enumerate(("start", "back", "forward", "end"))]

    def _paint_step_bar(self, painter: QPainter) -> None:
        bar = self.step_bar_rect()
        painter.setPen(QPen(_BORDER, 1.2))
        painter.setBrush(QBrush(_STEP_BAR_BG))
        painter.drawRoundedRect(bar.adjusted(0.5, 0, -0.5, 0), 5, 5)
        path = self.step_path() if self.stepping else []
        can_back = self.stepping and self.step_pos > 0
        can_forward = not self.stepping or self.step_pos < len(path) - 1
        enabled = {"start": self.stepping, "back": can_back, "forward": can_forward, "end": can_forward}
        for name, rect in self.step_buttons():
            painter.setPen(QPen(_BORDER, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            pen = QPen(_STEP_ICON if enabled[name] else _STEP_ICON_OFF, 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            c, s = rect.center(), 5.0
            left_chevron = [QPointF(c.x() + s / 2, c.y() - s), QPointF(c.x() - s / 2, c.y()), QPointF(c.x() + s / 2, c.y() + s)]
            right_chevron = [QPointF(c.x() - s / 2, c.y() - s), QPointF(c.x() + s / 2, c.y()), QPointF(c.x() - s / 2, c.y() + s)]
            if name in ("start", "back"):
                painter.drawPolyline(left_chevron)
            else:
                painter.drawPolyline(right_chevron)
            if name == "start":
                painter.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() - s, c.y() + s))
            if name == "end":
                painter.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        if self.stepping and self.step_note and path and path[self.step_pos] != OUT and self.step_pos == len(path) - 1:
            # Stuck: say why, at the left of the bar.
            painter.setFont(self._italic)
            painter.setPen(_QUIET_TEXT)
            room = self.step_buttons()[0][1].left() - bar.left() - CARD_PADDING
            text = self._metrics.elidedText(self.step_note, Qt.ElideRight, room)
            painter.drawText(QRectF(bar.left() + CARD_PADDING / 2, bar.top(), room, bar.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)

    def _paint_step_output(self, painter: QPainter) -> None:
        """At the end of a run: the output it leaves by, lit."""
        if not self.at_output or self.step_output not in self._ports:
            return
        name = self.step_output
        y = self._ports[name]
        x = self._output_label_x[name]
        w = self._width_of(self._output_spans[name])
        painter.setPen(QPen(_OUTPUT_LIT, 1.5))
        painter.setBrush(QBrush(_OUTPUT_LIT_FILL))
        painter.drawRoundedRect(QRectF(x - 4, y - self._line_h / 2, w + 8, self._line_h), 3, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_OUTPUT_LIT))
        painter.drawEllipse(QPointF(self._size[0], y), PORT_RADIUS + 2.5, PORT_RADIUS + 2.5)

    def _in_title(self, pos: QPointF) -> bool:
        return pos.y() <= self._title_h

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton and self.close_rect().contains(event.pos()):
            if self.on_close is not None:
                self.on_close(self)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.has_step_bar() and self.step_bar_rect().contains(event.pos()):
            for name, rect in self.step_buttons():
                if rect.contains(event.pos()):
                    {"start": self.step_to_start, "back": self.step_back,
                     "forward": self.step_forward, "end": self.step_to_end}[name]()
            event.accept()
            return
        if (event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier
                and not self._in_title(event.pos()) and self.input_at(event.pos()) is None):
            # Ctrl+click a name: show (or hide) its value over its uses on the card.
            self.last_note = self.toggle_value_overlay(self.word_at(event.pos()))
            QToolTip.showText(QCursor.pos(), self.last_note)
            event.accept()
            return
        # A crossed-out card's inputs can't take new value cards: its code is gone.
        input_name = (self.input_at(event.pos())
                      if event.button() == Qt.LeftButton and not self.stale else None)
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
            if self.on_clicked is not None and not self.stale:  # its code is gone
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
        for _, rect, value in self._overlay_boxes():
            if rect.contains(event.pos()):
                QToolTip.showText(QCursor.pos(), value)  # the whole value, when it's cut short
                break
        if self.close_rect().contains(event.pos()) or (
                self.has_step_bar() and any(r.contains(event.pos()) for _, r in self.step_buttons())):
            self.setCursor(Qt.PointingHandCursor)
        elif not self.stale and self.input_at(event.pos()) is not None:
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
        if self.has_step_bar():
            height += STEP_BAR_HEIGHT  # the step-through bar hangs under the card
        # +border, and the port dots that sit half outside the edges (bigger when lit).
        return QRectF(-PORT_RADIUS - 4, -1, width + 2 * PORT_RADIUS + 8, height + 2)

    def _draw_spans(self, painter: QPainter, x: float, y_centre: float, spans) -> None:
        top = y_centre - self._line_h / 2
        for text, colour in spans:
            painter.setPen(colour)
            painter.drawText(QRectF(x, top, 10_000, self._line_h), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += self._metrics.horizontalAdvance(text)

    def close_rect(self) -> QRectF:
        """The close X at the right end of the title bar."""
        return QRectF(self._size[0] - CARD_PADDING - CLOSE_SIZE, (self._title_h - CLOSE_SIZE) / 2,
                      CLOSE_SIZE, CLOSE_SIZE)

    # --- Ctrl+click value overlays ------------------------------------------------

    def toggle_value_overlay(self, word: str | None) -> str:
        """Shows (or hides) an input's value over every use of it on this
        card, and says what happened - or why nothing did."""
        if not word:
            return "Ctrl+click an input's name to show its value here"
        if word in self.value_overlays:
            self.value_overlays.discard(word)
            self.update()
            return f"{word}: value hidden"
        if word not in {p.name for p in self.parameters}:
            return f"{word}: not an input - only inputs with a value card show a value"
        if word not in self.input_values:
            return f"{word}: no value yet - drag its blue dot out and type one"
        self.value_overlays.add(word)
        self.update()
        uses = sum(1 for name, _, _ in self._overlay_boxes() if name == word)
        return (f"{word} = {self.input_values[word]}  (shown on {uses} use{'' if uses == 1 else 's'}; "
                "Ctrl+click again to hide)")

    def _overlay_boxes(self):
        """(name, rect, value) for every use on the card of an input whose
        value is showing. Its own input row already reads "= value", and the
        title and variable list aren't uses, so those are left alone."""
        shown_locals = len(self._locals) if self._show_variables else 0
        first_use = 1 + shown_locals + len(self._inputs)  # runs: title, variables, inputs, then logic + outputs
        for name in sorted(self.value_overlays):
            value = self.input_values.get(name)
            if value is None:
                continue  # its value card was cleared or closed: nothing to show
            pattern = _whole_word(name)
            for x, y, text, _, _ in self._runs[first_use:]:
                strings = [m.span() for m in _STRING_LITERAL.finditer(text)]
                for m in pattern.finditer(text):
                    if any(a <= m.start() < b for a, b in strings):
                        continue  # inside "quotes": text, not the variable
                    left = x + self._metrics.horizontalAdvance(text[:m.start()])
                    width = self._metrics.horizontalAdvance(m.group(0))
                    yield name, QRectF(left - 1, y - self._line_h / 2 + 1, width + 2, self._line_h - 2), value

    def _paint_value_overlays(self, painter: QPainter) -> None:
        painter.setFont(self._font)
        for _, rect, value in self._overlay_boxes():
            # The name faded but still faintly there, the value on top in white,
            # cut to the name's width (hover shows it all).
            painter.fillRect(rect, _OVERLAY_FADE)
            painter.setPen(_OVERLAY_TEXT)
            shown = self._metrics.elidedText(value, Qt.ElideRight, max(rect.width(), self._metrics.horizontalAdvance("…")))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, shown)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        self._paint_card(painter)
        self._paint_value_overlays(painter)
        self._paint_step_output(painter)
        if self.has_step_bar():
            self._paint_step_bar(painter)
        _draw_close(painter, self.close_rect())
        if self.stale:
            # Gone from the code: a big X across everything but the title bar.
            width, height = self._size
            if height > self._title_h + 1:
                pen = QPen(_STALE_CROSS, 2.5)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(4, self._title_h + 4), QPointF(width - 4, height - 4))
                painter.drawLine(QPointF(width - 4, self._title_h + 4), QPointF(4, height - 4))

    def _paint_card(self, painter: QPainter) -> None:
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

        self._paint_boxes(painter)
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
    # A value typed into a value card changed (the code view's overlays redraw).
    values_changed = Signal()
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
        self._fit_until_moved = False  # keep fitting the cards on resize until you zoom or pan
        self.defines: dict = {}  # the open file's #define values, for step-throughs (set by the window)
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

    def add_card(self, function: FunctionInfo, language: str | None, stale: bool = False) -> FunctionCard:
        """Adds a card for function, or selects its card if it already has one."""
        key = (function.name, function.line)
        card = self._cards.get(key)
        if card is None:
            card = FunctionCard(function, language, stale)
            card.on_close = self.remove_card
            card.language = language
            card.on_moved = lambda c=card: self._update_wires(c)
            card.on_input_dropped = self.add_value_card
            card.on_clicked = lambda c, spans, word: self.card_clicked.emit(c, spans, word)
            card.on_values_changed = self.values_changed.emit
            card.inputs_provider = lambda c=card: self.input_values_of(c)
            card.defines = self.defines
            card.refresh_run()
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

    def card_for(self, function: FunctionInfo) -> FunctionCard | None:
        """The card showing function, if it has one."""
        card = self._cards.get((function.name, function.line))
        return card if card is not None and not card.stale else None

    def card_at_code(self, line: int, col: int) -> FunctionCard | None:
        """The card for the function whose code holds line, col - the
        innermost, for a Python function nested in another."""
        around = [c for c in self.cards
                  if not c.stale and c.function.span is not None and c.function.span.contains(line, col)]
        return min(around, key=lambda c: c.function.span.size) if around else None

    def input_values_of(self, card: FunctionCard) -> dict:
        """{input name: value} from card's value cards that have a good value
        (None for NULL) - what a step-through runs with."""
        return {v.parameter.name: (None if v.is_null else v.value)
                for v in self.value_cards_of(card) if v.has_value}

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
            value_card.on_close = self.remove_value_card
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
        height += STEP_BAR_HEIGHT if card.has_step_bar() else 0
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

    def cards_rect(self) -> QRectF:
        """The area the showing cards and value cards cover (empty when there are none)."""
        rect = QRectF()
        for card in self.cards:
            if card.isVisible():
                rect = rect.united(card.sceneBoundingRect())
        for value_card, _ in self._value_cards.values():
            if value_card.isVisible():
                rect = rect.united(value_card.sceneBoundingRect())
        return rect

    def fit_cards(self) -> None:
        """Zooms and scrolls so every card is in view with a margin - never
        past 100%, so one small card isn't blown up. Until you zoom or pan
        yourself, it fits again when the canvas changes size (the window
        maximizing at start-up), so the cards stay in view."""
        self._fit_until_moved = True
        self._fit()

    def _fit(self) -> None:
        rect = self.cards_rect()
        if rect.isEmpty():
            return
        rect = rect.adjusted(-FIT_MARGIN, -FIT_MARGIN, FIT_MARGIN, FIT_MARGIN)
        view = self.viewport().rect()
        zoom = min(view.width() / rect.width(), view.height() / rect.height(), 1.0)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.set_zoom(zoom)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.centerOn(rect.center())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._fit_until_moved:
            self._fit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._fit_until_moved = False  # you've taken over the view
        super().mousePressEvent(event)

    def reset_zoom(self) -> None:
        """Back to 100% (the 100% button and View > Reset Zoom)."""
        self._fit_until_moved = False
        self.set_zoom(1.0)

    def zoom_by(self, steps: float) -> None:
        """Zooms in (positive) or out by wheel-notch steps around the middle
        of the view - for menu commands, where the mouse isn't on the canvas."""
        self._fit_until_moved = False  # you've taken over the view
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.set_zoom(self.zoom * ZOOM_STEP ** steps)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._fit_until_moved = False
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
