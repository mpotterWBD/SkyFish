"""What a standard C function returns and changes, by its contract alone.

The cards draw a call as a black box: inputs on the left, what it returns and
writes on the right. For functions the C standard pins down (strlen, strcmp,
snprintf...), the box can be filled in from the arguments' values without
stepping inside the function - that's call_effect(). Anything not in here
(HAL calls, the project's own functions) gets None back, and the card shows
"?".

Values are the ones value cards hold (see values.py): int, float, bool, str
for char *, bytes for uint8_t *, None for NULL. An argument that isn't known
is passed as UNKNOWN; each contract decides which arguments it really needs
(snprintf never needs the buffer's old contents). C-string rules apply: a
str's contents stop at the first '\0', NULL where a string is needed makes
the result UNKNOWN (that call would crash, not return), and %u, %x and
right-shifts refuse negatives rather than guess at a width to wrap with.

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence


class _UnknownValue:
    def __repr__(self) -> str:  # pragma: no cover - only for debugging
        return "UNKNOWN"


# A known function's result when the arguments it needs aren't known (or the
# call would be undefined - strlen(NULL), sqrt of a negative).
UNKNOWN = _UnknownValue()


@dataclass(frozen=True)
class CallEffect:
    """What one call does: its return value (UNKNOWN when it can't be told
    from the arguments given) and what it writes - (argument index, the new
    contents that argument points at afterwards). When value is UNKNOWN and
    one argument is what stopped it (missing, or a value the call would die
    on), blocked is that argument's index - snprintf blames the missing %d
    value, never the buffer."""
    value: object
    changes: tuple[tuple[int, object], ...] = ()
    blocked: int | None = None


def call_effect(name: str, args: Sequence[object]) -> CallEffect | None:
    """The effect of name(*args) by contract, or None for a function this
    module doesn't know (the card's "?" box)."""
    contract = _CONTRACTS.get(name)
    if contract is None:
        return None
    try:
        return contract(list(args))
    except _CantTell as blocked:
        return CallEffect(UNKNOWN, blocked=blocked.index)


def knows(name: str) -> bool:
    """Whether name has a contract here - a box that can be filled in."""
    return name in _CONTRACTS


class _CantTell(Exception):
    """An argument a contract needs is UNKNOWN, missing or the wrong shape.
    index says which argument, when it's one argument's fault."""

    def __init__(self, index: int | None = None) -> None:
        super().__init__(index)
        self.index = index


# --- argument readers ------------------------------------------------------------

def _arg(args: list, index: int):
    if index >= len(args) or isinstance(args[index], _UnknownValue):
        raise _CantTell(index)
    return args[index]


def _int(args: list, index: int) -> int:
    value = _arg(args, index)
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int):
        raise _CantTell(index)
    return value


def _real(args: list, index: int) -> float:
    value = _arg(args, index)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _CantTell(index)
    return float(value)


def _string(args: list, index: int) -> str:
    """A C string argument: its contents up to the first '\\0'. NULL raises -
    the real call wouldn't return."""
    value = _arg(args, index)
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if not isinstance(value, str):
        raise _CantTell(index)
    terminator = value.find("\0")
    return value if terminator < 0 else value[:terminator]


def _buffer(args: list, index: int) -> bytes:
    """A memory argument (memcmp, memcpy source): raw bytes, no '\\0' rule."""
    value = _arg(args, index)
    if isinstance(value, str):
        return value.encode("latin-1", errors="replace")
    if not isinstance(value, bytes):
        raise _CantTell(index)
    return value


def _char(args: list, index: int) -> str:
    """An int-passed character (strchr's c, toupper's argument)."""
    return chr(_int(args, index) & 0xFF)


def _sign(difference: int) -> int:
    return (difference > 0) - (difference < 0)


# --- string.h ---------------------------------------------------------------------

def _strlen(args):
    return CallEffect(len(_string(args, 0)))


