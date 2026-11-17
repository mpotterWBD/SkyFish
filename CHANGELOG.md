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
- **C read with tree-sitter** (`analysis/c_parser.py`): `.c` / `.h` files are parsed with a real C
  grammar (a ready-built wheel - no compiler needed) instead of the brace scanner, which C++ still uses.
  Same functions, variables and logic as before on Mike's firmware, plus the exact code location
  (`Span`) of every function, declaration, if header and body, and return - Python files too.
- **Cards tied to the code**: click a line on a card and the code it came from is highlighted and
  centred in the code view - a variable's declaration, an input's parameter, an if's header and the
  return it leaves through, or every return behind an output - with a clicked word marked wherever it's
  used in that function. Click in the code and the matching line on that function's card lights up (the
  declaration, if header or return clicked on, else the innermost if around it), and the word is marked
  on the card and through the function's code. Clicking in the code doesn't move the diagram.
- **Ctrl+click a variable to see its value**: in the code view, Ctrl+click an input that has a value
  card value and that value is shown over every use of it in the function - the name faded but still
  faintly visible, the value on top in white, cut short with `…` where the name is narrower (hover for
  the whole value). Uses in comments and strings are left alone. Several can show at once; Ctrl+click
  one again to hide it. Changing the value card updates them straight away. Ctrl only needs to be
  held when the button goes down (letting go of it first used to make it a plain click).
- **Saving diagrams** (`diagram_file.py`): **File > Save** (Ctrl+S) and **Save As...** (Ctrl+Shift+S)
  write a `.skyfish` file (readable JSON) - the cards and where they sit, value cards with what was
  typed and their NULL tickbox, the wires, the zoom and the view. Save asks where the first time,
  suggesting a file beside the code with the same name (`stm32PacketDriver.skyfish`), then just updates
  it. Opening a code file brings its diagram back (the file it was last saved to, or the one beside it).
  A card's function is matched by name in today's code, so an edited function's card shows its current
  code; a function that's gone (renamed or deleted) keeps its card, drawn from the saved code with a red
  X across everything below its title bar, no longer tied to the code. Value cards whose input is gone
  are dropped; the summary line says which.
- **Close X on every card**: function cards and value cards have an ✕ at the right of the title bar;
  closing a function card closes its value cards too.
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
- **Black-box back end** - the analysis side of call/assignment rows on cards:
  - **Actions in the flow tree** (`analysis/logic.py`, `analysis/c_parser.py`): bare calls,
    assignments (`x = ...`, `x += ...`, `x++`) and declarations initialized by a call
    (`uint16_t len = snprintf(...)`) ride along in a function's flow as `Action` nodes - the
    statement as written, the target, the called function and each argument as written, an
    evaluable value expression (`x += 2` carries `x + (2)`), line and span. `actions(flow)`
    gathers them in source order; the logic rows are unchanged by them.
  - **Stdlib contracts** (`analysis/stdlib_c.py`): `call_effect(name, args)` fills in a call's
    box from the C standard's contract alone - the return value and what it writes
    (`snprintf(buf, 8, "T=%d\r", 125)` returns 6 and leaves `"T=125\r"` in buf). Covers
    string.h (strlen/strcmp/strchr/strstr/memcmp and the copies, with C-string `\0` rules), a
    printf subset (flags, width, precision, `*`; `%u` of a negative refuses rather than
    guesses a width), ctype.h, atoi/abs, and common math.h. A function it doesn't know returns
    None - the card's "?" box. `evaluate_condition` uses the contracts too, so
    `strcmp(key, "TEMP") == 0` now resolves - and blames an unresolved argument, not the call.
  - **#define extractor** (`analysis/defines.py`): `defines(text)` reads a file's object-like
    macros into evaluator values - numbers, chars, strings, expressions over earlier defines,
    continuation lines folded, comments stripped. Function-like macros, bare flags and register
    tricks are quietly left out. Merge under the card values so the operator always wins:
    `{**defines(text), **card_values}`.
  - **compute_expression()** (`analysis/evaluate.py`): the evaluator's value rather than just
    its truth - for a #define's body, or what an assignment's right-hand side comes to.
- **Runner** (`analysis/simulate.py`): `simulate(function, inputs, defines)` runs a function top
  to bottom with the card values, carrying state - `used = strlen(dataCSV)` feeds the next if -
  and returns a Trace: a Step per flow node (keyed by its Span) with status `ran` / `taken` /
  `not taken` / `skipped` / `unknown`, a call's return value and what it wrote, and why anything
  unknown is unknown; plus the output reached and every name's final value. Locals pick up their
  plain initializers (`int used = 0;` - new `VariableInfo.initial`). Honesty rules: a loop's
  body runs zero-or-many times, not once, so its steps are unknown and what they assign is
  forgotten; a return inside a loop or an undecidable if makes the final output uncertain even
  when a later return is sure; a call with no contract could have rewritten whatever its pointer
  arguments reach, so they're forgotten too. A contract that can't answer now blames the exact
  argument it needed (`CallEffect.blocked`) - snprintf never blames its buffer. `Action` also
  carries `context` ("in loop" / "in switch", like `Exit.context`).
- **sizeof resolves** (B1): `sizeof(name)` from the name's declared type (`char number[12]` ->
  12, `uint16_t` -> 2, pointers -> 4; an array parameter is a pointer), `sizeof(type)` from the
  32-bit ARM sizes values.py uses, and an array sized by a #define (`uint8_t[TX_MAX]`) resolves
  when the define's value is known. Structs, unknown typedefs and sizeof of an expression stay
  unknown, as written. `evaluate_condition` / `compute_expression` take an optional `types`
  mapping ({name: declared type}); `simulate` fills it from the function's variables - so the
  real `addKeyValuePair` now runs every step to its output.
- **Remembered between sessions** (per user, `QSettings`): the last file (reopened at start-up if it's
  still there), the recent files, the panel dividers, and the checkboxes.
