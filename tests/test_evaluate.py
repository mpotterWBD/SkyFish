"""evaluate.py: a C condition plus input values -> true / false / unknown."""

import pytest

from skyfish.analysis.evaluate import Evaluation, evaluate_condition


def test_true_and_false_from_a_comparison():
    assert evaluate_condition("len > 4", {"len": 10}) == Evaluation(True)
    assert evaluate_condition("len > 4", {"len": 4}) == Evaluation(False)


def test_missing_name_is_unknown_and_reported():
    assert evaluate_condition("used < limit", {"limit": 8}) == Evaluation(None, ("used",))


def test_unresolved_names_in_order_met_without_repeats():
    result = evaluate_condition("a + b > a", {})
    assert result == Evaluation(None, ("a", "b"))


def test_or_short_circuit_hides_the_irrelevant_unknown():
    # ok is true, so the whole || is true no matter what "used" holds.
    assert evaluate_condition("ok || used > 2", {"ok": 1}) == Evaluation(True)
    # ok is false: now "used" decides, and it has no value.
    assert evaluate_condition("ok || used > 2", {"ok": 0}) == Evaluation(None, ("used",))


def test_and_short_circuit_both_directions():
    assert evaluate_condition("flag && count", {"flag": 0}) == Evaluation(False)
    assert evaluate_condition("count && flag", {"flag": 0}) == Evaluation(False)
    assert evaluate_condition("flag && count", {"flag": 1}) == Evaluation(None, ("count",))


def test_not_flips_and_passes_unknown_through():
    assert evaluate_condition("!ready", {"ready": 0}) == Evaluation(True)
    assert evaluate_condition("!ready", {"ready": 5}) == Evaluation(False)
    assert evaluate_condition("!ready", {}) == Evaluation(None, ("ready",))


def test_null_checks_on_pointers():
    # None is a value card's NULL tickbox; a str is a real (non-NULL) pointer.
    assert evaluate_condition("key == NULL", {"key": None}) == Evaluation(True)
    assert evaluate_condition("key == NULL", {"key": "TEMP"}) == Evaluation(False)
    assert evaluate_condition("key != NULL", {"key": "TEMP"}) == Evaluation(True)
    assert evaluate_condition("!key", {"key": None}) == Evaluation(True)
    assert evaluate_condition("key", {"key": ""}) == Evaluation(True)  # empty, but not NULL


def test_comparing_two_strings_is_pointer_comparison_so_unknown():
    result = evaluate_condition('key == "TEMP"', {"key": "TEMP"})
    assert result == Evaluation(None, ('key == "TEMP"',))


def test_compute_expression_gives_the_value_itself():
    from skyfish.analysis.evaluate import Computation, compute_expression
    assert compute_expression("(len + 1) * 2", {"len": 3}) == Computation(True, 8)
    assert compute_expression("strlen(s)", {"s": "TEMP"}) == Computation(True, 4)
    assert compute_expression("limit - used", {"limit": 8}) \
        == Computation(False, unresolved=("used",))


def test_integer_arithmetic_is_c_not_python():
    # C truncates division toward zero; the remainder keeps the dividend's sign.
    assert evaluate_condition("-7 / 2 == -3", {}) == Evaluation(True)
    assert evaluate_condition("-7 % 2 == -1", {}) == Evaluation(True)
    assert evaluate_condition("x / 0 == 1", {"x": 4}) == Evaluation(None, ("x / 0",))


def test_number_literal_spellings():
    assert evaluate_condition("x == 0x1F", {"x": 31}) == Evaluation(True)
    assert evaluate_condition("x == 0b101", {"x": 5}) == Evaluation(True)
    assert evaluate_condition("x == 010", {"x": 8}) == Evaluation(True)  # C octal
    assert evaluate_condition("x == 100U", {"x": 100}) == Evaluation(True)
    assert evaluate_condition("v > 3.0f", {"v": 3.3}) == Evaluation(True)


def test_char_literal_and_bit_operations():
    assert evaluate_condition("c == 'A'", {"c": 65}) == Evaluation(True)
    assert evaluate_condition("c == '\\r'", {"c": 13}) == Evaluation(True)
    assert evaluate_condition("(flags & 0x04) != 0", {"flags": 0x06}) == Evaluation(True)
    assert evaluate_condition("1 << n == 8", {"n": 3}) == Evaluation(True)


def test_cast_wraps_to_the_c_type():
    # values.py's 32-bit ARM sizes: uint8_t holds 0..255, int8_t -128..127.
    assert evaluate_condition("(uint8_t)x == 44", {"x": 300}) == Evaluation(True)
    assert evaluate_condition("(int8_t)x == -128", {"x": 128}) == Evaluation(True)
    assert evaluate_condition("(bool)p", {"p": None}) == Evaluation(False)


def test_ternary_follows_the_known_condition():
    values = {"ok": 1, "a": 2, "b": 3}
    assert evaluate_condition("(ok ? a : b) == 2", values) == Evaluation(True)
    assert evaluate_condition("(ok ? a : b) == 2", {"a": 2, "b": 3}) == Evaluation(None, ("ok",))
    # Both arms agree, so the unknown condition doesn't matter.
    assert evaluate_condition("(ok ? 5 : 5) == 5", {}) == Evaluation(True)