def _strcmp(args):
    return CallEffect(_sign((_string(args, 0) > _string(args, 1))
                            - (_string(args, 0) < _string(args, 1))))


def _strncmp(args):
    n = _int(args, 2)
    a, b = _string(args, 0)[:n], _string(args, 1)[:n]
    return CallEffect(_sign((a > b) - (a < b)))


def _strcasecmp(args):
    a, b = _string(args, 0).lower(), _string(args, 1).lower()
    return CallEffect(_sign((a > b) - (a < b)))


def _strncasecmp(args):
    n = _int(args, 2)
    a, b = _string(args, 0)[:n].lower(), _string(args, 1)[:n].lower()
    return CallEffect(_sign((a > b) - (a < b)))


def _strchr(args, find=str.find):
    text, wanted = _string(args, 0), _char(args, 1)
    if wanted == "\0":
        return CallEffect("")  # the terminator: a real, empty-string pointer
    position = find(text, wanted)
    return CallEffect(None if position < 0 else text[position:])


def _strrchr(args):
    return _strchr(args, find=str.rfind)


def _strstr(args):
    haystack, needle = _string(args, 0), _string(args, 1)
    position = haystack.find(needle)
    return CallEffect(None if position < 0 else haystack[position:])


def _memcmp(args):
    n = _int(args, 2)
    a, b = _buffer(args, 0), _buffer(args, 1)
    if len(a) < n or len(b) < n:
        raise _CantTell  # the real arrays go on past what the values hold
    return CallEffect(_sign((a[:n] > b[:n]) - (a[:n] < b[:n])))


def _strcpy(args):
    copied = _string(args, 1)
    return CallEffect(copied, ((0, copied),))


def _strncpy(args):
    copied = _string(args, 1)[:_int(args, 2)]
    return CallEffect(copied, ((0, copied),))


def _strcat(args):
    joined = _string(args, 0) + _string(args, 1)
    return CallEffect(joined, ((0, joined),))


def _strncat(args):
    joined = _string(args, 0) + _string(args, 1)[:_int(args, 2)]
    return CallEffect(joined, ((0, joined),))


def _memset(args):
    contents = bytes([_int(args, 1) & 0xFF]) * _int(args, 2)
    return CallEffect(contents, ((0, contents),))


def _memcpy(args):
    n = _int(args, 2)
    source = _buffer(args, 1)
    if len(source) < n:
        raise _CantTell
    copied = source[:n]
    return CallEffect(copied, ((0, copied),))


# --- printf formatting ------------------------------------------------------------

_SPEC = re.compile(r"%([-+ #0]*)(\d+|\*)?(?:\.(\d+|\*))?[hlLzjt]*([diuxXocsfFeEgG%])")


def _format(fmt: str, args: list, start: int) -> str:
    """fmt with C conversions filled in from args[start:], or _CantTell."""
    pieces: list[str] = []
    position = 0
    index = start

    def take_int() -> int:
        nonlocal index
        value = _int(args, index)
        index += 1
        return value

    for match in _SPEC.finditer(fmt):
        pieces.append(fmt[position:match.start()])
        position = match.end()
        flags, width, precision, letter = match.groups()
        if letter == "%":
            pieces.append("%")
            continue
        width = str(take_int()) if width == "*" else (width or "")
        precision = str(take_int()) if precision == "*" else precision
        spec = "%" + flags + width + ("." + precision if precision is not None else "")
        if letter in "di":
            pieces.append((spec + "d") % take_int())
        elif letter == "u":
            value = take_int()
            if value < 0:
                raise _CantTell  # wrapping needs a width this module won't guess
            pieces.append((spec + "d") % value)
        elif letter in "xXo":
            value = take_int()
            if value < 0:
                raise _CantTell
            pieces.append((spec + letter) % value)
        elif letter == "c":
            pieces.append((spec + "s") % _char(args, index)); index += 1
        elif letter == "s":
            pieces.append((spec + "s") % _string(args, index)); index += 1
        else:  # f F e E g G
            value = _real(args, index); index += 1
            pieces.append((spec + letter) % value)
    if "%" in fmt[position:].replace("%%", ""):
        raise _CantTell  # a conversion the pattern didn't recognise
    pieces.append(fmt[position:])
    return "".join(pieces)


