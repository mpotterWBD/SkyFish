import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QSplitter

from skyfish.ui import highlighter as hl
from skyfish.ui.main_window import MainWindow
from skyfish.ui.spans_delegate import SPANS_ROLE
from skyfish.version import APP_VERSION


@pytest.fixture
def make_window(tmp_path):
    """Builds MainWindows that remember files in a throwaway .ini, never the
    real per-user settings. Windows made by one test share that .ini."""
    windows = []

    def make() -> MainWindow:
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        window = MainWindow(settings=settings)
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.close()


def _add(window, name, has_body=True):
    """Adds the named function's card, as right-click > Add Card in the code does."""
    function = next(f for f in window._functions if f.name == name and f.has_body == has_body)
    window.add_function_card(function)
    return {c.function.name: c for c in window.cards.cards}.get(name)


def test_title_shows_version(make_window):
    assert f"v{APP_VERSION}" in make_window().windowTitle()


def test_viewer_left_analytics_right_and_read_only(make_window):
    window = make_window()
    splitter = window.centralWidget()
    assert isinstance(splitter, QSplitter)
    # Left column (header over code | list), then analysis at full height.
    assert splitter.widget(0).isAncestorOf(window.code_splitter)
    assert splitter.widget(1) is window.analytics_panel
    assert window.code_splitter.widget(0) is window.viewer
    assert window.code_splitter.widget(1) is window.functions_list
    assert window.viewer.isReadOnly()


def test_open_file_shows_code_and_summary(make_window, tmp_path):
    path = tmp_path / "demo.py"
    path.write_text("def f(a):\n    b = 1\n\nclass C:\n    def m(self, x):\n        MAX_N = x\n")
    window = make_window()
    window.open_file(path)
    assert window.viewer.toPlainText() == path.read_text()
    assert "demo.py" in window.windowTitle()
    # Locals only - parameters a and x aren't counted.
    assert window.message.text() == "1 function · 1 method · 2 variables in demo.py"


def test_c_summary_counts_prototypes_separately(make_window, tmp_path):
    path = tmp_path / "a.c"
    path.write_text("static void f(void);\n\nstatic void f(void) {\n    uint8_t x;\n}\n")
    window = make_window()
    window.open_file(path)
    assert window.message.text() == "1 function · 0 methods · 1 declaration · 1 variable in a.c"


