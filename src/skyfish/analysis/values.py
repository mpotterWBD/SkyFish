"""What value a parameter can hold, and checking what someone types for it.

Used by the value cards (drag a card's blue input dot out): each knows its
parameter's type from value_type_for(), and parse_value() turns the typed
text into a value or says what's wrong ("Too big for uint16_t (0 to 65,535)").

C sizes are for the target the firmware runs on - 32-bit ARM (STM32) - so
int, long and size_t are 32 bits, and plain char is unsigned (ARM's default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INTEGER = "integer"
REAL = "real"
BOOLEAN = "boolean"
TEXT = "text"        # char * / char[N] - a C string
BYTES = "bytes"      # uint8_t * / uint8_t[N] - raw bytes
OTHER = "other"      # a struct, handle or function pointer: no value entry yet

# C integer types on 32-bit ARM: (bits, signed).
_C_INTEGERS = {
    "uint8_t": (8, False), "int8_t": (8, True), "uint16_t": (16, False), "int16_t": (16, True),
    "uint32_t": (32, False), "int32_t": (32, True), "uint64_t": (64, False), "int64_t": (64, True),
    "size_t": (32, False), "ssize_t": (32, True), "uintptr_t": (32, False), "intptr_t": (32, True),
    "char": (8, False), "signed char": (8, True), "unsigned char": (8, False),
    "short": (16, True), "short int": (16, True), "signed short": (16, True),
    "unsigned short": (16, False), "unsigned short int": (16, False),
    "int": (32, True), "signed": (32, True), "signed int": (32, True),
    "unsigned": (32, False), "unsigned int": (32, False),
    "long": (32, True), "long int": (32, True), "signed long": (32, True),
    "unsigned long": (32, False), "unsigned long int": (32, False),
    "long long": (64, True), "long long int": (64, True),
    "unsigned long long": (64, False), "unsigned long long int": (64, False),
}
_C_REALS = {"float", "double", "long double"}
_C_BOOLS = {"bool", "_Bool"}
_BYTE_TYPES = {"uint8_t", "unsigned char", "int8_t", "signed char"}


@dataclass(frozen=True)
class ValueType:
    kind: str                 # INTEGER, REAL, BOOLEAN, TEXT, BYTES or OTHER
    type_name: str            # as written, e.g. "const char *"
    bits: int = 0             # INTEGER
    signed: bool = False      # INTEGER
    pointer: bool = False     # can be NULL
    max_length: int | None = None  # TEXT: characters (not counting the 0x00); BYTES: bytes

    @property
    def minimum(self) -> int:
        return -(1 << (self.bits - 1)) if self.signed else 0

    @property
    def maximum(self) -> int:
        return (1 << (self.bits - 1)) - 1 if self.signed else (1 << self.bits) - 1

    def hint(self) -> str:
        """What to type, for the value card's empty field."""
        if self.kind == INTEGER:
            if not self.bits:
                return "a whole number"
            return f"{self.minimum:,} to {self.maximum:,}  (or 0x.. / 0b..)"
        if self.kind == REAL:
            return "a number, e.g. 3.3"
        if self.kind == BOOLEAN:
            return "true or false"
        if self.kind == TEXT:
            limit = f"up to {self.max_length} characters; " if self.max_length is not None else ""
            return f"text - {limit}\\r \\n \\t \\0 \\xNN work"
        if self.kind == BYTES:
            limit = f"up to {self.max_length} " if self.max_length is not None else ""
            return f"{limit}bytes, e.g. DB 01 FF"
        return "no value entry for this type yet"


def value_type_for(type_name: str, language: str | None) -> ValueType:
    """The ValueType for a parameter's type as SkyFish read it ("const char *",
    "uint8_t[12]", "int", or a Python annotation such as "str")."""
    if language == "python":
        return _python_type(type_name)
    text = re.sub(r"\s+", " ", type_name).strip()
    array = re.search(r"\[\s*(\w*)\s*\]$", text)
    size = int(array.group(1), 0) if array and array.group(1).isdigit() else None
    base = re.sub(r"\[.*\]$", "", text)
    pointer = base.endswith("*") or array is not None
    base = re.sub(r"\b(const|volatile|static|register|struct|enum)\b", "", base.rstrip("* ")).strip()
    base = re.sub(r"\s+", " ", base)
    if "(" in text:  # function pointer
        return ValueType(OTHER, type_name, pointer=True)
    if pointer:
        stars = text.count("*") + (1 if array else 0)
        if stars == 1 and base == "char":
            return ValueType(TEXT, type_name, pointer=True,
                             max_length=size - 1 if size is not None else None)
        if stars == 1 and base in _BYTE_TYPES:
            return ValueType(BYTES, type_name, pointer=True, max_length=size)
        return ValueType(OTHER, type_name, pointer=True)
    if base in _C_INTEGERS:
        bits, signed = _C_INTEGERS[base]
        return ValueType(INTEGER, type_name, bits=bits, signed=signed)
    if base in _C_REALS:
        return ValueType(REAL, type_name)
    if base in _C_BOOLS:
        return ValueType(BOOLEAN, type_name)
    return ValueType(OTHER, type_name)