def _snprintf(args):
    size = _int(args, 1)
    formatted = _format(_string(args, 2), args, 3)
    written = formatted[:max(size - 1, 0)]
    return CallEffect(len(formatted), ((0, written),) if size > 0 else ())


def _sprintf(args):
    formatted = _format(_string(args, 1), args, 2)
    return CallEffect(len(formatted), ((0, formatted),))


# --- stdlib.h / ctype.h / math.h ---------------------------------------------------

def _atoi(args):
    match = re.match(r"[ \t\n\r\f\v]*([+-]?\d+)", _string(args, 0))
    return CallEffect(int(match.group(1)) if match else 0)


def _abs(args):
    return CallEffect(abs(_int(args, 0)))


def _fabs(args):
    return CallEffect(abs(_real(args, 0)))


def _ctype(test: Callable[[str], bool]):
    def contract(args):
        return CallEffect(int(test(_char(args, 0))))
    return contract


def _toupper(args):
    return CallEffect(ord(_char(args, 0).upper()))


def _tolower(args):
    return CallEffect(ord(_char(args, 0).lower()))


def _sqrt(args):
    value = _real(args, 0)
    if value < 0:
        raise _CantTell
    return CallEffect(math.sqrt(value))


def _math(operation: Callable[..., float], operands: int = 1):
    def contract(args):
        try:
            return CallEffect(float(operation(*(_real(args, i) for i in range(operands)))))
        except (ValueError, ZeroDivisionError):
            raise _CantTell from None
    return contract


_CONTRACTS: dict[str, Callable[[list], CallEffect]] = {
    "strlen": _strlen,
    "strcmp": _strcmp, "strncmp": _strncmp,
    "strcasecmp": _strcasecmp, "strncasecmp": _strncasecmp,
    "stricmp": _strcasecmp, "strnicmp": _strncasecmp,  # the common MSVC/firmware spellings
    "strchr": _strchr, "strrchr": _strrchr, "strstr": _strstr,
    "memcmp": _memcmp,
    "strcpy": _strcpy, "strncpy": _strncpy, "strcat": _strcat, "strncat": _strncat,
    "memset": _memset, "memcpy": _memcpy, "memmove": _memcpy,
    "snprintf": _snprintf, "sprintf": _sprintf,
    "atoi": _atoi, "atol": _atoi, "atoll": _atoi,
    "abs": _abs, "labs": _abs, "llabs": _abs,
    "fabs": _fabs, "fabsf": _fabs,
    "isdigit": _ctype(str.isdigit), "isalpha": _ctype(str.isalpha),
    "isalnum": _ctype(str.isalnum), "isspace": _ctype(lambda c: c in " \t\n\r\f\v"),
    "isupper": _ctype(str.isupper), "islower": _ctype(str.islower),
    "isxdigit": _ctype(lambda c: c in "0123456789abcdefABCDEF"),
    "toupper": _toupper, "tolower": _tolower,
    "sqrt": _sqrt, "sqrtf": _sqrt,
    "floor": _math(math.floor), "floorf": _math(math.floor),
    "ceil": _math(math.ceil), "ceilf": _math(math.ceil),
    "round": _math(round), "roundf": _math(round),
    "pow": _math(math.pow, 2), "powf": _math(math.pow, 2),
    "fmod": _math(math.fmod, 2), "fmodf": _math(math.fmod, 2),
    "fmin": _math(min, 2), "fminf": _math(min, 2),
    "fmax": _math(max, 2), "fmaxf": _math(max, 2),
}
