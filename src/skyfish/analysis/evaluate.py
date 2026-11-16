"""Whether an if condition is true, false or unknown, given input values.

The value cards hold Python values for a function's inputs (see values.py:
int, float, bool, str for char *, bytes for uint8_t *, None for NULL). This
module takes a condition as a card row shows it ("len > used + 2") and those
values, and decides which way the branch goes - or says it can't, and lists
what stopped it (names with no value, like a local, or a call like
strcmp(a, b)) so a card can show why.

The condition is parsed with the same tree-sitter C grammar as c_parser.py,
not re-scanned by hand, and evaluated with C's rules: || and && short-circuit
(a side that decides the answer makes the other side's unknowns irrelevant),
integer division truncates toward zero, a pointer is true when it isn't NULL,
and a cast to a C integer type wraps to that type's bits (values.py's 32-bit
ARM sizes). What it won't guess at: calls, struct fields, array elements,
dereferences and sizeof are unknown, and so is comparing two strings' pointers
(==/!= against NULL still works) - C compares the pointers, not the text.

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping

from tree_sitter import Language, Node, Parser
import tree_sitter_c

from skyfish.analysis.values import BOOLEAN, INTEGER, REAL, value_type_for

_PARSER = Parser(Language(tree_sitter_c.language()))

# Names with a meaning of their own, used when no input value covers them.
# (true/false/TRUE/FALSE are boolean literals in the grammar, handled below.)
_BUILTINS = {"NULL": None}

# Node types that would need the program's memory to answer - a value card
# can't provide them, so they're reported as written and left unknown.
_OPAQUE = ("call_expression", "field_expression", "subscript_expression",
           "sizeof_expression", "offsetof_expression", "generic_expression")


@dataclass(frozen=True)
class Evaluation:
    """Which way a condition goes with the values given."""
    value: bool | None          # True / False, or None when it can't be told
    # What stopped it, written as in the code, in the order met, no repeats.
    # Empty when value is known (an unknown the short-circuit made irrelevant
    # isn't listed) and when the condition didn't parse.
    unresolved: tuple[str, ...] = ()


class _Unknown:
    """A subexpression that couldn't be computed, carrying what blocked it."""

    def __init__(self, names: list[str]) -> None:
        self.names = names


def evaluate_condition(condition: str, values: Mapping[str, object]) -> Evaluation:
    """Evaluate a C condition as written on a card ("" - an else row - and
    anything that doesn't parse come back unknown with nothing to report)."""
    expression = _parse(condition)
    if expression is None:
        return Evaluation(None)
    result = _eval(expression, values)
    if isinstance(result, _Unknown):
        return Evaluation(None, tuple(dict.fromkeys(result.names)))
    return Evaluation(_truth(result))


def _parse(condition: str) -> Node | None:
    """The condition's expression node, or None when it isn't one. The text
    is parsed where a condition really sits - in an if inside a function -
    since an expression on its own isn't a C file."""
    if not condition.strip():
        return None
    tree = _PARSER.parse(f"void __skyfish(void) {{ if ({condition}); }}".encode("utf-8"))
    if tree.root_node.has_error:
        return None
    for statement in _find(tree.root_node, "if_statement"):
        parenthesized = statement.child_by_field_name("condition")
        if parenthesized is not None:
            return next((c for c in parenthesized.named_children if c.type != "comment"), None)
    return None


def _find(node: Node, node_type: str):
    for child in node.children:
        if child.type == node_type:
            yield child
        else:
            yield from _find(child, node_type)


def _text(node: Node) -> str:
    return re.sub(r"\s+", " ", node.text.decode("utf-8", errors="replace")).strip()


def _truth(value) -> bool:
    """C truth: a number is its comparison with 0, NULL is false, and a
    pointer with something behind it (str/bytes, even empty) is true."""
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return True
    return bool(value)


