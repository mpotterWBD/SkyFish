import pytest

from skyfish.analysis.functions import (
    FUNCTION,
    LOCAL,
    METHOD,
    PARAMETER,
    UnsupportedLanguage,
    find_functions,
)

# A C/C++ prototype (no body), as the list shows it.
DECLARATION = "declaration"


def names(found):
    return [(f.name, f.kind if f.has_body else DECLARATION, f.line) for f in found]


def variables(found, name):
    [f] = [f for f in found if f.name == name and f.has_body]
    return [(v.name, v.kind, v.line) for v in f.variables]


def test_python_variables():
    text = (
        "class A:\n"                                        # 1
        "    def run(self, a, b=2, *args, c, **kw):\n"      # 2
        "        global G\n"                                # 3
        "        G = 1\n"                                   # 4
        "        x = a + b\n"                               # 5
        "        x += 1\n"                                  # 6
        "        self.attr = 3\n"                           # 7
        "        for i, (j, k) in enumerate(args):\n"       # 8
        "            total: int = i\n"                      # 9
        "        try:\n"                                    # 10
        "            pass\n"                                # 11
        "        except ValueError as err:\n"               # 12
        "            pass\n"                                # 13
        "        squares = [n * n for n in args]\n"         # 14
        "        def helper(h):\n"                          # 15
        "            inner_only = h\n"                      # 16
        "        if (m := len(args)):\n"                    # 17
        "            pass\n"                                # 18
    )
    found = find_functions(text, ".py")
    assert variables(found, "A.run") == [
        ("a", PARAMETER, 2), ("b", PARAMETER, 2), ("args", PARAMETER, 2),
        ("c", PARAMETER, 2), ("kw", PARAMETER, 2),
        ("x", LOCAL, 5), ("i", LOCAL, 8), ("j", LOCAL, 8), ("k", LOCAL, 8),
        ("total", LOCAL, 9), ("err", LOCAL, 12), ("squares", LOCAL, 14), ("m", LOCAL, 17),
    ]
    assert variables(found, "A.run.helper") == [("h", PARAMETER, 15), ("inner_only", LOCAL, 16)]


def test_c_variables():
    text = (
        "static uint8_t run(const char *line, uint8_t buf[], void (*cb)(int), struct cfg *c) {\n"  # 1
        "    uint8_t a = 1, b, *p = &a;\n"                  # 2
        "    char tx[TX_MAX];\n"                            # 3
        "    static const char *verdict;\n"                 # 4
        "    HAL_StatusTypeDef st = HAL_Init(x, y);\n"      # 5
        "    a = b;\n"                                      # 6
        "    foo(a, b);\n"                                  # 7
        "    return a > b ? a : b;\n"                       # 8
        "    for (int i = 0; i < 4; i++) {\n"               # 9
        "        uint16_t inner = i;\n"                     # 10
        "    }\n"                                           # 11
        "    if (a) { struct cfg local_cfg = {0}; }\n"      # 12
        "}\n"                                               # 13
        "int no_params(void) { return 0; }\n"               # 14
    )
    found = find_functions(text, ".c")
    assert variables(found, "run") == [
        ("line", PARAMETER, 1), ("buf", PARAMETER, 1), ("cb", PARAMETER, 1), ("c", PARAMETER, 1),
        ("a", LOCAL, 2), ("b", LOCAL, 2), ("p", LOCAL, 2), ("tx", LOCAL, 3), ("verdict", LOCAL, 4),
        ("st", LOCAL, 5), ("i", LOCAL, 9), ("inner", LOCAL, 10), ("local_cfg", LOCAL, 12),
    ]
    assert variables(found, "no_params") == []


def types(found, name):
    [f] = [f for f in found if f.name == name and f.has_body]
    return {v.name: v.type for v in f.variables}


def test_c_variable_types():
    text = (
        "static uint8_t run(const char *line, uint8_t buf[], void (*cb)(int), struct cfg *c) {\n"
        "    uint8_t a = 1, b, *p = &a, **pp;\n"
        "    char tx[TX_MAX];\n"
        "    static const char *verdict;\n"
        "    const uint8_t packet[] = {1, 2};\n"
        "    for (int i = 0; i < 4; i++) {}\n"
        "}\n"
    )
    assert types(find_functions(text, ".c"), "run") == {
        "line": "const char *", "buf": "uint8_t[]", "cb": "void (*)(int)", "c": "struct cfg *",
        "a": "uint8_t", "b": "uint8_t", "p": "uint8_t *", "pp": "uint8_t **",
        "tx": "char[TX_MAX]", "verdict": "static const char *", "packet": "const uint8_t[]", "i": "int",
    }


