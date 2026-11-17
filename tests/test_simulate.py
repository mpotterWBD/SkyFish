"""simulate.py: one run of a function with the card values, as a Trace."""

from skyfish.analysis.functions import find_functions
from skyfish.analysis.logic import END, actions
from skyfish.analysis.simulate import NOT_TAKEN, RAN, SKIPPED, TAKEN, UNKNOWN, simulate


def function_named(text, name):
    [f] = [f for f in find_functions(text, ".c") if f.name == name and f.has_body]
    return f


REPLY = """
static int reply(const char *key, uint8_t slot) {
    char tx[16];
    if (key == NULL) {
        return -1;
    }
    int used = snprintf(tx, 16, "T=%d\\r", slot);
    if (used > 15) {
        return -2;
    }
    sendPacket(tx, used);
    return used;
}
"""


def spans(function):
    """REPLY's flow: [IfChain(key==NULL), Action(snprintf), IfChain(used>15),
    Action(sendPacket), Exit(used)] - the bare declaration makes no node."""
    return function.flow


def test_the_good_path_runs_start_to_finish():
    f = function_named(REPLY, "reply")
    trace = simulate(f, {"key": "TEMP", "slot": 125})

    null_guard = f.flow[0].branches[0]           # if (key == NULL)
    assert trace.steps[null_guard.span].status == NOT_TAKEN
    assert trace.steps[null_guard.body[0].span].status == SKIPPED  # return -1

    fill = actions(f.flow)[0]                    # int used = snprintf(...)
    step = trace.steps[fill.span]
    assert step.status == RAN
    assert (step.returned, step.known) == (6, True)
    assert step.changes == {"used": 6, "tx": "T=125\r"}

    length_guard = f.flow[2].branches[0]         # if (used > 15)
    assert trace.steps[length_guard.span].status == NOT_TAKEN

    send = actions(f.flow)[1]                    # sendPacket(tx, used) - no contract
    step = trace.steps[send.span]
    assert step.status == UNKNOWN
    assert step.unknown_call
    assert step.unresolved == ("sendPacket(tx, used)",)

    last = f.flow[-1]                            # return used
    assert trace.steps[last.span].status == TAKEN
    assert (trace.steps[last.span].returned, trace.steps[last.span].known) == (6, True)
    assert trace.output == "used"
    assert trace.state["used"] == 6
    assert "tx" not in trace.state               # sendPacket may have rewritten it


def test_an_early_return_skips_the_rest():
    f = function_named(REPLY, "reply")
    trace = simulate(f, {"key": None, "slot": 125})
    null_guard = f.flow[0].branches[0]
    assert trace.steps[null_guard.span].status == TAKEN
    exit_step = trace.steps[null_guard.body[0].span]
    assert exit_step.status == TAKEN
    assert (exit_step.returned, exit_step.known) == (-1, True)
    assert trace.output == "-1"
    for action in actions(f.flow):
        assert trace.steps[action.span].status == SKIPPED
    assert trace.steps[f.flow[-1].span].status == SKIPPED


def test_an_undecidable_if_leaves_both_ways_possible():
    f = function_named(REPLY, "reply")
    trace = simulate(f, {"key": "TEMP"})         # no value for slot

    fill = actions(f.flow)[0]
    assert trace.steps[fill.span].status == UNKNOWN
    assert trace.steps[fill.span].unresolved == ("slot",)

    length_guard = f.flow[2].branches[0]         # if (used > 15): used is poisoned
    assert trace.steps[length_guard.span].status == UNKNOWN
    assert trace.steps[length_guard.span].unresolved == ("used",)
    assert trace.steps[length_guard.body[0].span].status == UNKNOWN  # return -2: a maybe

    assert trace.steps[f.flow[-1].span].status == UNKNOWN  # only maybe reached
    assert trace.output is None
    assert "used" not in trace.state


def test_locals_pick_up_their_plain_initializers():
    text = """
int total(int n) {
    int base = 10;
    int scaled = base * 2;
    if (n > scaled) {
        return 1;
    }
    return 0;
}
"""
    f = function_named(text, "total")
    trace = simulate(f, {"n": 25})
    assert trace.state["base"] == 10
    assert trace.state["scaled"] == 20
    assert trace.steps[f.flow[0].branches[0].span].status == TAKEN
    assert trace.output == "1"


def test_defines_sit_under_the_inputs():
    text = """
#define TX_MAX 64
int fits(int length) {
    if (length > TX_MAX) {
        return 0;
    }
    return 1;
}
"""
    f = function_named(text, "fits")
    from skyfish.analysis.defines import defines
    found = defines(text)
    assert simulate(f, {"length": 100}, found).output == "0"
    assert simulate(f, {"length": 10}, found).output == "1"
    # the operator's card value for TX_MAX wins over the file's
    assert simulate(f, {"length": 100, "TX_MAX": 200}, found).output == "1"


