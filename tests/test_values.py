import pytest

from skyfish.analysis.values import (
    BOOLEAN, BYTES, INTEGER, OTHER, REAL, TEXT, InvalidValue, describe, parse_value, value_type_for,
)


def c(type_name):
    return value_type_for(type_name, "c")


def test_c_types():
    assert c("uint16_t").kind == INTEGER and (c("uint16_t").minimum, c("uint16_t").maximum) == (0, 65535)
    assert (c("int").minimum, c("int").maximum) == (-2**31, 2**31 - 1)  # 32-bit ARM
    assert c("size_t").bits == 32 and not c("size_t").signed
    assert c("const char *").kind == TEXT and c("const char *").pointer
    assert c("char[12]").kind == TEXT and c("char[12]").max_length == 11  # + the 0x00
    assert c("const uint8_t *").kind == BYTES
    assert c("uint8_t[2]").max_length == 2
    assert c("bool").kind == BOOLEAN and c("float").kind == REAL
    assert c("UART_HandleTypeDef *").kind == OTHER and c("UART_HandleTypeDef *").pointer
    assert c("void (*)(int)").kind == OTHER


def test_integers_in_range_and_formats():
    u16 = c("uint16_t")
    assert parse_value(u16, "65535") == 65535
    assert parse_value(u16, "0x1F") == 31 and parse_value(u16, "0b101") == 5
    assert parse_value(u16, "12U") == 12 and parse_value(u16, "1,000") == 1000
    assert parse_value(c("uint8_t"), "'A'") == 65
    with pytest.raises(InvalidValue, match=r"Too big for uint16_t \(0 to 65,535\)"):
        parse_value(u16, "65536")
    with pytest.raises(InvalidValue, match="Too small"):
        parse_value(u16, "-1")
    assert parse_value(c("int"), "-5") == -5
    with pytest.raises(InvalidValue, match="Not a whole number"):
        parse_value(u16, "12.5")


def test_text_escapes_and_length():
    assert parse_value(c("const char *"), r"KEY:1,\r\n") == "KEY:1,\r\n"
    assert parse_value(c("char *"), r"\x41\0") == "A\0"
    assert parse_value(c("char[4]"), "abc") == "abc"
    with pytest.raises(InvalidValue, match=r"holds 3 \+ the 0x00"):
        parse_value(c("char[4]"), "abcd")
    assert parse_value(c("const char *"), "") == ""  # an empty string is a real value


def test_bytes_bool_real():
    assert parse_value(c("const uint8_t *"), "DB 01, 0xFF") == b"\xdb\x01\xff"
    with pytest.raises(InvalidValue, match="Too big for a byte"):
        parse_value(c("uint8_t *"), "1FF")
    with pytest.raises(InvalidValue, match="holds 2"):
        parse_value(c("uint8_t[2]"), "01 02 03")
    assert parse_value(c("bool"), "TRUE") is True and parse_value(c("bool"), "0") is False
    assert parse_value(c("float"), "3.3f") == 3.3
    with pytest.raises(InvalidValue):
        parse_value(c("UART_HandleTypeDef *"), "x")


def test_python_types_and_describe():
    py = lambda t: value_type_for(t, "python")  # noqa: E731
    assert py("int").kind == INTEGER and parse_value(py("int"), str(2**80)) == 2**80  # no size limit
    assert py("str").kind == TEXT and py("Path").kind == OTHER
    assert py("int | None").pointer and py("Optional[str]").kind == TEXT
    assert describe(c("const char *"), "KEY\r\n") == r'"KEY\r\n"'
    assert describe(c("const char *"), None) == "NULL"
    assert describe(c("uint8_t *"), b"\xdb\x01") == "{0xDB, 0x01}"
    assert describe(c("bool"), True) == "true"
