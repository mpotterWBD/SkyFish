from skyfish.analysis.functions import find_functions
from skyfish.analysis.logic import ALWAYS, END, FOREVER, OTHERWISE, logic_rows, outputs


def rows_of(text, suffix, name):
    [f] = [f for f in find_functions(text, suffix) if f.name == name and f.has_body]
    rows = logic_rows(f.flow, f.end_line)
    return [(r.indent, r.keyword, r.condition, r.exit) for r in rows], outputs(rows)


C_DISPATCH = """
static uint8_t handle(const char *line) {
    char tx[8];
    if(strcmp(line, "a{") == 0) {   // a brace inside a string
        do_a();
        return 1U;
    }
    if(strcmp(line, "b") == 0) {
        if(ready()) {
            verdict = "pass";
        }
        else if(failed())
            verdict = "fail";
        else {
            verdict = "none";
        }
        send(verdict);
        return 1U;
    }
    for (i = 0; i < 3; i++) {
        if (i == 2) return 2U;
    }
    return 0U;
}
"""


def test_c_dispatcher_rows_and_outputs():
    rows, outs = rows_of(C_DISPATCH, ".c", "handle")
    assert rows == [
        (0, "if", 'strcmp(line, "a{") == 0', "1U"),
        (0, "if", 'strcmp(line, "b") == 0', "1U"),
        (1, "if", "ready()", None),
        (1, "else if", "failed()", None),
        (1, "else", "", None),
        (0, "if", "i == 2", "2U"),       # inside the for loop (loops see-through for now)
        (0, OTHERWISE, "", "0U"),
    ]
    assert outs == ["1U", "2U", "0U"]


def test_c_void_function_guard_and_end():
    text = (
        "void cb(UART_HandleTypeDef *huart) {\n"
        "    if (huart->Instance != USART1) {\n"
        "        return;\n"
        "    }\n"
        "    if (x) { y = 1; } else { y = 2; }\n"
        "}\n"
    )
    rows, outs = rows_of(text, ".c", "cb")
    assert rows == [
        (0, "if", "huart->Instance != USART1", END),
        (0, "if", "x", None),
        (0, "else", "", None),
        (0, OTHERWISE, "", END),
    ]
    assert outs == [END]


def test_c_if_else_that_both_return_has_no_end():
    text = "int f(int a) {\n    if (a) return 1;\n    else return 2;\n}\n"
    rows, outs = rows_of(text, ".c", "f")
    assert rows == [(0, "if", "a", "1"), (0, "else", "", "2")]
    assert outs == ["1", "2"]


def test_c_no_ifs_is_always_end():
    rows, outs = rows_of("void f(void) {\n    g();\n}\n", ".c", "f")
    assert rows == [(0, ALWAYS, "", END)]


def test_python_if_elif_else_and_returns():
    text = (
        "def f(a, b):\n"
        "    if a is None:\n"
        "        return\n"
        "    if a > b:\n"
        "        x = 1\n"
        "    elif a == b:\n"
        "        if b:\n"
        "            return 'same'\n"
        "    else:\n"
        "        pass\n"
        "    try:\n"
        "        if b < 0:\n"
        "            return -1\n"
        "    except ValueError:\n"
        "        return None\n"
        "    return a + b\n"
    )
    rows, outs = rows_of(text, ".py", "f")
    assert rows == [
        (0, "if", "a is None", END),
        (0, "if", "a > b", None),
        (0, "elif", "a == b", None),
        (1, "if", "b", "'same'"),
        (0, "else", "", None),
        (0, "if", "b < 0", "-1"),         # inside try (see-through)
        (0, "in except ValueError", "", "None"),  # only sometimes: doesn't end the flow
        (0, OTHERWISE, "", "a + b"),
    ]
    assert outs == [END, "'same'", "-1", "None", "a + b"]


def test_return_before_any_exit_is_always():
    text = "def f(x):\n    if x:\n        print(x)\n    return x\n"
    rows, _ = rows_of(text, ".py", "f")
    assert rows == [(0, "if", "x", None), (0, ALWAYS, "", "x")]


