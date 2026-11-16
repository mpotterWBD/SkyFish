"""Reads C (.c / .h) with tree-sitter - a real C parser that ships as a
ready-built wheel, so no C compiler is involved.

Produces the same FunctionInfo / VariableInfo / flow tree as the rest of
analysis/, plus the exact location (Span) of everything: each function,
each parameter and local declaration, each if header and body, each return.
Those spans are what tie a card's rows to the code they came from.

tree-sitter parses code with preprocessor lines and HAL macros left in, and
keeps going past anything it can't read (it marks an ERROR node and carries
on), so a file with unusual macros still gives everything it can.
"""

from __future__ import annotations

import re

from tree_sitter import Language, Node, Parser
import tree_sitter_c

from skyfish.analysis.functions import (
    FUNCTION,
    LOCAL,
    PARAMETER,
    FunctionInfo,
    VariableInfo,
    _c_tidy,
    _c_type,
)
from skyfish.analysis.logic import END, Branch, Exit, Forever, IfChain, Span

_PARSER = Parser(Language(tree_sitter_c.language()))

# Declarator wrappers to look through on the way to a name.
_DECLARATOR_WRAPPERS = ("pointer_declarator", "array_declarator", "init_declarator",
                        "parenthesized_declarator", "attributed_declarator", "function_declarator")
_LOOPS = ("for_statement", "while_statement", "do_statement")