def test_unsupported_file_still_shows_code(make_window, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello\n")
    window = make_window()
    window.open_file(path)
    assert window.viewer.toPlainText() == "hello\n"
    assert window._functions == []
    assert "isn't supported yet" in window.message.text()


def test_reopens_last_file(make_window, tmp_path):
    path = tmp_path / "last.c"
    path.write_text("int main(void) { return 0; }\n")
    make_window().open_file(path)

    window = make_window()  # like the next launch
    window.reopen_last_file()
    assert window.current_file == path
    assert [f.name for f in window._functions] == ["main"]


def test_missing_last_file_is_skipped(make_window, tmp_path):
    path = tmp_path / "gone.py"
    path.write_text("x = 1\n")
    make_window().open_file(path)
    path.unlink()

    window = make_window()
    window.reopen_last_file()
    assert window.current_file is None


def test_divider_position_is_remembered(make_window, tmp_path):
    window = make_window()
    window.resize(1600, 800)
    window.show()
    total = sum(window.splitter.sizes())
    # setSizes() doesn't emit splitterMoved, so emit it as a mouse drag would.
    window.splitter.setSizes([700, total - 700])
    window.code_splitter.setSizes([480, 200])
    window.splitter.splitterMoved.emit(700, 1)

    # Opening a file leaves the dividers where they were dragged.
    path = tmp_path / "a.py"
    path.write_text("x = '" + "y" * 500 + "'\n")
    window.open_file(path)
    assert window.splitter.sizes()[0] == 700

    # Next launch, in a bigger window: same left column, analysis gets the extra.
    window2 = make_window()
    window2.resize(1900, 800)
    window2.show()
    assert window2.splitter.sizes()[0] == 700
    assert window2.splitter.sizes()[1] > total - 700
    assert window2.code_splitter.sizes()[1] == 200  # the list keeps its width too


def test_checkbox_choices_are_remembered(make_window):
    window = make_window()
    window.show_methods.setChecked(False)
    window.show_variables.setChecked(True)
    window.show_logic.setChecked(False)

    window2 = make_window()
    assert window2.show_functions.isChecked()
    assert not window2.show_methods.isChecked()
    assert window2.show_variables.isChecked()
    assert not window2.show_logic.isChecked()


def test_cards_follow_checkboxes(make_window, tmp_path):
    path = tmp_path / "demo.py"
    path.write_text("def f(a):\n    b = 1\n    c = 2\n\nclass C:\n    def m(self):\n        d = 3\n")
    window = make_window()
    window.show_variables.setChecked(True)
    window.open_file(path)

    _add(window, "f")
    _add(window, "f")  # again: selects the existing card, no duplicate
    _add(window, "C.m")
    cards = {card.function.name: card for card in window.cards.cards}
    assert sorted(cards) == ["C.m", "f"]
    assert cards["f"].variable_line_count == 2  # locals b and c, not parameter a
    # Placed side by side, not on top of each other.
    assert cards["C.m"].pos().x() > cards["f"].pos().x() + cards["f"].size[0]

    # Variables unticked: the card loses its variable lines.
    height_with = cards["f"].size[1]
    window.show_variables.setChecked(False)
    assert cards["f"].variable_line_count == 0
    assert cards["f"].size[1] < height_with

    # Methods unticked: method cards hide.
    window.show_methods.setChecked(False)
    assert cards["f"].isVisible() and not cards["C.m"].isVisible()

    window.cards.remove_card(cards["f"])
    assert [c.function.name for c in window.cards.cards] == ["C.m"]

    # Opening another file starts with an empty canvas.
    window.open_file(path)
    assert window.cards.cards == []


def test_card_variables_written_and_coloured_as_declared(make_window, tmp_path):
    path = tmp_path / "a.c"
    path.write_text("void f(void) {\n    static const char *verdict;\n    uint8_t buf[2] = {0};\n}\n")
    window = make_window()
    window.show_variables.setChecked(True)
    window.open_file(path)
    card = _add(window, "f")
    lines = ["".join(t for t, _ in spans) for spans in card._variable_spans]
    assert lines == ["static const char *verdict", "uint8_t buf[2]"]
    spans = card._variable_spans[0]
    assert spans[:3] == [("static", hl.DECLARE.foreground().color()), (" ", hl.PLAIN_COLOR),
                         ("const", hl.DECLARE.foreground().color())]
    assert ("char", hl.TYPE.foreground().color()) in spans


def test_add_card_on_prototype_uses_definition(make_window, tmp_path):
    path = tmp_path / "a.c"
    path.write_text("static void f(void);\n\nstatic void f(void) {\n    uint8_t x;\n}\n")
    window = make_window()
    window.show_variables.setChecked(True)
    window.open_file(path)
    _add(window, "f", has_body=False)  # the prototype
    _add(window, "f")                  # the definition: same card
    [card] = window.cards.cards
    assert card.function.has_body and card.function.line == 3
    assert card.variable_line_count == 1


def test_card_logic_section(make_window, tmp_path):
    path = tmp_path / "a.c"
    path.write_text(
        "static uint8_t f(const char *line) {\n"
        "    if (strcmp(line, \"x\") == 0) { return 1U; }\n"
        "    if (strcmp(line, \"y\") == 0) { return 1U; }\n"
        "    return 0U;\n"
        "}\n"
    )
    window = make_window()
    window.open_file(path)
    card = _add(window, "f")
    assert card.outputs == ["1U", "0U"]  # the two "return 1U" share one output
    assert [r.keyword for r in card.logic_rows] == ["if", "if", "otherwise"]
    assert card.logic_shown

    height_with = card.size[1]
    window.show_logic.setChecked(False)
    assert not card.logic_shown
    assert card.size[1] < height_with


def _card_for(window, tmp_path, source, suffix=".c"):
    path = tmp_path / f"card{suffix}"
    path.write_text(source)
    window.show_variables.setChecked(True)
    window.open_file(path)
    window.add_function_card(next(f for f in window._functions if f.has_body))
    return window.cards.cards[0]

CARD_SOURCE = (
    "static uint8_t take(char *out, uint8_t out_size) {\n"
    "    uint8_t pending;\n"
    "    if (pending) { out[out_size - 1U] = 0; }\n"
    "    if (s_line_buf[0]) { return pending; }\n"
    "    return 0U;\n"
    "}\n"
)


def _point_on(card, word, occurrence=0):
    """Card coordinates of the middle of the given occurrence of word."""
    from PySide6.QtCore import QPointF
    from skyfish.ui.card_canvas import _whole_word
    hits = []
    for x, y, text, _, _ in card._runs:
        for m in _whole_word(word).finditer(text):
            left = x + card._metrics.horizontalAdvance(text[:m.start()])
            hits.append(QPointF(left + card._metrics.horizontalAdvance(word) / 2, y))
    return hits[occurrence]


def test_clicking_a_word_highlights_it_everywhere_in_the_card(make_window, tmp_path):
    window = make_window()
    card = _card_for(window, tmp_path, CARD_SOURCE)
    # "pending": declared as a variable, an if condition, and an output.
    assert card.word_at(_point_on(card, "pending")) == "pending"
    card.set_highlight("pending")
    assert card.highlight_count() == 3
    # Whole words only: "out" doesn't count inside "out_size".
    card.set_highlight("out")
    assert card.highlight_count() == 1
    # Blank space isn't a word.
    from PySide6.QtCore import QPointF
    assert card.word_at(QPointF(card.size[0] - 2, card.title_height + 3)) is None


def test_only_the_title_bar_drags_a_card(make_window, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    window = make_window()
    window.resize(1400, 900)
    window.show()
    card = _card_for(window, tmp_path, CARD_SOURCE)
    view = window.cards

    def drag(card_point, dx):
        start = view.mapFromScene(card.mapToScene(card_point))
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        for step in range(1, 6):
            QTest.mouseMove(view.viewport(), start + QPoint(dx * step // 5, 0))
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(dx, 0))

    before = card.pos()
    drag(_point_on(card, "pending"), 60)        # a word in the body: highlights, no move
    assert card.pos() == before
    assert card.highlight == "pending"

    drag(QPointF(20, card.title_height / 2), 60)  # the title bar: moves
    assert card.pos().x() > before.x() + 30


CODE_MENU_SOURCE = (
    "static void helper(void);\n"            # 1  prototype
    "\n"                                     # 2
    "static void helper(void) {\n"           # 3
    "    int x = 1;\n"                       # 4
    "}\n"                                    # 5
    "\n"                                     # 6
    "int main(void) {\n"                     # 7
    "    helper();\n"                        # 8  a call
    "    strcmp(a, b);\n"                    # 9  a function from elsewhere
    "    return 0;\n"                        # 10
    "}\n"                                    # 11
)


def test_code_right_click_picks_the_function(make_window, tmp_path):
    path = tmp_path / "m.c"
    path.write_text(CODE_MENU_SOURCE)
    window = make_window()
    window.open_file(path)

    assert window.card_target("helper", 1).name == "helper"   # on the prototype
    assert window.card_target("helper", 1).has_body           # ... gives the definition
    assert window.card_target("helper", 8).name == "helper"   # on a call in main
    assert window.card_target("strcmp", 9).name == "main"     # not ours: the function we're in
    assert window.card_target("return", 10).name == "main"
    assert window.card_target("", 6) is None                  # between functions


def test_code_context_menu_adds_card(make_window, tmp_path):
    path = tmp_path / "m.c"
    path.write_text(CODE_MENU_SOURCE)
    window = make_window()
    window.resize(1400, 900)
    window.show()
    window.open_file(path)
    viewer = window.viewer

    # The point over "helper" in "    helper();" (line 8).
    block = viewer.document().findBlockByNumber(7)
    from PySide6.QtGui import QTextCursor
    cursor = QTextCursor(block)
    cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, 6)
    pos = viewer.cursorRect(cursor).center()

    menu = viewer.context_menu_for(pos)
    first = menu.actions()[0]
    assert first.text() == "Add Card: helper"
    first.trigger()
    [card] = window.cards.cards
    assert card.function.name == "helper" and card.function.has_body

    # Read-only: the standard menu has Copy but no Cut or Paste.
    texts = [a.text().replace("&", "") for a in menu.actions()]
    assert any(t.startswith("Copy") for t in texts)
    assert not any(t.startswith(("Cut", "Paste")) for t in texts)


def _wheel(view, notches, pos=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    pos = pos or view.viewport().rect().center()
    event = QWheelEvent(QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), QPoint(0, 0),
                        QPoint(0, 120 * notches), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), event)


def test_scroll_wheel_zooms_the_diagram(make_window, tmp_path):
    from skyfish.ui import card_canvas as cc

    window = make_window()
    window.resize(1400, 900)
    window.show()
    view = window.cards
    assert window.zoom_label.text() == "100%"

    _wheel(view, 1)
    assert abs(view.zoom - cc.ZOOM_STEP) < 1e-6
    assert window.zoom_label.text() == f"{round(cc.ZOOM_STEP * 100)}%"

    _wheel(view, -40)  # far out: stops at the minimum
    assert abs(view.zoom - cc.ZOOM_MIN) < 1e-6
    _wheel(view, 40)
    assert abs(view.zoom - cc.ZOOM_MAX) < 1e-6

    view.set_zoom(1.0)  # what the 100% button does
    assert window.zoom_label.text() == "100%"


def test_diagram_header_counts_cards(make_window, tmp_path):
    path = tmp_path / "a.py"
    path.write_text("def f():\n    pass\n\ndef g():\n    pass\n")
    window = make_window()
    window.open_file(path)
    assert "Add Card" in window.card_count.text()
    _add(window, "f")
    assert window.card_count.text() == "1 card"
    _add(window, "g")
    assert window.card_count.text() == "2 cards"
    window.cards.remove_card(window.cards.cards[0])
    assert window.card_count.text() == "1 card"


def _list_rows(window):
    """[(text, [child texts])] for what the functions list shows."""
    tree = window.functions_list
    return [(tree.topLevelItem(i).text(0),
             [tree.topLevelItem(i).child(c).text(0) for c in range(tree.topLevelItem(i).childCount())])
            for i in range(tree.topLevelItemCount())]


def test_functions_list_follows_checkboxes(make_window, tmp_path):
    path = tmp_path / "demo.py"
    path.write_text("def f(a):\n    b = 1\n\nclass C:\n    def m(self, x):\n        MAX_N = x\n")
    window = make_window()
    window.open_file(path)
    assert _list_rows(window) == [("f", []), ("C.m", [])]

    window.show_variables.setChecked(True)
    assert _list_rows(window) == [("f", ["b: int"]), ("C.m", ["MAX_N"])]
    child = window.functions_list.topLevelItem(0).child(0)
    assert child.data(0, SPANS_ROLE) == [("b: ", hl.PLAIN_COLOR), ("int", hl.TYPE.foreground().color())]
    assert window.functions_list.topLevelItem(0).foreground(0).color() == hl.FUNCTION.foreground().color()

    window.show_functions.setChecked(False)
    assert _list_rows(window) == [("C.m", ["MAX_N"])]

    # Clicking a row jumps to its line in the code.
    window.show_functions.setChecked(True)
    window._on_list_clicked(window.functions_list.topLevelItem(1))
    assert window.viewer.textCursor().blockNumber() == 4


def test_functions_list_hides_prototypes_of_defined_functions(make_window, tmp_path):
    c_file = tmp_path / "a.c"
    c_file.write_text("static void f(void);\nvoid g(void);\n\nstatic void f(void) {\n}\n")
    window = make_window()
    window.open_file(c_file)
    # f's prototype repeats its definition; g is only declared here, so it stays.
    assert [row for row, _ in _list_rows(window)] == ["g", "f"]
    assert window.functions_list.topLevelItem(0).font(0).italic()


def test_functions_list_index_points_at_the_function(make_window, tmp_path):
    from skyfish.ui import main_window as mw
    path = tmp_path / "demo.py"
    path.write_text("def f():\n    b = 1\n\ndef g():\n    pass\n")
    window = make_window()
    window.show_variables.setChecked(True)
    window.open_file(path)
    rows = [window.functions_list.topLevelItem(i) for i in range(2)]
    assert [window._functions[r.data(0, mw._FUNCTION_INDEX_ROLE)].name for r in rows] == ["f", "g"]
    assert rows[0].child(0).data(0, mw._FUNCTION_INDEX_ROLE) is None  # variable rows: no Add Card


def test_header_logo_and_version(make_window, tmp_path):
    from skyfish.ui import main_window as mw

    window = make_window()
    window.resize(1600, 900)
    window.show()
    assert mw.LOGO_PATH.is_file()

    def top_left(widget):
        return widget.mapTo(window, widget.rect().topLeft())

    logo, version, bar, code, analysis = (top_left(w) for w in (
        window.logo, window.version_label, window.menu_bar, window.viewer, window.analytics_panel))
    # Logo top-left of the window, the version beside it.
    assert logo.x() < 20 and logo.y() < 20
    assert window.version_label.text() == f"v{APP_VERSION}"
    assert version.x() >= logo.x() + window.logo.width()
    # The menu bar under the logo, across the window's full width.
    assert bar.y() >= logo.y() + window.logo.height()
    assert bar.x() == 0 and window.menu_bar.width() == window.width()
    # Both columns start under the menu bar.
    assert code.y() >= bar.y() + window.menu_bar.height()
    assert analysis.y() >= bar.y() + window.menu_bar.height()
    assert round(window.logo.pixmap().deviceIndependentSize().height()) == mw.LOGO_HEIGHT_PX


def _menu(window, title):
    menu = {"File": window.file_menu, "View": window.view_menu, "Help": window.help_menu}[title]
    return menu


def _items(menu):
    return [a.text().replace("&", "") for a in menu.actions() if not a.isSeparator()]


def test_menu_bar(make_window):
    window = make_window()
    assert [a.text().replace("&", "") for a in window.menu_bar.actions()] == ["File", "View", "Help"]
    assert _items(_menu(window, "File")) == ["New", "Open File...", "Open Recent", "Save", "Save As...", "Exit"]
    assert window.open_action.shortcut().toString() == "Ctrl+O"
    assert _items(_menu(window, "View")) == [
        "Functions", "Methods", "Variables", "Logic", "Zoom In", "Zoom Out", "Reset Zoom"]
    assert _items(_menu(window, "Help")) == ["About SkyFish Code Analytics"]


def test_view_menu_and_checkboxes_stay_in_step(make_window):
    window = make_window()
    action = window.view_actions["Variables"]
    assert action.isChecked() == window.show_variables.isChecked()
    action.setChecked(not action.isChecked())
    assert window.show_variables.isChecked() == action.isChecked()
    window.show_variables.setChecked(not window.show_variables.isChecked())
    assert action.isChecked() == window.show_variables.isChecked()


def test_view_menu_zoom(make_window):
    from skyfish.ui import card_canvas as cc
    window = make_window()
    window.cards.zoom_by(1)
    assert abs(window.cards.zoom - cc.ZOOM_STEP) < 1e-6
    window.cards.zoom_by(-2)
    assert abs(window.cards.zoom - 1 / cc.ZOOM_STEP) < 1e-6


def test_open_recent(make_window, tmp_path):
    from skyfish.ui import main_window as mw
    files = []
    for i in range(mw.RECENT_FILES_MAX + 2):
        path = tmp_path / f"f{i}.py"
        path.write_text("x = 1\n")
        files.append(path)
    window = make_window()
    for path in files:
        window.open_file(path)
    window.open_file(files[3])  # again: moves to the top, no duplicate

    recent = window.recent_files()
    assert recent[0] == files[3]
    assert len(recent) == mw.RECENT_FILES_MAX and len(set(recent)) == len(recent)

    files[-1].unlink()  # gone files drop out
    assert files[-1] not in window.recent_files()

    window._fill_recent_menu()
    first = window.recent_menu.actions()[0]
    assert first.text() == "&1  f3.py" and first.toolTip() == str(files[3])
    window.open_file(files[0])
    window._fill_recent_menu()
    window.recent_menu.actions()[1].trigger()  # "2  f3.py"
    assert window.current_file == files[3]


ADD_KEY_VALUE_PAIR = (
    "bool addKeyValuePair(char *dataCSV, uint16_t size, const char *KEY, int VALUE) {\n"
    "    char number[12];\n"
    "    if(dataCSV == NULL || KEY == NULL || size == 0) {\n"
    "        return false;\n"
    "    }\n"
    "    return true;\n"
    "}\n"
)


def _drag_input_out(window, card, name, dx=-260):
    """Presses on the input's blue dot and drags it dx pixels across the canvas, as a person would."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    view = window.cards
    start = view.mapFromScene(card.input_port_scene_pos(name))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    for step in range(1, 6):
        QTest.mouseMove(view.viewport(), start + QPoint(dx * step // 5, 0))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(dx, 0))


def test_drag_an_input_dot_out_to_make_a_value_card(make_window, tmp_path):
    path = tmp_path / "drv.c"
    path.write_text(ADD_KEY_VALUE_PAIR)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    card.setPos(500, 40)  # room on its left for value cards

    _drag_input_out(window, card, "size")
    [size_card] = window.cards.value_cards_of(card)
    assert size_card.parameter.name == "size" and size_card.value_type.type_name == "uint16_t"
    # Its port sits where the dot was dropped, left of the card, and the wire joins the two.
    assert size_card.port_scene_pos().x() < card.input_port_scene_pos("size").x()
    wire = window.cards._value_cards[(card.key, "size")][1]
    assert wire.path().elementCount() > 0

    size_card.set_text("70000")
    assert "Too big for uint16_t (0 to 65,535)" in size_card.error
    assert "size" not in card.input_values
    size_card.set_text("64")
    assert size_card.value == 64 and size_card.error is None
    assert card.input_values["size"] == "64"
    row = next("".join(t for t, _ in spans) for _, spans, n in card._inputs if n == "size")
    assert row == "uint16_t size = 64"

    # Text, with escapes; and NULL for a pointer.
    key_card = window.cards.add_value_card(card, "KEY")
    key_card.set_text(r"TEMP\r\n")
    assert card.input_values["KEY"] == r'"TEMP\r\n"'
    csv_card = window.cards.add_value_card(card, "dataCSV")
    csv_card.null_box.setChecked(True)
    assert card.input_values["dataCSV"] == "NULL" and not csv_card.editor.isEnabled()

    # Dragging the same dot again selects its card rather than making another.
    _drag_input_out(window, card, "size", dx=-120)
    assert len(window.cards.value_cards_of(card)) == 3

    # The wire follows when the function card moves.
    before = wire.path().pointAtPercent(1)
    card.setPos(card.pos().x() + 50, card.pos().y())
    assert wire.path().pointAtPercent(1).x() == before.x() + 50

    # Removing a value card clears its value; removing the function card takes the rest.
    window.cards.remove_value_card(size_card)
    assert "size" not in card.input_values
    window.cards.remove_card(card)
    assert window.cards._value_cards == {}


def test_value_cards_hide_with_their_inputs(make_window, tmp_path):
    path = tmp_path / "drv.c"
    path.write_text(ADD_KEY_VALUE_PAIR)
    window = make_window()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    value_card = window.cards.add_value_card(card, "VALUE")
    window.show_logic.setChecked(False)  # no inputs showing: nothing to wire to
    assert not value_card.isVisible()
    window.show_logic.setChecked(True)
    assert value_card.isVisible()
    # Opening a file clears value cards with the rest.
    window.open_file(path)
    assert window.cards._value_cards == {}


def test_file_new_clears_everything(make_window, tmp_path):
    path = tmp_path / "drv.c"
    path.write_text(ADD_KEY_VALUE_PAIR)
    window = make_window()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    window.cards.add_value_card(card, "size").set_text("12")

    [new_action] = [a for a in window.file_menu.actions() if a.text() == "&New"]
    assert new_action.shortcut().toString() == "Ctrl+N"
    new_action.trigger()

    assert window.viewer.toPlainText() == ""
    assert window.functions_list.topLevelItemCount() == 0
    assert window.cards.cards == [] and window.cards._value_cards == {}
    assert window.card_count.text().startswith("Right-click")
    assert window.message.text().startswith("Open a file")
    assert window.current_file is None
    assert "drv.c" not in window.windowTitle()
    # Blank at the next start-up too; the file is still in Open Recent.
    window2 = make_window()
    window2.reopen_last_file()
    assert window2.current_file is None
    assert window2.recent_files() == [path]


def _code_text(viewer, span):
    """The code a span covers, as the code view holds it."""
    cursor = viewer.cursor_for(span.line, span.col)
    from PySide6.QtGui import QTextCursor
    cursor.setPosition(viewer.cursor_for(span.end_line, span.end_col).position(), QTextCursor.KeepAnchor)
    return cursor.selectedText()


def _tie_in_window(make_window, tmp_path):
    path = tmp_path / "drv.c"
    path.write_text(ADD_KEY_VALUE_PAIR)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.show_variables.setChecked(True)
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    return window, card


def _click_card(window, card, card_point):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    view = window.cards
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.mapFromScene(card.mapToScene(card_point)))


def _click_code(window, line, col):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    viewer = window.viewer
    rect = viewer.cursorRect(viewer.cursor_for(line, col))
    point = rect.center()
    point.setX(point.x() + 2)  # just inside the character, not on its left edge
    QTest.mouseClick(viewer.viewport(), Qt.LeftButton, Qt.NoModifier, point)


def test_clicking_a_card_line_highlights_its_code(make_window, tmp_path):
    window, card = _tie_in_window(make_window, tmp_path)

    # The first if row: its header and the return it leaves through.
    _click_card(window, card, _point_on(card, "dataCSV", 1))  # 0: the input, 1: the if row
    texts = [_code_text(window.viewer, s) for s in window.viewer.linked_spans]
    assert texts == ["if(dataCSV == NULL || KEY == NULL || size == 0)", "return false;"]
    # The clicked word is marked throughout the function's code.
    assert window.viewer.marked_word == "dataCSV"
    assert len(window.viewer._word_marks) == 2  # the parameter and the condition

    # A variable line: its declaration.
    _click_card(window, card, _point_on(card, "number"))
    assert [_code_text(window.viewer, s) for s in window.viewer.linked_spans] == ["char number[12];"]

    # An output: every return that leaves through it.
    _click_card(window, card, _point_on(card, "false"))  # the output label
    shown = [_code_text(window.viewer, s) for s in window.viewer.linked_spans]
    assert shown == ["return false;"]  # (the real file has two ifs; this cut-down one has one)


def test_clicking_code_lights_its_card_line(make_window, tmp_path):
    window, card = _tie_in_window(make_window, tmp_path)

    def lit_text():
        return card._runs[card.linked_run][2] if card.linked_run is not None else None

    # On the if's header.
    _click_code(window, 3, 9)  # "    if(dataCSV" - on dataCSV
    assert lit_text() == "if dataCSV == NULL || KEY == NULL || size == 0"
    assert card.highlight == "dataCSV"
    assert window.viewer.marked_word == "dataCSV"

    # Inside its body: still that if (the innermost one holding it).
    _click_code(window, 4, 10)  # "        return false;"
    assert lit_text() == "if dataCSV == NULL || KEY == NULL || size == 0"

    # On a declaration.
    _click_code(window, 2, 10)  # "    char number[12];"
    assert lit_text() == "char number[12]"
    assert card.highlight == "number"

    # On the last return: the "otherwise" line.
    _click_code(window, 6, 8)  # "    return true;"
    assert lit_text() == "otherwise"


def test_linked_code_is_centred_in_the_code_view(make_window, tmp_path):
    path = tmp_path / "long.c"
    filler = "".join(f"int pad{i}(void) {{ return {i}; }}\n" for i in range(150))
    path.write_text(filler + ADD_KEY_VALUE_PAIR + filler)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    _click_card(window, card, _point_on(card, "dataCSV", 1))  # the first if line

    viewer = window.viewer
    span = viewer.linked_spans[0]
    top = viewer.cursorRect(viewer.cursor_for(span.line, span.col)).center().y()
    middle = viewer.viewport().height() / 2
    assert abs(top - middle) < viewer.fontMetrics().height() * 1.5


OVERLAY_SOURCE = (
    "bool addKeyValuePair(char *dataCSV, uint16_t size, const char *KEY, int VALUE) {\n"   # 1
    "    // size is checked first\n"                                                       # 2
    "    if(dataCSV == NULL || KEY == NULL || size == 0) {\n"                               # 3
    "        return false;\n"                                                              # 4
    "    }\n"                                                                              # 5
    "    snprintf(dataCSV, size, \"size\");\n"                                            # 6
    "    return true;\n"                                                                   # 7
    "}\n"                                                                                  # 8
)


def _ctrl_click_code(window, line, col):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    viewer = window.viewer
    point = viewer.cursorRect(viewer.cursor_for(line, col)).center()
    point.setX(point.x() + 2)
    QTest.mouseClick(viewer.viewport(), Qt.LeftButton, Qt.ControlModifier, point)


def test_ctrl_click_shows_a_value_over_every_use(make_window, tmp_path):
    path = tmp_path / "drv.c"
    path.write_text(OVERLAY_SOURCE)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    viewer = window.viewer

    # No value yet: Ctrl+click does nothing.
    _ctrl_click_code(window, 3, 43)  # "size" in the if
    assert viewer.overlays == {}

    size_card = window.cards.add_value_card(card, "size")
    size_card.set_text("32")
    _ctrl_click_code(window, 3, 43)
    [(key, uses)] = viewer.overlays.items()
    assert key[0] == "size"
    # The parameter, the if and the snprintf argument - not the comment or the string.
    texts = []
    for start, end in uses:
        cursor = viewer.cursor_for(1, 0)
        cursor.setPosition(start)
        texts.append(cursor.block().blockNumber() + 1)
    assert texts == [1, 3, 6]
    boxes = list(viewer._overlay_boxes())
    assert [value for _, value in boxes] == ["32", "32", "32"]

    # A new value shows straight away.
    size_card.set_text("0x40")
    assert [value for _, value in viewer._overlay_boxes()] == ["64", "64", "64"]

    # Ctrl+click again hides it; a plain click doesn't.
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(viewer.viewport(), Qt.LeftButton, Qt.NoModifier,
                     viewer.cursorRect(viewer.cursor_for(3, 43)).center())
    assert viewer.overlays
    _ctrl_click_code(window, 6, 23)  # another use of size hides it too
    assert viewer.overlays == {}

    # Several at once; text values are cut to the name's width (full on hover).
    key_card = window.cards.add_value_card(card, "KEY")
    key_card.set_text("TEMP")
    _ctrl_click_code(window, 3, 28)  # KEY
    size_card.set_text("32")
    _ctrl_click_code(window, 3, 43)  # size
    assert {name for name, _ in viewer.overlays} == {"KEY", "size"}
    values = {value for _, value in viewer._overlay_boxes()}
    assert values == {'"TEMP"', "32"}

    # Opening a file clears them.
    window.open_file(path)
    assert viewer.overlays == {}


def test_ctrl_click_works_when_ctrl_is_let_go_first(make_window, tmp_path):
    """People often release Ctrl a moment before the mouse button."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    path = tmp_path / "drv.c"
    path.write_text(OVERLAY_SOURCE)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    window.cards.add_value_card(card, "size").set_text("32")
    viewer = window.viewer
    point = viewer.cursorRect(viewer.cursor_for(3, 43)).center()
    point.setX(point.x() + 2)
    QTest.mousePress(viewer.viewport(), Qt.LeftButton, Qt.ControlModifier, point)
    QTest.mouseRelease(viewer.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    assert [name for name, _ in viewer.overlays] == ["size"]
    # And a plain click afterwards is a plain click again.
    QTest.mouseClick(viewer.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    assert [name for name, _ in viewer.overlays] == ["size"]


SAVE_SOURCE = (
    "bool addKeyValuePair(char *dataCSV, uint16_t size, const char *KEY, int VALUE) {\n"
    "    if(dataCSV == NULL || size == 0) {\n"
    "        return false;\n"
    "    }\n"
    "    return true;\n"
    "}\n"
    "\n"
    "int helper(int a) {\n"
    "    return a + 1;\n"
    "}\n"
)


def _saved_diagram(make_window, tmp_path):
    """Opens SAVE_SOURCE, builds a diagram and saves it beside the code."""
    path = tmp_path / "drv.c"
    path.write_text(SAVE_SOURCE)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    card = _add(window, "addKeyValuePair")
    card.setPos(420, 60)
    size = window.cards.add_value_card(card, "size")
    size.setPos(30, 90)
    size.set_text("32")
    csv = window.cards.add_value_card(card, "dataCSV")
    csv.setPos(30, 300)  # clear of the size card
    csv.null_box.setChecked(True)
    _add(window, "helper").setPos(420, 400)
    window.cards.set_zoom(0.8)
    window.write_diagram(tmp_path / "drv.skyfish")
    return path


def test_save_and_reopen_brings_the_diagram_back(make_window, tmp_path):
    path = _saved_diagram(make_window, tmp_path)
    assert (tmp_path / "drv.skyfish").is_file()

    window = make_window()  # like the next time the file is opened
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    cards = {c.function.name: c for c in window.cards.cards}
    assert sorted(cards) == ["addKeyValuePair", "helper"]
    main = cards["addKeyValuePair"]
    assert (main.pos().x(), main.pos().y()) == (420, 60)
    values = {v.parameter.name: v for v in window.cards.value_cards_of(main)}
    assert values["size"].value == 32 and (values["size"].pos().x(), values["size"].pos().y()) == (30, 90)
    assert values["dataCSV"].is_null
    assert main.input_values == {"size": "32", "dataCSV": "NULL"}
    assert abs(window.cards.zoom - 0.8) < 1e-6
    assert not any(c.stale for c in cards.values())
    assert window.diagram_path == tmp_path / "drv.skyfish"


def test_edited_function_updates_and_gone_function_is_crossed_out(make_window, tmp_path):
    path = _saved_diagram(make_window, tmp_path)
    # Edit addKeyValuePair (a new if) and rename helper.
    path.write_text(SAVE_SOURCE.replace("        return false;\n    }\n",
                                        "        return false;\n    }\n    if (size > 64) { return false; }\n")
                               .replace("int helper(int a)", "int helper2(int a)"))
    window = make_window()
    window.open_file(path)
    cards = {c.function.name: c for c in window.cards.cards}
    main, old = cards["addKeyValuePair"], cards["helper"]
    assert not main.stale and [r.keyword for r in main.logic_rows] == ["if", "if", "otherwise"]  # updated
    assert old.stale and old.saved_source.startswith("int helper(int a)")  # kept, crossed out
    assert "crossed out (no longer in the code): helper" in window.message.text()
    # A crossed-out card isn't tied to today's code.
    assert window.cards.card_at_code(9, 4) is None
    # Saving again keeps it as it was.
    window.write_diagram(tmp_path / "drv.skyfish")
    window2 = make_window()
    window2.open_file(path)
    assert {c.function.name: c.stale for c in window2.cards.cards} == {"addKeyValuePair": False, "helper": True}


def test_close_x_closes_cards(make_window, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    path = _saved_diagram(make_window, tmp_path)
    window = make_window()
    window.resize(1600, 900)
    window.show()
    window.open_file(path)
    view = window.cards
    main = next(c for c in view.cards if c.function.name == "addKeyValuePair")
    size = next(v for v in view.value_cards_of(main) if v.parameter.name == "size")

    def click(item, rect):
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier,
                         view.mapFromScene(item.mapToScene(rect.center())))

    click(size, size.close_rect())  # a value card's X: just that value card
    assert [v.parameter.name for v in view.value_cards_of(main)] == ["dataCSV"]
    assert "size" not in main.input_values
    click(main, main.close_rect())  # a function card's X: it and its value cards
    assert [c.function.name for c in view.cards] == ["helper"]
    assert view._value_cards == {}


def test_save_as_needs_a_code_file(make_window):
    window = make_window()
    window.save_diagram_as()
    assert "Open a code file first" in window.message.text()