def _eval(node: Node, values: Mapping[str, object]):
    """The node's value (int/float/bool/str/bytes, None for NULL) or _Unknown."""
    kind = node.type
    if kind == "parenthesized_expression":
        inner = next((c for c in node.named_children if c.type != "comment"), None)
        return _eval(inner, values) if inner is not None else _Unknown([_text(node)])
    if kind == "binary_expression":
        return _binary(node, values)
    if kind == "unary_expression":
        return _unary(node, values)
    if kind == "conditional_expression":
        return _conditional(node, values)
    if kind == "cast_expression":
        return _cast(node, values)
    if kind == "comma_expression":
        return _eval(node.child_by_field_name("right"), values)
    if kind == "identifier":
        name = _text(node)
        if name in values:
            return values[name]
        if name in _BUILTINS:
            return _BUILTINS[name]
        return _Unknown([name])
    if kind == "number_literal":
        return _number(_text(node))
    if kind == "char_literal":
        return _char(node)
    if kind in ("string_literal", "concatenated_string"):
        return _string(node)
    if kind in ("true", "false"):
        # The grammar reads true/false and the firmware TRUE/FALSE macros as
        # boolean literals - an input value for that exact name still wins.
        name = _text(node)
        if name in values:
            return values[name]
        return kind == "true"
    if kind == "null":
        return None
    if kind in _OPAQUE or kind == "pointer_expression":  # *p and &x need memory too
        return _Unknown([_text(node)])
    return _Unknown([_text(node)])


def _binary(node: Node, values: Mapping[str, object]):
    operator = node.child_by_field_name("operator").type
    left = _eval(node.child_by_field_name("left"), values)
    right = _eval(node.child_by_field_name("right"), values)

    if operator in ("||", "&&"):
        return _logic(operator, left, right)

    # Everything else needs both sides.
    blocked = [n for side in (left, right) if isinstance(side, _Unknown) for n in side.names]
    if blocked:
        return _Unknown(blocked)
    if operator in ("==", "!="):
        return _equality(operator, left, right, node)
    if operator in ("<", "<=", ">", ">="):
        if _is_number(left) and _is_number(right):
            return {"<": left < right, "<=": left <= right,
                    ">": left > right, ">=": left >= right}[operator]
        return _Unknown([_text(node)])  # ordering pointers needs their addresses
    return _arithmetic(operator, left, right, node)


def _logic(operator: str, left, right):
    """|| and && with a side possibly unknown: C short-circuits, so a side
    that settles the answer makes the other side not matter."""
    decider = (operator == "||")  # the value that settles it: true for ||, false for &&
    sides = [side if isinstance(side, _Unknown) else _truth(side) for side in (left, right)]
    if decider in sides:
        return decider
    if all(isinstance(side, bool) for side in sides):
        return not decider
    return _Unknown([n for side in sides if isinstance(side, _Unknown) for n in side.names])


def _equality(operator: str, left, right, node: Node):
    if _is_number(left) and _is_number(right):
        equal = left == right
    elif left is None or right is None:
        equal = left is None and right is None  # x == NULL: is it NULL?
    else:
        # Two pointers (str/bytes): C compares the addresses, which value
        # cards don't have - even equal text can live at two addresses.
        return _Unknown([_text(node)])
    return equal if operator == "==" else not equal


def _arithmetic(operator: str, left, right, node: Node):
    if not (_is_number(left) and _is_number(right)):
        return _Unknown([_text(node)])
    left, right = _as_number(left), _as_number(right)
    integers = isinstance(left, int) and isinstance(right, int)
    try:
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator == "/":
            if right == 0:
                return _Unknown([_text(node)])
            return _trunc_div(left, right) if integers else left / right
        if operator == "%":
            if right == 0:
                return _Unknown([_text(node)])
            # C99: the remainder has the dividend's sign (-7 % 2 is -1).
            return left - _trunc_div(left, right) * right if integers else math.fmod(left, right)
        if integers:
            if operator == "<<":
                return left << right if right >= 0 else _Unknown([_text(node)])
            if operator == ">>":
                return left >> right if right >= 0 else _Unknown([_text(node)])
            if operator == "&":
                return left & right
            if operator == "|":
                return left | right
            if operator == "^":
                return left ^ right
    except (ValueError, OverflowError):
        return _Unknown([_text(node)])
    return _Unknown([_text(node)])