def test_stdlib_calls_resolve_by_contract():
    # strcmp has a contract now (stdlib_c.py) - the old pointer excuse is gone.
    assert evaluate_condition('strcmp(key, "TEMP") == 0', {"key": "TEMP"}) == Evaluation(True)
    assert evaluate_condition('strcmp(key, "TEMP") == 0', {"key": "HUM"}) == Evaluation(False)
    assert evaluate_condition("strlen(name) > used", {"name": "ab", "used": 1}) == Evaluation(True)
    assert evaluate_condition("strchr(line, ':') != NULL", {"line": "T:1"}) == Evaluation(True)
    assert evaluate_condition("isdigit(c) && c != '0'", {"c": ord("7")}) == Evaluation(True)


def test_a_stdlib_call_blames_its_unknown_argument():
    assert evaluate_condition('strcmp(key, "TEMP") == 0', {}) == Evaluation(None, ("key",))


def test_calls_fields_and_elements_are_unknown_as_written():
    # calcCrc isn't standard C - no contract, so the call itself is the blocker.
    assert evaluate_condition("calcCrc(buf, len) == 0", {"buf": b"\x01", "len": 1}) \
        == Evaluation(None, ("calcCrc(buf, len)",))
    assert evaluate_condition("s->len > 0", {}) == Evaluation(None, ("s->len",))
    assert evaluate_condition("buf[0] == 0xDB", {"buf": b"\xdb"}) == Evaluation(None, ("buf[0]",))


def test_true_false_and_macros():
    assert evaluate_condition("ready == true", {"ready": True}) == Evaluation(True)
    assert evaluate_condition("ready == TRUE", {"ready": 1}) == Evaluation(True)
    assert evaluate_condition("ready == FALSE", {"ready": 0}) == Evaluation(True)


def test_input_values_win_over_the_builtin_macros():
    assert evaluate_condition("x == TRUE", {"x": 7, "TRUE": 7}) == Evaluation(True)


def test_else_rows_and_bad_text_come_back_quietly_unknown():
    assert evaluate_condition("", {}) == Evaluation(None)
    assert evaluate_condition("if while (", {}) == Evaluation(None)


def test_sizeof_of_declared_variables_and_types():
    # B1: sizeof(name) from the declared type, sizeof(type) from the ARM sizes.
    types = {"number": "char[12]", "crc": "uint16_t", "name": "const char *",
             "buf": "uint8_t[TX_MAX]", "cb": "void (*)(int)", "pt": "struct Point"}
    assert evaluate_condition("sizeof(number) == 12", {}, types) == Evaluation(True)
    assert evaluate_condition("sizeof(crc) == 2", {}, types) == Evaluation(True)
    assert evaluate_condition("sizeof(name) == 4", {}, types) == Evaluation(True)  # a pointer
    assert evaluate_condition("sizeof(cb) == 4", {}, types) == Evaluation(True)    # likewise
    assert evaluate_condition("sizeof number == 12", {}, types) == Evaluation(True)
    # an array sized by a #define resolves when the define's value is in
    assert evaluate_condition("sizeof(buf) == 8", {"TX_MAX": 8}, types) == Evaluation(True)
    assert evaluate_condition("sizeof(buf) == 8", {}, types) == Evaluation(None, ("sizeof(buf)",))
    # types it won't guess at stay unknown, as written
    assert evaluate_condition("sizeof(pt) > 0", {}, types) == Evaluation(None, ("sizeof(pt)",))


def test_sizeof_of_type_names():
    assert evaluate_condition("sizeof(uint16_t) == 2", {}) == Evaluation(True)
    assert evaluate_condition("sizeof(int) == 4", {}) == Evaluation(True)
    assert evaluate_condition("sizeof(char[12]) == 12", {}) == Evaluation(True)
    assert evaluate_condition("sizeof(double) == 8", {}) == Evaluation(True)
    assert evaluate_condition("sizeof(uint32_t *) == 4", {}) == Evaluation(True)


def test_sizeof_of_expressions_and_unknown_names_stays_unknown():
    assert evaluate_condition("sizeof(buf[0]) == 1", {}) == Evaluation(None, ("sizeof(buf[0])",))
    assert evaluate_condition("sizeof(s->len) == 2", {}) == Evaluation(None, ("sizeof(s->len)",))
    # no types mapping passed - today's behaviour, unknown as written
    assert evaluate_condition("sizeof(number) == 12", {}) \
        == Evaluation(None, ("sizeof(number)",))


@pytest.mark.parametrize("condition,values,expected", [
    # The kind of guards Mike's firmware actually has.
    ("length < 2 || length > TX_MAX", {"length": 1, "TX_MAX": 64}, True),
    ("length < 2 || length > TX_MAX", {"length": 10, "TX_MAX": 64}, False),
    ("state == 2 && remaining == 0", {"state": 2, "remaining": 0}, True),
    ("(crc & 0xFF) == expected", {"crc": 0x12AB, "expected": 0xAB}, True),
])
def test_firmware_style_guards(condition, values, expected):
    assert evaluate_condition(condition, values) == Evaluation(expected)