def _python_type(annotation: str) -> ValueType:
    name = annotation.strip()
    if name == "int":
        return ValueType(INTEGER, name)  # Python ints have no size limit
    if name == "float":
        return ValueType(REAL, name)
    if name == "bool":
        return ValueType(BOOLEAN, name)
    if name in ("str", ""):
        return ValueType(TEXT, name or "any")
    if name == "bytes":
        return ValueType(BYTES, name)
    if name.endswith("| None") or name.startswith("Optional["):
        inner = re.sub(r"^Optional\[(.*)\]$|^(.*)\s*\|\s*None$", lambda m: m.group(1) or m.group(2), name)
        return _with_null(_python_type(inner.strip()))
    return ValueType(OTHER, name)


def _with_null(value_type: ValueType) -> ValueType:
    return ValueType(value_type.kind, value_type.type_name, value_type.bits, value_type.signed,
                     True, value_type.max_length)


class InvalidValue(Exception):
    """What's wrong with a typed value, worded for the person typing it."""


def parse_value(value_type: ValueType, text: str):
    """The value text stands for, or raises InvalidValue saying why not.
    INTEGER -> int, REAL -> float, BOOLEAN -> bool, TEXT -> str (escapes
    decoded), BYTES -> bytes."""
    kind = value_type.kind
    stripped = text.strip()
    if kind == OTHER:
        raise InvalidValue(f"No value entry for {value_type.type_name} yet")
    if kind == TEXT:
        value = _decode_escapes(text)
        if value_type.max_length is not None and len(value) > value_type.max_length:
            raise InvalidValue(f"Too long for {value_type.type_name}: {len(value)} characters, "
                              f"it holds {value_type.max_length} + the 0x00")
        return value
    if not stripped:
        raise InvalidValue("Type a value")
    if kind == INTEGER:
        number = _parse_integer(stripped)
        if value_type.bits and not value_type.minimum <= number <= value_type.maximum:
            word = "big" if number > value_type.maximum else "small"
            raise InvalidValue(f"Too {word} for {value_type.type_name} "
                              f"({value_type.minimum:,} to {value_type.maximum:,})")
        return number
    if kind == REAL:
        try:
            return float(stripped.rstrip("fF"))
        except ValueError:
            raise InvalidValue(f"Not a number: {stripped}") from None
    if kind == BOOLEAN:
        lowered = stripped.lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise InvalidValue("Type true or false")
    if kind == BYTES:
        data = bytes(_parse_byte(part) for part in re.split(r"[\s,]+", stripped) if part)
        if value_type.max_length is not None and len(data) > value_type.max_length:
            raise InvalidValue(f"Too long for {value_type.type_name}: {len(data)} bytes, "
                              f"it holds {value_type.max_length}")
        return data
    raise InvalidValue(f"No value entry for {value_type.type_name} yet")


def _parse_integer(text: str) -> int:
    cleaned = re.sub(r"[uUlL]+$", "", text.replace("_", "").replace(",", ""))
    char = re.fullmatch(r"'(.)'", cleaned)
    if char:
        return ord(char.group(1))
    try:
        return int(cleaned, 0)
    except ValueError:
        raise InvalidValue(f"Not a whole number: {text}") from None


def _parse_byte(part: str) -> int:
    try:
        # Bytes are hex by default ("DB"), with or without 0x.
        value = int(part, 0) if part.lower().startswith(("0x", "0b")) else int(part, 16)
    except ValueError:
        raise InvalidValue(f"Not a byte: {part}") from None
    if not 0 <= value <= 0xFF:
        raise InvalidValue(f"Too big for a byte: {part} (00 to FF)")
    return value


_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\", '"': '"', "'": "'"}


def _decode_escapes(text: str) -> str:
    def replace(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        return _ESCAPES.get(m.group(2), m.group(0))
    return re.sub(r"\\x([0-9A-Fa-f]{2})|\\(.)", replace, text)


def describe(value_type: ValueType, value) -> str:
    """A value written the way it would be in code, for showing on a card:
    "TEMP" in quotes, 0x1F-style bytes, true/false, numbers as typed."""
    if value is None:
        return "NULL" if value_type.pointer else "None"
    if value_type.kind == TEXT:
        shown = value.encode("unicode_escape").decode("ascii").replace('"', '\\"')
        return f'"{shown}"'
    if value_type.kind == BYTES:
        return "{" + ", ".join(f"0x{b:02X}" for b in value) + "}"
    if value_type.kind == BOOLEAN:
        return "true" if value else "false"
    return str(value)