def test_loop_bodies_are_honest_not_run_once():
    text = """
int sum(int n) {
    int total = 0;
    while (n > 0) {
        total += n;
        n--;
    }
    return total;
}
"""
    f = function_named(text, "sum")
    trace = simulate(f, {"n": 3})
    add = actions(f.flow)[0]                     # total += n, in the loop
    assert trace.steps[add.span].status == UNKNOWN
    last = f.flow[-1]                            # return total
    assert trace.steps[last.span].status == TAKEN  # it does return...
    assert not trace.steps[last.span].known        # ...but the value can't be told
    assert trace.steps[last.span].unresolved == ("total",)
    assert trace.output == "total"               # the output row is still certain
    assert "total" not in trace.state


def test_a_return_in_a_loop_makes_the_output_uncertain():
    text = """
int f(int n) {
    while (n--) {
        return 9;
    }
    return 0;
}
"""
    f = function_named(text, "f")
    trace = simulate(f, {"n": 1})
    maybe, final = f.flow[0], f.flow[1]
    assert trace.steps[maybe.span].status == UNKNOWN   # ran 0..n times
    # return 0 runs only if the loop DIDN'T return - also a maybe.
    assert trace.steps[final.span].status == UNKNOWN
    assert trace.output is None                        # could have been 9 or 0


def test_falling_off_the_end_is_the_end_output():
    text = "void f(int n) {\n    if (n) {\n        log(n);\n    }\n}\n"
    f = function_named(text, "f")
    trace = simulate(f, {"n": 0})
    assert trace.output == END
    assert trace.steps[f.end_span].status == TAKEN


def test_a_forever_loop_never_reaches_the_end():
    text = """
int wait(void) {
    while (1) {
        return 1;
    }
}
"""
    f = function_named(text, "wait")
    trace = simulate(f, {})
    assert trace.output is None  # its only way out is a maybe-return


def test_unknown_calls_poison_their_pointer_arguments_only():
    text = """
int f(const char *name, int n) {
    HAL_Mangle(name, n);
    if (n == 2) {
        return 1;
    }
    return 0;
}
"""
    f = function_named(text, "f")
    trace = simulate(f, {"name": "GET", "n": 2})
    assert trace.steps[actions(f.flow)[0].span].unknown_call
    assert "name" not in trace.state       # a char * the callee could write through
    assert trace.state["n"] == 2           # passed by value - safe
    assert trace.output == "1"             # the walk carries on where it's sure


ADD_KEY_VALUE_PAIR = """
bool addKeyValuePair(char *dataCSV, uint16_t size, const char *KEY, int VALUE) {
    char number[12];
    size_t used;
    size_t pairLength;

    if(dataCSV == NULL || KEY == NULL || size == 0) {
        return false;
    }

    snprintf(number, sizeof(number), "%d", VALUE);

    used = strlen(dataCSV);
    pairLength = strlen(KEY) + 1 + strlen(number) + 1;

    if(used + pairLength + 1 > size) {
        return false;
    }

    snprintf(dataCSV + used, size - used, "%s:%s,", KEY, number);
    return true;
}
"""


def test_b1_addkeyvaluepair_runs_end_to_end():
    # The Backlog's done-when: the real stm32PacketDriver.c function, every
    # step resolved - sizeof(number) comes from char number[12].
    f = function_named(ADD_KEY_VALUE_PAIR, "addKeyValuePair")
    trace = simulate(f, {"dataCSV": "", "size": 64, "KEY": "TEMP", "VALUE": -42})
    assert trace.output == "true"
    statuses = {step.status for step in trace.steps.values()}
    assert UNKNOWN not in statuses          # every step resolved
    fill = actions(f.flow)[0]               # snprintf(number, sizeof(number), "%d", VALUE)
    assert trace.steps[fill.span].changes == {"number": "-42"}
    assert trace.state["used"] == 0
    assert trace.state["pairLength"] == 9   # "TEMP" + ':' + "-42" + ','
    last = actions(f.flow)[-1]              # snprintf(dataCSV + used, size - used, ...)
    assert trace.steps[last.span].changes["dataCSV + used"] == "TEMP:-42,"


def test_b1_addkeyvaluepair_too_small_returns_false():
    f = function_named(ADD_KEY_VALUE_PAIR, "addKeyValuePair")
    trace = simulate(f, {"dataCSV": "", "size": 8, "KEY": "TEMP", "VALUE": -42})
    assert trace.output == "false"          # 0 + 9 + 1 > 8
    guard = f.flow[4].branches[0]           # if (used + pairLength + 1 > size)
    assert trace.steps[guard.span].status == TAKEN


def test_an_array_parameter_is_a_pointer_to_sizeof():
    text = """
int f(uint8_t buf[], int n) {
    if (sizeof(buf) == 4) {
        return 1;
    }
    return 0;
}
"""
    f = function_named(text, "f")
    assert simulate(f, {"n": 0}).output == "1"
