# SkyFish Code Analytics - Changelog

Versions follow `src/skyfish/version.py` (MAJOR.MINOR.PATCH); `version` in `pyproject.toml` is kept
the same. The version is shown in the window title.

Bump rules (raising a number resets every number to its right to 0):
- **MAJOR** - a change that breaks existing saved data or behaviour people rely on
- **MINOR** - a new feature or window
- **PATCH** - a fix or wording/layout change only

Versions are listed oldest first - the newest is at the bottom.

## 0.1.0 - 2026-11-16

First version.

### Added
- **PySide6 app on the oilIQ framework**: `uv`, `src/` layout, the same dark theme, `version.py`, and
  headless pytest. `launch_skyfish.bat` (the Smart-App-Control-safe launcher, like oilIQ's) and a VS
  Code Run and Debug configuration.
- **Menu bar**: **File** (New Ctrl+N - a blank window: no code, empty list and diagram, and a blank
  start next time; Open File... Ctrl+O, Open Recent - the last 8 files, newest first - and
  Exit), **View** (Functions / Methods / Variables / Logic, kept in step with the panel's checkboxes;
  Zoom In Ctrl+=, Zoom Out Ctrl+-, Reset Zoom Ctrl+0) and **Help** (About SkyFish).
- **Window**: across the top, the window's full width, the SkyFish logo with the version beside it,
  then the menu bar under it. Below, two columns with a draggable divider: the code viewer and the
  functions list (with their own divider), and the analysis panel. When the window is resized, only
  the analysis side changes width.
- **Functions list**: a narrow column of the file's functions and methods (names in the code's function
  colour). Click one to jump to it in the code; right-click > **Add Card**; hover for its kind and line.
  The Functions / Methods checkboxes filter it, and Variables adds each one's local variables under it,
  written and coloured as declared (`ui/spans_delegate.py`). A C prototype is listed (in italics) only
  when the file doesn't define that function.
- **Code viewer**: read-only, with line numbers. Syntax colouring for Python and C/C++ in VS Code's Dark+
  palette (`ui/highlighter.py`); tabs drawn 4 spaces wide (`TAB_WIDTH_SPACES` in `ui/code_viewer.py`).
  Right-click > **Add Card: <name>** puts a function on the diagram: on a function's name (definition,
  prototype or a call) it's that function, if it's defined in the file; anywhere else in a function's
  body, it's the function you're in.
- **Function finder** (`analysis/functions.py`): Python (exact, using Python's own parser) and C/C++
  (`.c .h .cpp .cc .cxx .hpp .hh`, a brace-aware scanner). Finds functions, methods and C prototypes,
  each one's parameters and local variables with their types (C/C++ as declared; Python from an
  annotation, or the first value assigned when it's clear), and its logic (below). The analysis panel's
  summary line counts them, e.g. "26 functions · 0 methods · 21 declarations · 21 variables".
- **Diagram** (`ui/card_canvas.py`): one card per function added. A card shows its name on a title bar,
  its local variables written and coloured as declared in the code, and its logic. A card moves only by
  its title bar; clicking a word anywhere else in it marks that word wherever it appears in the card (the
  same word again, or a blank spot, clears it). Right-click a card > **Remove Card**. Adding one that's
  already there selects it; a C prototype's card is its definition's. Drag empty canvas to pan; the
  scroll wheel zooms around the pointer (25% to 300%). The header shows the card count, the zoom %, and a
  **100%** button. Cards are cleared when another file is opened.
- **Card logic** (`analysis/logic.py`): a card shows its function as paths from inputs to outputs.
  Inputs are the parameters, each with a blue port on the left edge. Each if / else-if / else branch is
  a row (◆ and its condition as written), indented by nesting; a branch that returns has a wire to its
  output, one that doesn't is a detour the flow continues after. Outputs are green ports on the right
  edge, one per distinct return value (identical returns share one; `end` is a bare return or running
  off the end). Exit rows that aren't a branch read `always` / `otherwise`. Loops, `switch`, `try` and
  `match` aren't drawn yet: the ifs inside them show, and a return inside one is labelled `in loop`,
  `in switch`, `in except …` or `in case …`, since it only sometimes runs. A loop that never ends
  (`while(1)`, `for(;;)`, `while True:`) with no `break` of its own stops the flow at a quiet
  **runs forever** row, so the function has no `end` output (found on `stm32PacketDriver.c`'s
  `waitForBusQuiet`).
- **Value cards** (`ui/value_card.py`, `analysis/values.py`): drag a card's blue input dot out onto the
  canvas and a value card appears where it's dropped, wired to that input. Type a value; it's checked
  against the parameter's type as you type - integers by their range (`uint16_t` 0 to 65,535, with
  `0x`/`0b`/`'A'` accepted; sizes for 32-bit ARM, so `int`/`long`/`size_t` are 32 bits), `char *` /
  `char[N]` as text with `\r \n \t \0 \xNN` escapes (`char[N]` holds N-1 characters), `uint8_t *` as hex
  bytes, `bool`, `float`/`double`, and Python's `int`/`str`/`float`/`bool` - and a red message says
  what's wrong ("Too big for uint16_t (0 to 65,535)"). Pointers have a **NULL** tickbox. A good value is
  stored for that input and shown on its row (`const char *KEY = "TEMP"`). The wire follows the cards
  as they move; right-click a value card > **Remove Value Card**. Value cards hide while Logic is
  unticked, and go with their function card.
- **Checkboxes** for what the list and cards show: **Functions** / **Methods** (those rows and cards),
  **Variables** (the variable rows and lines) and **Logic** (cards' inputs, branches and outputs).
- **Logo** (`assets/`): a monoline fish in the Westbound Designs style and blue (`#00AEEF`), facing
  left, its tail the Westbound arrow's nested chevrons, above a wave. As the mark alone, in a circle
  (icon, plus `skyfish.ico`), and with "SkyFish / Code Analytics"; SVG and PNG. Regenerate with
  `uv run python assets/make_logo.py`. `skyfish_logo.png` sits in the window's top-left corner
  (`LOGO_HEIGHT_PX` in `ui/main_window.py`), loaded from `assets/` so a regenerated logo shows up.
- **Condition evaluator** (`analysis/evaluate.py`): given input values (what value cards hold),
  says whether a C if condition is true, false or unknown - and when it's unknown, lists what
  stopped it (names with no value, calls like `strcmp(a, b)`, struct fields, array elements), in
  the order met. Parsed with the same tree-sitter C grammar as `c_parser.py` and evaluated with
  C's rules: `||`/`&&` short-circuit (a side that settles the answer makes the other side's
  unknowns irrelevant), `!`, comparisons, `== NULL` (a value card's NULL tickbox; a string value
  is a real pointer, true even when empty), integer arithmetic (division truncates toward zero,
  `-7 % 2` is `-1`, hex/octal/binary/char literals, `100U` suffixes), casts that wrap to the C
  type's bits (`(uint8_t)300` is 44, values.py's 32-bit ARM sizes), `?:`, and the firmware
  `TRUE`/`FALSE` macros. Comparing two strings is left unknown on purpose - C compares the
  pointers, not the text. No UI: for the cards to light up the taken path later.
- **Remembered between sessions** (per user, `QSettings`): the last file (reopened at start-up if it's
  still there), the recent files, the panel dividers, and the checkboxes.
