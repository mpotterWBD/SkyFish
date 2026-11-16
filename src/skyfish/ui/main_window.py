import re
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from skyfish.analysis.functions import (
    FUNCTION,
    LOCAL,
    METHOD,
    SUPPORTED_EXTS,
    FunctionInfo,
    UnsupportedLanguage,
    find_functions,
)
from skyfish.ui.card_canvas import CardCanvas
from skyfish.ui.code_viewer import CodeViewer
from skyfish.ui.highlighter import FUNCTION as FUNCTION_FORMAT
from skyfish.ui.highlighter import colour_spans, language_for
from skyfish.ui.spans_delegate import SPANS_ROLE, SpansDelegate
from skyfish.version import APP_NAME, APP_VERSION

_THEME_PATH = Path(__file__).parent / "theme.qss"
_LAST_FILE_KEY = "last_file"
# The summary line with no file open (at start, and after File > New).
_START_MESSAGE = "Open a file, then right-click a function in the code > Add Card."
_RECENT_KEY = "recent_files"
RECENT_FILES_MAX = 8  # how many File > Open Recent lists
# The dividers: left column | analysis, and code | list. (New keys: the older
# "divider" / "dividers" held earlier layouts, which don't fit this one.)
_DIVIDER_KEY = "main_divider"
_CODE_DIVIDER_KEY = "code_divider"
LIST_WIDTH_PX = 240  # the functions list's starting width (layout knob)

# The logo in the window's top-left corner: SkyFish/assets/skyfish_logo.png
# (made by assets/make_logo.py), loaded from there so a regenerated logo shows up.
LOGO_PATH = Path(__file__).resolve().parents[3] / "assets" / "skyfish_logo.png"
LOGO_HEIGHT_PX = 96  # layout knob

# List-row data roles: the row's line (for jumping to it) and, on function
# rows, its index into the open file's functions (for Add Card).
_LINE_ROLE = Qt.UserRole
_FUNCTION_INDEX_ROLE = Qt.UserRole + 2
_SHOW_FUNCTIONS_KEY = "show_functions"
_SHOW_METHODS_KEY = "show_methods"
_SHOW_VARIABLES_KEY = "show_variables"
_SHOW_LOGIC_KEY = "show_logic"

_FILE_FILTER = (
    "Supported code (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTS)) + ");;All files (*)"
)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


