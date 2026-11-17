"""Saving and loading a diagram: File > Save / Save As write a .skyfish file.

A .skyfish file is JSON, so it reads (and diffs in git) as plain text. It
holds what's needed to put a diagram back exactly as it was left: each card
(which function, where it sits, and the function's code as it was when
saved), each value card (which input, what was typed, the NULL tickbox,
where it sits), the zoom, and where the view was looking.

The function's code is kept so a card can still be drawn if that function
has since been renamed or deleted - it's shown crossed out. A function that
still exists is matched by name and drawn from the current code instead, so
an edited function's card simply updates.

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT = 1
SUFFIX = ".skyfish"


class DiagramFileError(Exception):
    """A .skyfish file that can't be read, worded for the person opening it."""


@dataclass
class ValueCardState:
    parameter: str
    text: str
    null: bool
    x: float
    y: float


@dataclass
class CardState:
    function: str            # its name as SkyFish lists it ("addKeyValuePair", "Window.load")
    line: int                # where it was, to choose between two of the same name
    source: str              # its code when saved, to draw it if it's gone from the file
    x: float
    y: float
    values: list[ValueCardState] = field(default_factory=list)


@dataclass
class DiagramState:
    code_file: str           # the code file, relative to the .skyfish file when it can be
    zoom: float = 1.0
    center: tuple[float, float] = (0.0, 0.0)  # the canvas point in the middle of the view
    cards: list[CardState] = field(default_factory=list)


def default_path(code_file: Path) -> Path:
    """Where Save suggests putting a code file's diagram: beside it, same name."""
    return Path(code_file).with_suffix(SUFFIX)


def save(path: Path, state: DiagramState) -> None:
    data = {"skyfish_diagram": FORMAT, **asdict(state)}
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load(path: Path) -> DiagramState:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiagramFileError(f"Couldn't read {Path(path).name}: {exc}") from None
    if not isinstance(data, dict) or data.get("skyfish_diagram") != FORMAT:
        raise DiagramFileError(f"{Path(path).name} isn't a SkyFish diagram this version can read")
    try:
        cards = [CardState(**{**c, "values": [ValueCardState(**v) for v in c.get("values", [])]})
                 for c in data.get("cards", [])]
        return DiagramState(data["code_file"], float(data.get("zoom", 1.0)),
                            tuple(data.get("center", (0.0, 0.0))), cards)
    except (KeyError, TypeError) as exc:
        raise DiagramFileError(f"{Path(path).name} is missing something: {exc}") from None


def code_file_for(diagram_path: Path, state: DiagramState) -> Path:
    """The code file a diagram belongs to, as an absolute path."""
    stored = Path(state.code_file)
    return stored if stored.is_absolute() else (Path(diagram_path).parent / stored).resolve()


def relative_code_file(diagram_path: Path, code_file: Path) -> str:
    """code_file as stored in the diagram: relative to it when they share a
    drive (so the pair can move together), absolute otherwise."""
    try:
        return Path(os.path.relpath(Path(code_file).resolve(), Path(diagram_path).resolve().parent)).as_posix()
    except ValueError:  # different drives on Windows
        return str(Path(code_file).resolve())