def _trunc_div(a: int, b: int) -> int:
    """C integer division: toward zero, not Python's floor (-7/2 is -3)."""
    return -(-a // b) if (a < 0) != (b < 0) else a // b


def _unary(node: Node, values: Mapping[str, object]):
    operator = node.child_by_field_name("operator").type
    argument = _eval(node.child_by_field_name("argument"), values)
    if operator == "!":
        return argument if isinstance(argument, _Unknown) else not _truth(argument)
    if isinstance(argument, _Unknown):
        return argument
    if not _is_number(argument):
        return _Unknown([_text(node)])
    number = _as_number(argument)
    if operator == "-":
        return -number
    if operator == "+":
        return +number
    if operator == "~" and isinstance(number, int):
        return ~number
    return _Unknown([_text(node)])


def _conditional(node: Node, values: Mapping[str, object]):
    condition = _eval(node.child_by_field_name("condition"), values)
    consequence = _eval(node.child_by_field_name("consequence"), values)
    alternative = _eval(node.child_by_field_name("alternative"), values)
    if not isinstance(condition, _Unknown):
        return consequence if _truth(condition) else alternative
    # Which way it goes is unknown - unless both ways give the same answer.
    if (not isinstance(consequence, _Unknown) and not isinstance(alternative, _Unknown)
            and consequence == alternative and type(consequence) is type(alternative)):
        return consequence
    names = list(condition.names)
    for side in (consequence, alternative):
        if isinstance(side, _Unknown):
            names += side.names
    return _Unknown(names)


def _cast(node: Node, values: Mapping[str, object]):
    value = _eval(node.child_by_field_name("value"), values)
    if isinstance(value, _Unknown):
        return value
    type_name = _text(node.child_by_field_name("type"))
    value_type = value_type_for(type_name, None)
    if value_type.kind == INTEGER and value_type.bits and _is_number(value):
        wrapped = int(_as_number(value)) & ((1 << value_type.bits) - 1)
        if value_type.signed and wrapped > value_type.maximum:
            wrapped -= 1 << value_type.bits
        return wrapped
    if value_type.kind == REAL and _is_number(value):
        return float(_as_number(value))
    if value_type.kind == BOOLEAN:
        return _truth(value)
    return value  # a pointer or struct cast changes the label, not the value


def _is_number(value) -> bool:
    return isinstance(value, (int, float, bool))


def _as_number(value):
    return int(value) if isinstance(value, bool) else value


def _number(text: str):
    cleaned = text.replace("'", "")
    hexadecimal = cleaned.lower().startswith("0x")
    # Strip u/U/l/L suffixes - but an f/F is only a suffix on a decimal float
    # (in 0x1F or 31.0f it's a hex digit / float marker handled below).
    cleaned = re.sub(r"[uUlL]+$", "", cleaned)
    if hexadecimal:
        if re.search(r"[.pP]", cleaned):
            return float.fromhex(cleaned)
        return int(cleaned, 16)
    if re.search(r"[.eE]", cleaned) or cleaned.lower().endswith("f"):
        try:
            return float(cleaned.rstrip("fF"))
        except ValueError:
            return _Unknown([text])
    if re.fullmatch(r"0[0-7]+", cleaned):
        return int(cleaned, 8)  # C's leading-zero octal; int(x, 0) refuses it
    try:
        return int(cleaned, 0)
    except ValueError:
        return _Unknown([text])


_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\", '"': '"', "'": "'",
            "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _unescape(text: str) -> str:
    def replace(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        return _ESCAPES.get(m.group(2), m.group(2))
    return re.sub(r"\\x([0-9A-Fa-f]{1,2})|\\(.)", replace, text)


def _char(node: Node):
    content = _unescape(_text(node)[1:-1])
    return ord(content) if len(content) == 1 else _Unknown([_text(node)])


def _string(node: Node):
    if node.type == "concatenated_string":
        parts = [_string(c) for c in node.named_children if c.type == "string_literal"]
        return "".join(parts)
    return _unescape(_text(node)[1:-1])