def test_python_variable_types():
    text = (
        "def run(path: Path, n):\n"
        "    count = 0\n"
        "    ratio = 1.5\n"
        "    name = f'{n}'\n"
        "    items = []\n"
        "    lookup = {k: 1 for k in n}\n"
        "    ok = n == 3\n"
        "    label = QtWidgets.QLabel('x')\n"
        "    link = None\n"
        "    link = MockLink()\n"
        "    total: float = n\n"
        "    total = 'later assignments do not change an annotation'\n"
        "    result = compute(n)\n"
        "    try:\n"
        "        pass\n"
        "    except (OSError, ValueError) as err:\n"
        "        pass\n"
    )
    assert types(find_functions(text, ".py"), "run") == {
        "path": "Path", "n": "",
        "count": "int", "ratio": "float", "name": "str", "items": "list", "lookup": "dict",
        "ok": "bool", "label": "QLabel", "link": "MockLink", "total": "float", "result": "",
        "err": "(OSError, ValueError)",
    }


def test_python_functions_methods_and_nested():
    text = (
        "import os\n"                      # 1
        "\n"                               # 2
        "def top():\n"                     # 3
        "    def inner():\n"               # 4
        "        pass\n"                   # 5
        "\n"                               # 6
        "class Window:\n"                  # 7
        "    @property\n"                  # 8
        "    def title(self):\n"           # 9
        "        return 1\n"               # 10
        "    async def load(self):\n"      # 11
        "        pass\n"                   # 12
        "    class Inner:\n"               # 13
        "        def go(self): pass\n"     # 14
        "if os.name == 'nt':\n"            # 15
        "    def windows_only(): pass\n"   # 16
    )
    assert names(find_functions(text, ".py")) == [
        ("top", FUNCTION, 3),
        ("top.inner", FUNCTION, 4),
        ("Window.title", METHOD, 9),
        ("Window.load", METHOD, 11),
        ("Window.Inner.go", METHOD, 14),
        ("windows_only", FUNCTION, 16),
    ]


def test_python_syntax_error_is_raised():
    with pytest.raises(SyntaxError):
        find_functions("def broken(:\n", ".py")


def test_c_definitions_and_prototypes():
    text = (
        '#include "main.h"\n'                                   # 1
        "#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"           # 2
        "static uint8_t helper(uint8_t x);\n"                   # 3
        "static uint8_t buf[SIZE(4)];\n"                        # 4
        "int total = count(3);\n"                               # 5
        "/* void commented(void) { } */\n"                      # 6
        "const char *msg = \"fake(void) {\";\n"                 # 7
        "\n"                                                    # 8
        "static uint8_t helper(uint8_t x)\n"                    # 9
        "{\n"                                                   # 10
        "    if (x) { return call(x); }\n"                      # 11
        "    return 0;\n"                                       # 12
        "}\n"                                                   # 13
        "__attribute__((weak)) void HAL_Hook(void) {}\n"        # 14
        "typedef void (*callback_t)(int);\n"                    # 15
        "struct s { void (*cb)(int); int n; };\n"               # 16
        "int main(void) {\n"                                    # 17
        "    while (1) {}\n"                                    # 18
        "}\n"                                                   # 19
    )
    assert names(find_functions(text, ".c")) == [
        ("helper", DECLARATION, 3),
        ("helper", FUNCTION, 9),
        ("HAL_Hook", FUNCTION, 14),
        ("main", FUNCTION, 17),
    ]


def test_c_extern_c_block_is_looked_inside():
    text = '#ifdef __cplusplus\nextern "C" {\n#endif\nvoid Error_Handler(void);\n#ifdef __cplusplus\n}\n#endif\n'
    assert names(find_functions(text, ".h")) == [("Error_Handler", DECLARATION, 4)]


def test_cpp_classes_and_qualified_methods():
    text = (
        "namespace app {\n"                         # 1
        "class Motor : public Device {\n"           # 2
        "public:\n"                                 # 3
        "    Motor();\n"                            # 4
        "    int speed() const { return s; }\n"     # 5
        "private:\n"                                # 6
        "    int s;\n"                              # 7
        "};\n"                                      # 8
        "Motor::Motor() : s(0) {}\n"                # 9
        "void Motor::stop() {}\n"                   # 10
        "}\n"                                       # 11
    )
    assert names(find_functions(text, ".cpp")) == [
        ("Motor::Motor", DECLARATION, 4),
        ("Motor::speed", METHOD, 5),
        ("Motor::Motor", METHOD, 9),
        ("Motor::stop", METHOD, 10),
    ]


def test_unsupported_extension():
    with pytest.raises(UnsupportedLanguage):
        find_functions("x", ".txt")
