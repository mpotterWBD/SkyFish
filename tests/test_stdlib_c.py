"""stdlib_c.py: what a standard C call returns and changes, by contract."""

from skyfish.analysis.stdlib_c import UNKNOWN, CallEffect, call_effect, knows


def test_an_unknown_function_is_nobodys_contract():
    assert call_effect("HAL_UART_Transmit", [1, 2, 3]) is None
    assert not knows("calcCrc")
    assert knows("strlen")


def test_strlen_stops_at_the_terminator():
    assert call_effect("strlen", ["TEMP"]) == CallEffect(4)
    assert call_effect("strlen", ["ab\0cd"]) == CallEffect(2)
    assert call_effect("strlen", [""]) == CallEffect(0)


def test_strlen_of_null_or_unknown_cant_be_told():
    assert call_effect("strlen", [None]) == CallEffect(UNKNOWN)  # would crash, not return
    assert call_effect("strlen", [UNKNOWN]) == CallEffect(UNKNOWN)


def test_strcmp_gives_a_sign_not_a_distance():
    assert call_effect("strcmp", ["TEMP", "TEMP"]) == CallEffect(0)
    assert call_effect("strcmp", ["a", "b"]) == CallEffect(-1)
    assert call_effect("strcmp", ["b", "a"]) == CallEffect(1)
    assert call_effect("strncmp", ["TEMPx", "TEMPy", 4]) == CallEffect(0)
    assert call_effect("strcasecmp", ["Temp", "tEMP"]) == CallEffect(0)


def test_strchr_returns_the_tail_or_null():
    assert call_effect("strchr", ["a:b", ord(":")]) == CallEffect(":b")
    assert call_effect("strchr", ["abc", ord(":")]) == CallEffect(None)
    assert call_effect("strrchr", ["a:b:c", ord(":")]) == CallEffect(":c")
    assert call_effect("strstr", ["T=12\r", "="]) == CallEffect("=12\r")


def test_memcmp_needs_the_whole_window():
    assert call_effect("memcmp", [b"\xdb\x01", b"\xdb\x01", 2]) == CallEffect(0)
    assert call_effect("memcmp", [b"\xdb", b"\xdb\x01", 2]) == CallEffect(UNKNOWN)


def test_copies_report_what_the_buffer_holds_afterwards():
    assert call_effect("strcpy", [UNKNOWN, "GET"]) == CallEffect("GET", ((0, "GET"),))
    assert call_effect("strncpy", [UNKNOWN, "ABCDEF", 3]) == CallEffect("ABC", ((0, "ABC"),))
    assert call_effect("strcat", ["T=", "1"]) == CallEffect("T=1", ((0, "T=1"),))
    assert call_effect("memset", [UNKNOWN, 0, 4]) == CallEffect(b"\0\0\0\0", ((0, b"\0\0\0\0"),))
    assert call_effect("memcpy", [UNKNOWN, b"\xdb\x01\x02", 2]) \
        == CallEffect(b"\xdb\x01", ((0, b"\xdb\x01"),))


def test_snprintf_counts_fully_but_writes_within_size():
    # Returns what it WOULD write; the buffer gets size-1 characters + NUL.
    effect = call_effect("snprintf", [UNKNOWN, 32, "T=%d\r", 125])
    assert effect == CallEffect(6, ((0, "T=125\r"),))
    effect = call_effect("snprintf", [UNKNOWN, 4, "T=%d\r", 125])
    assert effect == CallEffect(6, ((0, "T=1"),))


def test_printf_formatting_subset():
    assert call_effect("sprintf", [UNKNOWN, "%02X", 0xB]).changes == ((0, "0B"),)
    assert call_effect("sprintf", [UNKNOWN, "%5.1f", 3.26]).changes == ((0, "  3.3"),)
    assert call_effect("sprintf", [UNKNOWN, "%c%s", 65, "BC"]).changes == ((0, "ABC"),)
    assert call_effect("sprintf", [UNKNOWN, "%*d", 4, 7]).changes == ((0, "   7"),)
    assert call_effect("sprintf", [UNKNOWN, "100%% done"]).changes == ((0, "100% done"),)
    # %u of a negative would need a wrap width - refuse rather than guess.
    assert call_effect("sprintf", [UNKNOWN, "%u", -1]) == CallEffect(UNKNOWN)


def test_ctype_and_conversions():
    assert call_effect("isdigit", [ord("7")]) == CallEffect(1)
    assert call_effect("isdigit", [ord("x")]) == CallEffect(0)
    assert call_effect("isxdigit", [ord("F")]) == CallEffect(1)
    assert call_effect("toupper", [ord("a")]) == CallEffect(ord("A"))
    assert call_effect("atoi", ["  -42xyz"]) == CallEffect(-42)
    assert call_effect("atoi", ["xyz"]) == CallEffect(0)
    assert call_effect("abs", [-7]) == CallEffect(7)


def test_math_contracts():
    assert call_effect("sqrt", [9]) == CallEffect(3.0)
    assert call_effect("sqrt", [-1]) == CallEffect(UNKNOWN)
    assert call_effect("floor", [3.7]) == CallEffect(3.0)
    assert call_effect("fmax", [2, 5.5]) == CallEffect(5.5)
    assert call_effect("pow", [2, 10]) == CallEffect(1024.0)