class _Source:
    """The file's text, with byte offsets (tree-sitter's) turned into
    character positions (the code view's)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.data = text.encode("utf-8")
        self._lines = self.data.split(b"\n")

    def _char_col(self, row: int, byte_col: int) -> int:
        line = self._lines[row] if row < len(self._lines) else b""
        return len(line[:byte_col].decode("utf-8", errors="replace"))

    def span(self, start: Node, end: Node | None = None) -> Span:
        """From the start of start to the end of end (default: start's end)."""
        end = end or start
        (r0, c0), (r1, c1) = start.start_point, end.end_point
        return Span(r0 + 1, self._char_col(r0, c0), r1 + 1, self._char_col(r1, c1))

    def of(self, node: Node) -> str:
        return self.data[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def code(self, node: Node) -> str:
        """node's text with comments taken out and whitespace tidied - for
        conditions and return values shown on a card."""
        pieces, last = [], node.start_byte
        for comment in _descendants(node, "comment"):
            pieces.append(self.data[last:comment.start_byte])
            last = comment.end_byte
        pieces.append(self.data[last:node.end_byte])
        return re.sub(r"\s+", " ", b" ".join(pieces).decode("utf-8", errors="replace")).strip()


def _descendants(node: Node, node_type: str, stop_at: tuple[str, ...] = ()):
    """Every descendant of node of node_type, not looking inside stop_at types."""
    for child in node.children:
        if child.type == node_type:
            yield child
        if child.type not in stop_at:
            yield from _descendants(child, node_type, stop_at)


def _name_of(declarator: Node | None) -> Node | None:
    """The identifier a declarator declares, looking through *, [], = value,
    (...) and function-pointer wrappers."""
    while declarator is not None and declarator.type in _DECLARATOR_WRAPPERS:
        if declarator.type == "parenthesized_declarator":
            # "(*cb)" in "void (*cb)(int)": the inside isn't a named field.
            declarator = declarator.named_children[0] if declarator.named_children else None
        else:
            declarator = declarator.child_by_field_name("declarator")
    return declarator if declarator is not None and declarator.type in ("identifier", "field_identifier") else None


def _function_declarator(declarator: Node | None) -> Node | None:
    """The function_declarator of a function (not a function pointer):
    "f(...)" or "*f(...)" for one returning a pointer. None otherwise."""
    while declarator is not None and declarator.type in ("pointer_declarator", "attributed_declarator"):
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None or declarator.type != "function_declarator":
        return None
    inner = declarator.child_by_field_name("declarator")
    return declarator if inner is not None and inner.type == "identifier" else None


def c_functions(text: str) -> list[FunctionInfo]:
    source = _Source(text)
    tree = _PARSER.parse(source.data)
    found: list[FunctionInfo] = []

    def visit(node: Node) -> None:
        for child in node.children:
            if child.type == "function_definition":
                info = _definition(child, source)
                if info is not None:
                    found.append(info)
            elif child.type == "declaration":
                # A prototype; anything else declared at file level isn't a function.
                for declarator in child.children_by_field_name("declarator"):
                    func = _function_declarator(declarator)
                    if func is not None:
                        name = func.child_by_field_name("declarator")
                        found.append(FunctionInfo(source.of(name), FUNCTION, name.start_point[0] + 1,
                                                  has_body=False, span=source.span(child)))
            else:
                # extern "C" { }, #ifdef blocks, and anything tree-sitter
                # couldn't place (ERROR) - functions can be inside any of them.
                visit(child)

    visit(tree.root_node)
    return sorted(found, key=lambda f: f.line)


def _definition(node: Node, source: _Source) -> FunctionInfo | None:
    func = _function_declarator(node.child_by_field_name("declarator"))
    body = node.child_by_field_name("body")
    if func is None or body is None:
        return None
    name = func.child_by_field_name("declarator")
    variables = _parameters(func, source)
    seen = {v.name for v in variables}
    for local in _locals(body, source):
        if local.name not in seen:
            seen.add(local.name)
            variables.append(local)
    closing = body.children[-1] if body.children and body.children[-1].type == "}" else body
    return FunctionInfo(
        source.of(name), FUNCTION, name.start_point[0] + 1,
        variables=variables,
        flow=_block(body, source, ""),
        end_line=body.end_point[0] + 1,
        span=source.span(node),
        end_span=source.span(closing),
    )


# --- variables ----------------------------------------------------------------

def _parameters(func: Node, source: _Source) -> list[VariableInfo]:
    params = func.child_by_field_name("parameters")
    out = []
    for param in params.children if params is not None else []:
        if param.type != "parameter_declaration":
            continue  # "(", ",", ")" and "..."
        name = _name_of(param.child_by_field_name("declarator"))
        if name is None:
            continue  # "void", or a type with no name
        text = source.of(param)
        offset = len(source.data[param.start_byte:name.start_byte].decode("utf-8", errors="replace"))
        out.append(VariableInfo(source.of(name), PARAMETER, name.start_point[0] + 1,
                                _c_type(text, source.of(name), offset), _c_tidy(text),
                                span=source.span(param)))
    return out


def _locals(body: Node, source: _Source) -> list[VariableInfo]:
    """Every variable declared in the body, at any depth (including
    "for (int i = 0; ...)"), in the order they appear."""
    out = []
    for declaration in _descendants(body, "declaration"):
        # The shared part before the first name: "static const char", "uint8_t".
        declarators = declaration.children_by_field_name("declarator")
        if not declarators:
            continue
        prefix = source.data[declaration.start_byte:declarators[0].start_byte].decode("utf-8", errors="replace")
        for declarator in declarators:
            if _function_declarator(declarator) is not None:
                continue  # a prototype inside a function
            name = _name_of(declarator)
            if name is None:
                continue
            target = declarator.child_by_field_name("declarator") if declarator.type == "init_declarator" else declarator
            own = prefix + source.of(target)
            offset = len(prefix) + len(source.data[target.start_byte:name.start_byte].decode("utf-8", errors="replace"))
            out.append(VariableInfo(source.of(name), LOCAL, name.start_point[0] + 1,
                                    _c_type(own, source.of(name), offset), _c_tidy(own),
                                    span=source.span(declaration)))
    return out


# --- flow -----------------------------------------------------------------------

def _block(node: Node, source: _Source, context: str) -> list:
    """The flow of the statements in a compound statement (or of one statement)."""
    if node.type == "compound_statement":
        nodes: list = []
        for child in node.named_children:
            nodes += _statement(child, source, context)
        return nodes
    return _statement(node, source, context)


def _statement(node: Node, source: _Source, context: str) -> list:
    kind = node.type
    if kind == "compound_statement":
        return _block(node, source, context)
    if kind == "if_statement":
        return [_if_chain(node, source)]
    if kind == "return_statement":
        value = next((c for c in node.named_children if c.type != "comment"), None)
        return [Exit(source.code(value) if value is not None else END, node.start_point[0] + 1,
                     context, source.span(node))]
    if kind in _LOOPS:
        body = node.child_by_field_name("body")
        nodes = _block(body, source, "in loop") if body is not None else []
        if _never_ends(node, source) and not _has_own_break(body):
            header_end = node.child_by_field_name("condition") or node
            nodes.append(Forever(node.start_point[0] + 1, source.span(node, header_end)))
        return nodes
    if kind == "switch_statement":
        body = node.child_by_field_name("body")
        return _block(body, source, "in switch") if body is not None else []
    if kind == "case_statement":
        nodes = []
        for child in node.named_children:
            if child.type.endswith("statement") or child.type == "declaration":
                nodes += _statement(child, source, context)
        return nodes
    if kind == "labeled_statement":
        nodes = []
        for child in node.named_children:
            if child.type.endswith("statement"):
                nodes += _statement(child, source, context)
        return nodes
    return []


def _if_chain(node: Node, source: _Source) -> IfChain:
    branches: list[Branch] = []
    keyword, header_start = "if", node
    while True:
        condition = node.child_by_field_name("condition")
        consequence = node.child_by_field_name("consequence")
        inner = condition.named_children[0] if condition is not None and condition.named_children else condition
        branches.append(Branch(
            keyword, source.code(inner) if inner is not None else "", header_start.start_point[0] + 1,
            _block(consequence, source, "") if consequence is not None else [],
            span=source.span(header_start, condition or header_start),
            body_span=source.span(consequence) if consequence is not None else None,
        ))
        alternative = node.child_by_field_name("alternative")
        if alternative is None:
            return IfChain(branches)
        # tree-sitter-c wraps the else part in an else_clause.
        else_body = alternative.named_children[-1] if alternative.type == "else_clause" else alternative
        if else_body.type == "if_statement":
            keyword, header_start, node = "else if", alternative, else_body
            continue
        else_keyword = alternative.children[0] if alternative.type == "else_clause" else alternative
        branches.append(Branch("else", "", alternative.start_point[0] + 1,
                               _block(else_body, source, ""),
                               span=source.span(else_keyword), body_span=source.span(else_body)))
        return IfChain(branches)


def _never_ends(node: Node, source: _Source) -> bool:
    """while (1) / while (true) / for (;;) / do { } while (1)."""
    if node.type == "for_statement":
        return node.child_by_field_name("condition") is None
    condition = node.child_by_field_name("condition")
    if condition is None:
        return False
    return re.sub(r"[\s()]", "", source.code(condition)) in ("1", "true", "TRUE", "1U", "!0")


def _has_own_break(body: Node | None) -> bool:
    """A break that ends this loop - not one in a nested loop or switch."""
    if body is None:
        return False
    return any(True for _ in _descendants(body, "break_statement", stop_at=(*_LOOPS, "switch_statement")))
