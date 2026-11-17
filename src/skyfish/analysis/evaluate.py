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
ARM sizes). Calls to functions the C standard pins down are answered by
contract (stdlib_c.py), so strcmp(key, "TEMP") == 0 resolves. What it won't
guess at: other calls, struct fields, array elements, dereferences and sizeof
are unknown, and so is comparing two strings' pointers (==/!= against NULL
still works) - C compares the pointers, not the text.

compute_expression() is the same machinery giving the computed value instead
of true/false - for a #define's value (defines.py) or what an assignment's
right-hand side comes to (a card's black-box rows).

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping

from tree_sitter import Language, Node, Parser
import tree_sitter_c

from skyfish.analysis import stdlib_c
from skyfish.analysis.values import (BOOLEAN, INTEGER, REAL, _C_BOOLS, _C_INTEGERS,
                                     value_type_for)

_PARSER = Parser(Language(tree_sitter_c.language()))

# Names with a meaning of their own, used when no input value covers them.
# (true/false/TRUE/FALSE are boolean literals in the grammar, handled below.)
_BUILTINS = {"NULL": None}

# Node types that would need the program's memory to answer - a value card
# can't provide them, so they're reported as written and left unknown.
_OPAQUE = ("field_expression", "subscript_expression",
           "offsetof_expression", "generic_expression")

_POINTER_BYTES = 4  # 32-bit ARM, matching values.py's integer sizes
_REAL_BYTES = {"float": 4, "double": 8, "long double": 8}


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


@dataclass(frozen=True)
class _Scope:
    """What the evaluation sees: input values, and (for sizeof) each known
    name's declared type as written ({"number": "char[12]"})."""
    values: Mapping[str, object]
    types: Mapping[str, str]


@dataclass(frozen=True)
class Computation:
    """What an expression comes to with the values given."""
    known: bool
    value: object = None        # int/float/bool/str/bytes, None for NULL
    unresolved: tuple[str, ...] = ()  # as in Evaluation; filled when not known


def evaluate_condition(condition: str, values: Mapping[str, object],
                       types: Mapping[str, str] | None = None) -> Evaluation:
    """Evaluate a C condition as written on a card ("" - an else row - and
    anything that doesn't parse come back unknown with nothing to report).
    types is each name's declared type as written ({"number": "char[12]"}),
    for sizeof(name) - leave it out for today's behaviour."""
    expression = _parse(condition)
    if expression is None:
        return Evaluation(None)
    result = _eval(expression, _Scope(values, types or {}))
    if isinstance(result, _Unknown):
        return Evaluation(None, tuple(dict.fromkeys(result.names)))
    return Evaluation(_truth(result))


def compute_expression(expression: str, values: Mapping[str, object],
                       types: Mapping[str, str] | None = None) -> Computation:
    """The value of a C expression - an Action's right-hand side, a #define's
    body - rather than just which way it leans. types as evaluate_condition's."""
    node = _parse(expression)
    if node is None:
        return Computation(False)
    result = _eval(node, _Scope(values, types or {}))
    if isinstance(result, _Unknown):
        return Computation(False, unresolved=tuple(dict.fromkeys(result.names)))
    return Computation(True, result)


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


def _eval(node: Node, scope: _Scope):
    """The node's value (int/float/bool/str/bytes, None for NULL) or _Unknown."""
    kind = node.type
    if kind == "parenthesized_expression":
        inner = next((c for c in node.named_children if c.type != "comment"), None)
        return _eval(inner, scope) if inner is not None else _Unknown([_text(node)])
    if kind == "binary_expression":
        return _binary(node, scope)
    if kind == "unary_expression":
        return _unary(node, scope)
    if kind == "conditional_expression":
        return _conditional(node, scope)
    if kind == "cast_expression":
        return _cast(node, scope)
    if kind == "comma_expression":
        return _eval(node.child_by_field_name("right"), scope)
    if kind == "call_expression":
        return _call(node, scope)
    if kind == "identifier":
        name = _text(node)
        if name in scope.values:
            return scope.values[name]
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
        if name in scope.values:
            return scope.values[name]
        return kind == "true"
    if kind == "null":
        return None
    if kind == "sizeof_expression":
        return _sizeof(node, scope)
    if kind in _OPAQUE or kind == "pointer_expression":  # *p and &x need memory too
        return _Unknown([_text(node)])
    return _Unknown([_text(node)])


def _call(node: Node, scope: _Scope):
    """A call answered by its contract (stdlib_c) when the function and the
    arguments it needs are known. An unknown stdlib argument reports the
    argument's blockers; an unknown function reports the call as written."""
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if function is None or function.type != "identifier" or arguments is None:
        return _Unknown([_text(node)])
    args: list = []
    blocked: list[tuple[str, ...]] = []
    for child in arguments.named_children:
        if child.type == "comment":
            continue
        result = _eval(child, scope)
        if isinstance(result, _Unknown):
            blocked.append(tuple(result.names))
            args.append(stdlib_c.UNKNOWN)
        else:
            blocked.append(())
            args.append(result)
    effect = stdlib_c.call_effect(_text(function), args)
    if effect is None:
        return _Unknown([_text(node)])
    if effect.value is stdlib_c.UNKNOWN:
        # Blame the argument the contract actually needed, when it can say.
        if effect.blocked is not None and effect.blocked < len(blocked) and blocked[effect.blocked]:
            return _Unknown(list(blocked[effect.blocked]))
        names = [name for argument in blocked for name in argument]
        return _Unknown(names or [_text(node)])
    return effect.value


def _sizeof(node: Node, scope: _Scope):
    """sizeof(name) from the name's declared type, sizeof(type) from the C
    sizes values.py uses (32-bit ARM). sizeof of anything that needs the
    program - an expression, a struct, a typedef this file doesn't declare -
    stays unknown, reported as written."""
    target = node.child_by_field_name("value")
    descriptor = node.child_by_field_name("type")
    if target is not None:
        # sizeof x / sizeof(x): the grammar calls x an expression. Only a
        # plain name can be sized - sizeof(buf[0]) and friends need memory.
        if target.type == "parenthesized_expression":
            target = next((c for c in target.named_children if c.type != "comment"), None)
        if target is None or target.type != "identifier":
            return _Unknown([_text(node)])
        written = _text(target)
    elif descriptor is not None:
        # sizeof(uint16_t), sizeof(char[12]) - and a variable whose name
        # reads like a type (number_t) lands here too, so try both ways.
        written = _text(descriptor)
    else:
        return _Unknown([_text(node)])
    declared = scope.types.get(written, written)
    size = _c_sizeof(declared, scope)
    return size if size is not None else _Unknown([_text(node)])


def _c_sizeof(type_name: str, scope: _Scope) -> int | None:
    """Bytes a declared type takes on the firmware's 32-bit ARM, or None for
    one this can't size (structs, unions, unknown typedefs, empty [])."""
    text = re.sub(r"\s+", " ", type_name).strip()
    text = re.sub(r"\b(const|volatile|static|register)\b", "", text).strip()
    array = re.search(r"\[\s*([^\[\]]*?)\s*\]$", text)
    if array:
        element = _c_sizeof(text[:array.start()], scope)
        dimension = array.group(1)
        if element is None or not dimension:
            return None  # char buf[] has no size of its own
        if dimension.isdigit():
            return int(dimension) * element  # char[12]
        parsed = _parse(dimension)
        count = _eval(parsed, scope) if parsed is not None else None
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return count * element  # uint8_t[TX_MAX] with TX_MAX known
        return None
    if text.endswith("*") or "(*" in text:
        return _POINTER_BYTES  # any data or function pointer
    if text.startswith("enum "):
        return 4  # an enum is an int on ARM
    if text in _C_INTEGERS:
        return _C_INTEGERS[text][0] // 8
    if text in _REAL_BYTES:
        return _REAL_BYTES[text]
    if text in _C_BOOLS:
        return 1
    return None


def _binary(node: Node, scope: _Scope):
    operator = node.child_by_field_name("operator").type
    left = _eval(node.child_by_field_name("left"), scope)
    right = _eval(node.child_by_field_name("right"), scope)

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


def _unary(node: Node, scope: _Scope):
    operator = node.child_by_field_name("operator").type
    argument = _eval(node.child_by_field_name("argument"), scope)
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


def _conditional(node: Node, scope: _Scope):
    condition = _eval(node.child_by_field_name("condition"), scope)
    consequence = _eval(node.child_by_field_name("consequence"), scope)
    alternative = _eval(node.child_by_field_name("alternative"), scope)
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


def _cast(node: Node, scope: _Scope):
    value = _eval(node.child_by_field_name("value"), scope)
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
