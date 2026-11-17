import pytest

from skyfish.diagram_file import (
    CardState,
    DiagramFileError,
    DiagramState,
    ValueCardState,
    code_file_for,
    default_path,
    load,
    relative_code_file,
    save,
)


def test_round_trip(tmp_path):
    code = tmp_path / "drv.c"
    code.write_text("int f(void) { return 0; }\n")
    path = default_path(code)
    assert path.name == "drv.skyfish"
    state = DiagramState(
        relative_code_file(path, code), 0.75, (120.0, 80.0),
        [CardState("f", 1, "int f(void) { return 0; }", 400.0, 30.0,
                   [ValueCardState("size", "32", False, 50.0, 60.0)])],
    )
    save(path, state)
    loaded = load(path)
    assert loaded == state
    assert code_file_for(path, loaded) == code.resolve()
    assert '"skyfish_diagram": 1' in path.read_text()  # readable JSON


def test_bad_files_say_why(tmp_path):
    path = tmp_path / "x.skyfish"
    path.write_text("not json")
    with pytest.raises(DiagramFileError, match="Couldn't read x.skyfish"):
        load(path)
    path.write_text('{"something": "else"}')
    with pytest.raises(DiagramFileError, match="isn't a SkyFish diagram"):
        load(path)