class MainWindow(QMainWindow):
    """Across the top, full width: the logo and version, then the menu bar
    (File, View, Help). Below, two columns: the code viewer and a narrow list
    of the file's functions, and the analytics panel - a summary, the
    checkboxes (for the list and the cards), and the diagram. Cards are added
    by right-clicking a function in the code or the list (Add Card)."""

    def __init__(self, settings: QSettings | None = None) -> None:
        """settings is where the last opened file and the layout are
        remembered; by default the per-user SkyFish settings (the Windows
        registry). Tests pass a throwaway .ini so they never touch the real one."""
        super().__init__()
        self._base_title = f"{APP_NAME}  v{APP_VERSION}"
        self.setWindowTitle(self._base_title)
        self.resize(1100, 700)
        self.setStyleSheet(_THEME_PATH.read_text())
        self.current_file: Path | None = None
        self._settings = settings if settings is not None else QSettings("Westbound Designs", "SkyFish")
        self._functions: list[FunctionInfo] = []
        self._language: str | None = None  # of the open file, for colouring cards

        self.open_action = QAction("&Open File...", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self._choose_file)

        self.viewer = CodeViewer()
        self.viewer.setObjectName("codeViewer")
        # Right-click in the code > Add Card.
        self.viewer.card_target = self.card_target
        self.viewer.add_card_requested.connect(self.add_function_card)
        # Click in the code -> the matching card line lights up (see _on_code_clicked).
        self.viewer.code_clicked.connect(self._on_code_clicked)

        self.analytics_panel = QWidget()
        self.analytics_panel.setObjectName("analyticsPanel")
        analytics_layout = QVBoxLayout(self.analytics_panel)
        self._functions_title = QLabel("Functions & Methods")
        self._functions_title.setObjectName("panelTitle")
        self.message = QLabel(_START_MESSAGE)
        self.message.setObjectName("panelMessage")
        self.message.setWordWrap(True)

        # What cards show. Each box's state is remembered between sessions.
        filters = QHBoxLayout()
        self.show_functions = self._filter_box("Functions", _SHOW_FUNCTIONS_KEY, True)
        self.show_methods = self._filter_box("Methods", _SHOW_METHODS_KEY, True)
        self.show_variables = self._filter_box("Variables", _SHOW_VARIABLES_KEY, False)
        self.show_logic = self._filter_box("Logic", _SHOW_LOGIC_KEY, True)  # inputs, ifs, outputs
        for box in (self.show_functions, self.show_methods, self.show_variables, self.show_logic):
            filters.addWidget(box)
        filters.addStretch(1)

        self.cards = CardCanvas()
        # Click a card line -> its code is highlighted (see _on_card_clicked).
        self.cards.card_clicked.connect(self._on_card_clicked)
        for box in (self.show_functions, self.show_methods, self.show_variables, self.show_logic):
            box.toggled.connect(self._apply_card_filters)
        self._apply_card_filters()

        analytics_layout.addWidget(self._functions_title)
        analytics_layout.addWidget(self.message)
        analytics_layout.addLayout(filters)
        analytics_layout.addWidget(self._diagram_panel(), stretch=1)

        # The functions list: a narrow column between the code and the
        # analysis panel, for finding your way around the file quickly.
        self.functions_list = QTreeWidget()
        self.functions_list.setObjectName("functionsList")
        self.functions_list.setHeaderHidden(True)
        self.functions_list.setItemDelegateForColumn(0, SpansDelegate(self.functions_list))
        self.functions_list.itemClicked.connect(self._on_list_clicked)
        self.functions_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.functions_list.customContextMenuRequested.connect(self._on_list_context_menu)
        for box in (self.show_functions, self.show_methods, self.show_variables):
            box.toggled.connect(self._fill_list)

        # The code and the list side by side. Dragging the main divider
        # resizes the code; the list keeps the width it was dragged to.
        self.code_splitter = QSplitter(Qt.Horizontal)
        self.code_splitter.addWidget(self.viewer)
        self.code_splitter.addWidget(self.functions_list)
        self.code_splitter.setStretchFactor(0, 1)
        self.code_splitter.setStretchFactor(1, 0)
        self.code_splitter.setSizes([650, LIST_WIDTH_PX])

        # Main split: code and list | analysis. When the window grows or
        # shrinks, only analysis changes size.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.code_splitter)
        self.splitter.addWidget(self.analytics_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([650 + LIST_WIDTH_PX, 450])

        # Both dividers go back where they were last dragged; saved on every drag.
        for splitter, key in ((self.splitter, _DIVIDER_KEY), (self.code_splitter, _CODE_DIVIDER_KEY)):
            saved = self._settings.value(key)
            if saved is not None:
                splitter.restoreState(saved)
            splitter.splitterMoved.connect(self._save_dividers)
        self.setCentralWidget(self.splitter)

        # Across the top, the window's full width: the logo band, then the
        # menu bar under it. (setMenuWidget puts both where a menu bar would
        # normally sit alone.)
        self.menu_bar = QMenuBar()
        self._build_menus()
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        top_layout.addWidget(self._header())
        top_layout.addWidget(self.menu_bar)
        self.setMenuWidget(top)

    # --- menus ------------------------------------------------------------

    def _build_menus(self) -> None:
        """The menu bar: File, View, Help. View's items are the panel's
        checkboxes again - ticking either one ticks the other."""
        bar = self.menu_bar

        file_menu = self.file_menu = bar.addMenu("&File")
        new_action = file_menu.addAction("&New")
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self.new_file)
        file_menu.addAction(self.open_action)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("E&xit")
        exit_action.triggered.connect(self.close)

        view_menu = self.view_menu = bar.addMenu("&View")
        self.view_actions: dict[str, QAction] = {}
        for box in (self.show_functions, self.show_methods, self.show_variables, self.show_logic):
            action = view_menu.addAction(box.text())
            action.setCheckable(True)
            action.setChecked(box.isChecked())
            # Kept in step both ways; setChecked to the same value doesn't
            # signal again, so this can't loop.
            action.toggled.connect(box.setChecked)
            box.toggled.connect(action.setChecked)
            self.view_actions[box.text()] = action
        view_menu.addSeparator()
        for text, shortcut, slot in (
            ("Zoom &In", "Ctrl+=", lambda: self.cards.zoom_by(1)),
            ("Zoom &Out", "Ctrl+-", lambda: self.cards.zoom_by(-1)),
            ("&Reset Zoom", "Ctrl+0", lambda: self.cards.set_zoom(1.0)),
        ):
            action = view_menu.addAction(text)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)

        help_menu = self.help_menu = bar.addMenu("&Help")
        about = help_menu.addAction(f"&About {APP_NAME}")
        about.triggered.connect(self._show_about)

    def recent_files(self) -> list[Path]:
        """Recently opened files, newest first, that still exist."""
        stored = self._settings.value(_RECENT_KEY, [], type=list) or []
        return [Path(p) for p in stored if Path(p).is_file()]

    def _remember_recent(self, path: Path) -> None:
        recent = [p for p in self.recent_files() if p != path]
        recent.insert(0, path)
        self._settings.setValue(_RECENT_KEY, [str(p) for p in recent[:RECENT_FILES_MAX]])

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.recent_files()
        if not recent:
            empty = self.recent_menu.addAction("(none yet)")
            empty.setEnabled(False)
            return
        for number, path in enumerate(recent, start=1):
            # "&1" makes the number the item's keyboard key; the folder is in the tooltip.
            action = self.recent_menu.addAction(f"&{number}  {path.name}")
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.triggered.connect(lambda checked=False, p=path: self.open_file(p))

    def _show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        icon = QPixmap(str(LOGO_PATH.with_name("skyfish_icon.png")))
        if not icon.isNull():
            box.setIconPixmap(icon.scaledToHeight(96, Qt.SmoothTransformation))
        box.setText(f"<b>{APP_NAME}</b>&nbsp;&nbsp;v{APP_VERSION}")
        box.setInformativeText("Code analysis: functions, variables and logic as diagrams.\n"
                               "Westbound Designs")
        box.exec()

    # --- header -----------------------------------------------------------

    def _header(self) -> QWidget:
        """The top of the left column: the logo with the version beside it."""
        header = QWidget()
        header.setObjectName("appHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(12)
        self.logo = self._logo_label()
        if self.logo is not None:
            layout.addWidget(self.logo)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setObjectName("appVersion")
        # Level with the logo's bottom line ("Code Analytics").
        layout.addWidget(self.version_label, alignment=Qt.AlignBottom)
        layout.addStretch(1)
        return header

    def _logo_label(self) -> QLabel | None:
        """The SkyFish logo for the top-left corner, or None if the
        file isn't there (the app works without it). Scaled for the screen's
        pixel density so it stays sharp on high-DPI displays."""
        pixmap = QPixmap(str(LOGO_PATH))
        if pixmap.isNull():
            return None
        ratio = self.devicePixelRatioF()
        pixmap = pixmap.scaledToHeight(round(LOGO_HEIGHT_PX * ratio), Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        label = QLabel()
        label.setObjectName("appLogo")
        label.setPixmap(pixmap)
        label.setToolTip(f"{APP_NAME}  v{APP_VERSION}")
        return label

    def _choose_file(self) -> None:
        start = str(self.current_file.parent) if self.current_file else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open a code file", start, _FILE_FILTER)
        if path:
            self.open_file(Path(path))

    def new_file(self) -> None:
        """File > New: back to a blank window - no code, an empty functions
        list and an empty diagram. The last file is forgotten too, so the
        next start-up is blank as well (Open Recent still lists it)."""
        self.current_file = None
        self._settings.remove(_LAST_FILE_KEY)
        self.setWindowTitle(self._base_title)
        self.viewer.show_code("", "")
        self._language = None
        self.cards.clear_cards()
        self._functions = []
        self._fill_list()
        self._show_message(_START_MESSAGE)

    def open_file(self, path: Path) -> None:
        """Shows path's code on the left, and a summary of its functions and
        methods on the right; the diagram starts empty."""
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._show_message(f"Could not open {path.name}: {exc.strerror or exc}")
            return
        self.current_file = path
        self._settings.setValue(_LAST_FILE_KEY, str(path))
        self._remember_recent(path)
        self.setWindowTitle(f"{path.name} - {self._base_title}")
        self.viewer.show_code(text, path.suffix)
        self._language = language_for(path.suffix)
        self.cards.clear_cards()  # cards belong to the file they came from
        self._functions = []
        self._fill_list()

        try:
            self._functions = find_functions(text, path.suffix)
        except UnsupportedLanguage:
            supported = ", ".join(sorted(SUPPORTED_EXTS))
            self._show_message(f"{path.suffix or 'This file type'} isn't supported yet ({supported}).")
            return
        except SyntaxError as exc:
            self._show_message(f"Python could not parse this file (line {exc.lineno}): {exc.msg}")
            return
        self._fill_list()

        # Prototypes are counted on their own: in a .c file they mostly repeat
        # functions defined further down.
        functions = sum(f.kind == FUNCTION and f.has_body for f in self._functions)
        methods = sum(f.kind == METHOD and f.has_body for f in self._functions)
        declarations = sum(not f.has_body for f in self._functions)
        variables = sum(v.kind == LOCAL for f in self._functions for v in f.variables)
        parts = [_plural(functions, "function"), _plural(methods, "method")]
        if declarations:
            parts.append(_plural(declarations, "declaration"))
        parts.append(_plural(variables, "variable"))
        self._show_message(" · ".join(parts) + f" in {path.name}")

    def _filter_box(self, text: str, key: str, default: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(self._settings.value(key, default, type=bool))
        box.toggled.connect(lambda checked: self._settings.setValue(key, checked))
        return box

    # --- the functions list ----------------------------------------------

    def _fill_list(self) -> None:
        """Rebuilds the list from the open file's functions: the kinds whose
        boxes are ticked, each with its local variables under it when
        Variables is ticked. A C prototype is listed only when the file
        doesn't define that function (a header, say) - otherwise it would
        just repeat the definition's row."""
        self.functions_list.clear()
        show_variables = self.show_variables.isChecked()
        self.functions_list.setRootIsDecorated(show_variables)  # arrows only mean something with children
        defined = {f.name for f in self._functions if f.has_body}
        function_colour = FUNCTION_FORMAT.foreground().color()
        for index, f in enumerate(self._functions):
            box = self.show_functions if f.kind == FUNCTION else self.show_methods
            if not box.isChecked() or (not f.has_body and f.name in defined):
                continue
            kind = f.kind if f.has_body else f"{f.kind} declaration"
            item = self._list_item(f.name, f"{kind}, line {f.line}", f.line)
            item.setForeground(0, function_colour)  # as function names are in the code
            if not f.has_body:
                font = item.font(0)
                font.setItalic(True)
                item.setFont(0, font)
            item.setData(0, _FUNCTION_INDEX_ROLE, index)
            if show_variables:
                for v in f.variables:
                    if v.kind != LOCAL:
                        continue
                    child = self._list_item(v.declaration, f"variable, line {v.line}", v.line)
                    # Written and coloured as declared in the code.
                    child.setData(0, SPANS_ROLE, colour_spans(v.declaration, self._language))
                    item.addChild(child)
            self.functions_list.addTopLevelItem(item)
        if show_variables:
            self.functions_list.expandAll()

    @staticmethod
    def _list_item(text: str, tooltip: str, line: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        # The full text too: the list is narrow, so long ones get cut off.
        item.setToolTip(0, f"{text}\n{tooltip}")
        item.setData(0, _LINE_ROLE, line)
        return item

    # --- tie-ins: cards <-> code ------------------------------------------------

    def _on_card_clicked(self, card, spans: list, word: str | None) -> None:
        """A line (and maybe a word) on a card was clicked: highlight the code
        it came from, and mark the word everywhere it's used in that function."""
        for other in self.cards.cards:
            if other is not card:
                other.clear_code_link()
        self.viewer.show_link(spans)
        self.viewer.mark_word(word, card.function.span)

    def _on_code_clicked(self, line: int, col: int, word: str) -> None:
        """A click in the code: on the card for that function (if there is
        one), light the line the clicked code belongs to and mark the word;
        in the code, highlight what that card line stands for and mark the
        word throughout the function."""
        card = self.cards.card_at_code(line, col)
        for other in self.cards.cards:
            if other is not card:
                other.clear_code_link()
        function = card.function if card is not None else self._function_at(line, col)
        within = function.span if function is not None else None
        if card is not None:
            card.show_code_link(line, col)
            card.set_highlight(word if word and card.has_word(word) else None)
            linked = card._runs[card.linked_run][3] if card.linked_run is not None else []
            self.viewer.show_link(linked, scroll=False)  # don't jump away from where you clicked
        else:
            self.viewer.show_link([], scroll=False)
        self.viewer.mark_word(word or None, within)

    def _function_at(self, line: int, col: int) -> FunctionInfo | None:
        around = [f for f in self._functions if f.has_body and f.span is not None and f.span.contains(line, col)]
        return min(around, key=lambda f: f.span.size) if around else None

    def _on_list_clicked(self, item: QTreeWidgetItem) -> None:
        self.viewer.go_to_line(item.data(0, _LINE_ROLE))

    def _on_list_context_menu(self, pos) -> None:
        item = self.functions_list.itemAt(pos)
        if item is None or item.data(0, _FUNCTION_INDEX_ROLE) is None:
            return  # empty space or a variable row
        function = self._functions[item.data(0, _FUNCTION_INDEX_ROLE)]
        menu = QMenu(self)
        add = menu.addAction(f"Add Card: {function.name}")
        if menu.exec(self.functions_list.viewport().mapToGlobal(pos)) is add:
            self.add_function_card(function)

    # --- the diagram ------------------------------------------------------

    def _diagram_panel(self) -> QWidget:
        """The card canvas with its header: title, card count, zoom and a
        button back to 100%."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        title = QLabel("Diagram")
        title.setObjectName("panelTitle")
        self.card_count = QLabel()
        self.card_count.setObjectName("panelMessage")
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("panelMessage")
        reset = QPushButton("100%")
        reset.setObjectName("smallButton")
        reset.setToolTip("Reset zoom (the scroll wheel zooms the diagram)")
        reset.clicked.connect(lambda: self.cards.set_zoom(1.0))
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(self.card_count)
        header.addStretch(1)
        header.addWidget(self.zoom_label)
        header.addWidget(reset)
        layout.addLayout(header)
        layout.addWidget(self.cards, stretch=1)

        self.cards.cards_changed.connect(self._show_card_count)
        self.cards.zoom_changed.connect(self._show_zoom)
        self._show_card_count(0)
        self._show_zoom(1.0)
        return panel

    def _show_card_count(self, count: int) -> None:
        text = "Right-click a function in the code > Add Card" if count == 0 else _plural(count, "card")
        self.card_count.setText(text)

    def _show_zoom(self, zoom: float) -> None:
        self.zoom_label.setText(f"{round(zoom * 100)}%")

    def add_function_card(self, function: FunctionInfo) -> None:
        """Puts function on the card canvas. A C prototype's card is the
        function's own, from its definition (which has the variables), when
        the file has one."""
        if not function.has_body:
            definition = next((f for f in self._functions if f.has_body and f.name == function.name), None)
            function = definition or function
        self.cards.add_card(function, self._language)

    def card_target(self, word: str, line: int) -> FunctionInfo | None:
        """The function a right-click in the code offers a card for: the one
        named by word (its definition, a prototype or a call to it), if it's
        defined in this file; otherwise the function whose body holds line
        (the innermost, for nested Python functions)."""
        defined = [f for f in self._functions if f.has_body]
        if word:
            # Python methods are named Class.method, C++ ones Class::method.
            named = [f for f in defined if re.split(r"\.|::", f.name)[-1] == word]
            if named:
                around = [f for f in named if f.line <= line <= f.end_line]
                return (around or named)[0]
        around = [f for f in defined if f.line <= line <= f.end_line]
        return max(around, key=lambda f: f.line) if around else None

    def _apply_card_filters(self) -> None:
        self.cards.apply_filters(self.show_functions.isChecked(), self.show_methods.isChecked(),
                                 self.show_variables.isChecked(), self.show_logic.isChecked())

    def _save_dividers(self) -> None:
        self._settings.setValue(_DIVIDER_KEY, self.splitter.saveState())
        self._settings.setValue(_CODE_DIVIDER_KEY, self.code_splitter.saveState())

    def reopen_last_file(self) -> None:
        """Opens the file that was open last time, if it's still there."""
        last = self._settings.value(_LAST_FILE_KEY, "")
        if last and Path(last).is_file():
            self.open_file(Path(last))

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