def test_returns_in_loops_and_switch_are_conditional():
    text = (
        "int f(int n) {\n"
        "    while (n--) {\n"
        "        if (n == 3) { break; }\n"
        "        return 9;\n"
        "    }\n"
        "    switch (n) {\n"
        "        case 1: return 1;\n"
        "        default: break;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    rows, outs = rows_of(text, ".c", "f")
    assert rows == [
        (0, "if", "n == 3", None),
        (0, "in loop", "", "9"),
        (0, "in switch", "", "1"),
        (0, OTHERWISE, "", "0"),
    ]
    assert outs == ["9", "1", "0"]


def test_python_return_in_loop_does_not_end_the_function():
    text = "def f(items):\n    for i in items:\n        return i\n"
    rows, outs = rows_of(text, ".py", "f")
    assert rows == [(0, "in loop", "", "i"), (0, OTHERWISE, "", END)]


def test_c_forever_loop_has_no_end_output():
    # stm32PacketDriver.c's waitForBusQuiet: it only leaves through its returns.
    text = (
        "bool waitForBusQuiet(void) {\n"
        "    while(1) {\n"
        "        if (quiet()) { return true; }\n"
        "        if (timed_out()) { return false; }\n"
        "    }\n"
        "}\n"
    )
    rows, outs = rows_of(text, ".c", "waitForBusQuiet")
    assert rows == [(0, "if", "quiet()", "true"), (0, "if", "timed_out()", "false"), (0, FOREVER, "", None)]
    assert outs == ["true", "false"]


def test_c_loop_with_break_or_real_condition_still_ends():
    text = (
        "void a(void) {\n    for (;;) {\n        if (done()) { break; }\n    }\n}\n"
        "void b(void) {\n    while (n > 0) { n--; }\n}\n"
        "void c(void) {\n    while (1) {\n        switch (x) { case 1: break; }\n    }\n}\n"
        "void d(void) {\n    do { poll(); } while (1);\n}\n"
    )
    assert rows_of(text, ".c", "a")[1] == [END]       # its own break: the loop can end
    assert rows_of(text, ".c", "b")[1] == [END]       # a real condition
    assert rows_of(text, ".c", "c")[1] == []          # that break ends the switch, not the loop
    assert rows_of(text, ".c", "d")[1] == []          # do { } while (1)


def test_python_while_true():
    text = (
        "def serve(q):\n    while True:\n        if q.stop:\n            return 1\n"
        "def poll(q):\n    while True:\n        if q.empty():\n            break\n"
    )
    assert rows_of(text, ".py", "serve")[1] == ["1"]
    assert rows_of(text, ".py", "poll")[1] == [END]

ACTION_TEXT = """
static uint8_t reply(const char *key, uint8_t slot) {
    char tx[TX_MAX];
    uint16_t len = snprintf(tx, sizeof(tx), "T=%d\\r", slot);
    if (len == 0) {
        logError(len);
        return 0;
    }
    retries = 0;
    retries += 2;
    slot++;
    sendPacket(tx, len);
    return 1;
}
"""


def test_actions_calls_and_assignments():
    from skyfish.analysis.logic import actions

    [f] = [f for f in find_functions(ACTION_TEXT, ".c") if f.name == "reply"]
    found = [(a.target, a.call, a.args, a.expression) for a in actions(f.flow)]
    assert found == [
        ("len", "snprintf", ("tx", "sizeof(tx)", "\"T=%d\\r\"", "slot"),
         "snprintf(tx, sizeof(tx), \"T=%d\\r\", slot)"),
        ("", "logError", ("len",), ""),          # inside the if branch
        ("retries", "", (), "0"),
        ("retries", "", (), "retries + (2)"),    # += written as its result
        ("slot", "", (), "slot + 1"),            # ++ likewise
        ("", "sendPacket", ("tx", "len"), ""),
    ]
    assert [a.text for a in actions(f.flow)][:2] == [
        "uint16_t len = snprintf(tx, sizeof(tx), \"T=%d\\r\", slot)",
        "logError(len)",
    ]
    assert all(a.span is not None and a.span.line == a.line for a in actions(f.flow))


def test_actions_do_not_disturb_the_logic_rows():
    rows, outs = rows_of(ACTION_TEXT, ".c", "reply")
    assert rows == [(0, "if", "len == 0", "0"), (0, OTHERWISE, "", "1")]
    assert outs == ["0", "1"]


def test_plain_declarations_are_not_actions():
    from skyfish.analysis.logic import actions

    text = "void f(void) {\n    int i = 0;\n    char buf[4];\n    i;\n}\n"
    [f] = find_functions(text, ".c")
    assert actions(f.flow) == []
