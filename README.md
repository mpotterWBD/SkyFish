# SkyFish
Code Analytics Software

A PySide6 (Qt) desktop app, built on the same framework as oilIQ. Open a
file with **File > Open File...** (Ctrl+O) or **File > Open Recent**:

- **Left:** the code, read-only with line numbers. Right-click a function >
  **Add Card** to put it on the diagram.
- **Middle:** a narrow list of the file's functions. Click one to jump to it;
  right-click > **Add Card**.
- **Right:** a summary of the file's functions, checkboxes for what cards
  show, and the diagram: one card per function, showing its variables and its
  logic (inputs, if-branches and outputs). Drag a card by its title bar, click
  a word to mark it everywhere in the card, scroll to zoom.

Supported so far: Python (`.py`) and C/C++ (`.c .h .cpp .cc .cxx .hpp .hh`).

## Running

From this folder:

```powershell
uv run skyfish
```

You can also double-click `launch_skyfish.bat`, or in VS Code pick
**SkyFish Code Analytics** in Run and Debug. If Smart App Control blocks
`uv run skyfish` with "os error 4551", use `uv run python -m skyfish`.

## Testing

```powershell
uv run pytest tests/
```

## Versioning

The app version is in `src/skyfish/version.py`, and `version` in
`pyproject.toml` is kept the same. Each version has an entry in
`CHANGELOG.md`.
