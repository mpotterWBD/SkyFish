"""The #define constants in a file, as values the evaluator can use.

A firmware condition leans on object-like macros constantly ("length >
TX_MAX", "state == STATE_IDLE") - without their values every one is unknown.
defines() reads the file with the same tree-sitter C grammar as c_parser.py
and computes each #define whose body it can (numbers, chars, strings, and
expressions over earlier #defines, with evaluate.py's C arithmetic).

What's left out stays out quietly: function-like macros, bare flags
(#define DEBUG), and bodies that need the program (casts of registers, calls)
- a name that isn't in the result simply stays unknown on the cards. Merge
the result UNDER the card values so an input the operator typed always wins:
evaluate_condition(cond, {**defines(text), **card_values}).

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

import re

from tree_sitter import Language, Node, Parser
import tree_sitter_c

from skyfish.analysis.evaluate import compute_expression

_PARSER = Parser(Language(tree_sitter_c.language()))


def defines(text: str) -> dict[str, object]:
    """{name: value} for every #define in text whose value can be computed,
    in file order - later ones may use earlier ones (#define B (A * 2))."""
    found: dict[str, object] = {}
    for node in _preproc_defs(_PARSER.parse(text.encode("utf-8")).root_node):
        name = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name is None or value is None:
            continue  # a bare flag like "#define DEBUG" has no value to hold
        body = _body_text(value)
        result = compute_expression(body, found)
        if result.known:
            found[name.text.decode("utf-8", errors="replace")] = result.value
    return found


def _preproc_defs(node: Node):
    """Every object-like #define, in file order - they can sit inside #ifdef
    blocks and extern "C" wrappers. Function-like macros (preproc_function_def)
    are a different node type and aren't yielded."""
    for child in node.children:
        if child.type == "preproc_def":
            yield child
        else:
            yield from _preproc_defs(child)


def _body_text(value: Node) -> str:
    """The #define's body as one line: continuation backslashes folded, any
    trailing comment dropped (tree-sitter keeps both in the preproc_arg)."""
    body = value.text.decode("utf-8", errors="replace")
    body = re.sub(r"\\\r?\n", " ", body)
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.DOTALL)
    body = re.sub(r"//.*", "", body)
    return body.strip()
