"""Finds the functions and methods in one source file, and the variables
(parameters and locals) inside each.

No Qt here, so it can be tested on its own. Python uses the standard `ast`
parser, so it's exact. C and C++ use a brace-aware scanner: comments,
strings and preprocessor lines are blanked out first, then each top-level
statement is checked for a `name(...)` signature, and each function body for
declarations. It handles normal C and C++ (including `Class::method` and
methods inside a class body) but isn't a full compiler front end - heavy
macro tricks can fool it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from skyfish.analysis.logic import END, Branch, Exit, Forever, IfChain, Span

FUNCTION = "function"
METHOD = "method"

PARAMETER = "parameter"
LOCAL = "local"


@dataclass(frozen=True)
class VariableInfo:
    name: str
    kind: str  # PARAMETER or LOCAL
    line: int  # 1-based line where it's first declared or assigned
    # C/C++: the declared type as written ("const char *", "char[TX_MAX]").
    # Python: the annotation, else a type read off the first value assigned
    # (see _python_value_type); "" when it can't be told.
    type: str = ""
    # The variable written as it's declared, without any initial value:
    # C "static const char *verdict", "uint8_t buf[2]"; Python "count: int"
    # (annotation style), or just the name when the type can't be told.
    declaration: str = ""
    span: Span | None = None  # its declaration in the code (C), or where it's first set (Python)
    # C: the initial value as written ("0", "TX_MAX - 1", "calcCrc(buf)"),
    # "" when the declaration has none - simulate.py seeds locals from it.
    initial: str = ""


@dataclass
class FunctionInfo:
    name: str  # qualified, e.g. "MainWindow.analyze" or "Foo::bar"
    kind: str  # FUNCTION or METHOD
    line: int  # 1-based line of the name
    # False for a C/C++ prototype: declared here, body is elsewhere.
    has_body: bool = True
    variables: list[VariableInfo] = field(default_factory=list)
    # The body's if-chains and returns (see analysis/logic.py), and the last
    # line, for the implicit "end" exit. Empty for a prototype.
    flow: list = field(default_factory=list)
    end_line: int = 0
    span: Span | None = None      # the whole function, signature to closing brace
    end_span: Span | None = None  # its closing brace (C) - where an implicit "end" exit leaves


class UnsupportedLanguage(Exception):
    pass


PYTHON_EXTS = frozenset({".py", ".pyw"})
C_FAMILY_EXTS = frozenset({".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh"})
# Read with the tree-sitter C parser (analysis/c_parser.py); the rest of
# C_FAMILY_EXTS (C++) still use the brace-aware scanner below.
C_EXTS = frozenset({".c", ".h"})
SUPPORTED_EXTS = PYTHON_EXTS | C_FAMILY_EXTS


def find_functions(text: str, suffix: str) -> list[FunctionInfo]:
    """Functions and methods in text, in file order. suffix is the file's
    extension (".py", ".c" ...) and picks the parser. Raises
    UnsupportedLanguage for other extensions, and SyntaxError for Python
    that doesn't parse."""
    suffix = suffix.lower()
    if suffix in PYTHON_EXTS:
        return _python_functions(text)
    if suffix in C_EXTS:
        from skyfish.analysis.c_parser import c_functions  # it uses this module's helpers
        return c_functions(text)
    if suffix in C_FAMILY_EXTS:
        return _c_functions(text)
    raise UnsupportedLanguage(suffix or "(no extension)")


def find_functions_in_file(path: Path) -> list[FunctionInfo]:
    path = Path(path)
    return find_functions(path.read_text(encoding="utf-8", errors="replace"), path.suffix)


# --- Python -------------------------------------------------------------------

_PY_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


# The file being read, as UTF-8 lines: ast gives columns in bytes, the code
# view wants characters. Set by _python_functions for its helpers.
_py_lines: list[bytes] = []


def _py_col(line: int, byte_col: int) -> int:
    row = _py_lines[line - 1] if 0 < line <= len(_py_lines) else b""
    return len(row[:byte_col].decode("utf-8", errors="replace"))


def _py_span(start: ast.AST, end: ast.AST | None = None) -> Span:
    """From the start of start to the end of end (default: start's end)."""
    end = end or start
    return Span(start.lineno, _py_col(start.lineno, start.col_offset),
                end.end_lineno, _py_col(end.end_lineno, end.end_col_offset))


def _python_functions(text: str) -> list[FunctionInfo]:
    global _py_lines
    _py_lines = text.encode("utf-8").splitlines()
    found: list[FunctionInfo] = []

    def visit(body: list[ast.AST], prefix: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.", True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = METHOD if in_class else FUNCTION
                found.append(FunctionInfo(f"{prefix}{node.name}", kind, node.lineno,
                                          variables=_python_variables(node, in_class),
                                          flow=_python_flow(node.body), end_line=node.end_lineno,
                                          span=_py_span(node)))
                # Nested functions are listed too, as outer.inner.
                visit(node.body, f"{prefix}{node.name}.", False)
            else:
                # Functions defined under if/try/with blocks still count.
                for name in ("body", "orelse", "finalbody", "handlers"):
                    child = getattr(node, name, None)
                    if isinstance(child, list):
                        visit([c for c in child if isinstance(c, ast.AST)], prefix, in_class)

    visit(ast.parse(text).body, "", False)
    return sorted(found, key=lambda f: f.line)


def _python_variables(func: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> list[VariableInfo]:
    """Parameters, then local names in first-assigned order. Leaves out a
    method's self/cls, names declared global/nonlocal, and anything inside
    nested functions, classes, lambdas and comprehensions (their own scopes)."""
    a = func.args
    params = [*a.posonlyargs, *a.args, *([a.vararg] if a.vararg else []),
              *a.kwonlyargs, *([a.kwarg] if a.kwarg else [])]
    if is_method and params and params[0].arg in ("self", "cls"):
        params = params[1:]
    out = []
    for p in params:
        type_ = ast.unparse(p.annotation) if p.annotation else ""
        out.append(VariableInfo(p.arg, PARAMETER, p.lineno, type_, _py_declaration(p.arg, type_),
                                span=_py_span(p)))
    seen = {p.arg for p in params}

    not_local: set[str] = set()
    locals_: list[VariableInfo] = []
    annotated: dict[str, str] = {}  # an annotation wins wherever it is
    assigned: dict[str, str] = {}   # else the first informative assignment's type

    def note_assigned(name: str, value: ast.expr) -> None:
        # "x = None" then "x = Foo()" is a Foo set later, so a None or unknown
        # value gives way to the next one that says more.
        new, old = _python_value_type(value), assigned.get(name, "")
        if not old or (old == "None" and new):
            assigned[name] = new or old

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                not_local.update(child.names)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                annotated.setdefault(child.target.id, ast.unparse(child.annotation))
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        note_assigned(target.id, child.value)
            elif isinstance(child, ast.NamedExpr):
                note_assigned(child.target.id, child.value)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                locals_.append(VariableInfo(child.id, LOCAL, child.lineno, span=_py_span(child)))
            elif isinstance(child, ast.ExceptHandler) and child.name:
                exc_type = ast.unparse(child.type) if child.type is not None else "BaseException"
                annotated.setdefault(child.name, exc_type)
                locals_.append(VariableInfo(child.name, LOCAL, child.lineno, span=_py_span(child)))
            if isinstance(child, _PY_SCOPES):
                continue
            walk(child)

    for stmt in func.body:
        if not isinstance(stmt, _PY_SCOPES):
            walk(ast.Module(body=[stmt], type_ignores=[]))

    for v in sorted(locals_, key=lambda v: v.line):
        if v.name not in seen and v.name not in not_local:
            seen.add(v.name)
            type_ = annotated.get(v.name) or assigned.get(v.name, "")
            out.append(VariableInfo(v.name, LOCAL, v.line, type_, _py_declaration(v.name, type_), span=v.span))
    return out


def _python_flow(body: list[ast.stmt], context: str = "") -> list:
    """The if-chains and returns in body (see analysis/logic.py). Loops,
    with, try and match are see-through for now: the ifs and returns inside
    them are kept, the block itself isn't drawn. A return in a block that
    only sometimes runs it (a loop, an except handler, a match case) is
    marked with that context, since it may not happen. Nested functions and
    classes are left out - they have their own entries."""
    nodes: list = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            nodes.append(_python_if_chain(stmt))
        elif isinstance(stmt, ast.Return):
            value = ast.unparse(stmt.value) if stmt.value is not None else END
            nodes.append(Exit(value, stmt.lineno, context, _py_span(stmt)))
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            nodes += _python_flow(stmt.body + stmt.orelse, "in loop")
            if (isinstance(stmt, ast.While) and isinstance(stmt.test, ast.Constant)
                    and stmt.test.value and not _python_breaks(stmt.body)):
                nodes.append(Forever(stmt.lineno, _py_span(stmt, stmt.test)))  # while True: with no break
        elif isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
            nodes += _python_flow(stmt.body + stmt.orelse, context)
            for handler in stmt.handlers:
                caught = f"in except {ast.unparse(handler.type)}" if handler.type is not None else "in except"
                nodes += _python_flow(handler.body, caught)
            nodes += _python_flow(stmt.finalbody, context)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                nodes += _python_flow(case.body, f"in case {ast.unparse(case.pattern)}")
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            nodes += _python_flow(stmt.body, context)
    return nodes


def _python_breaks(body: list[ast.stmt]) -> bool:
    """Whether body has a break that ends the loop it belongs to (not one
    inside a nested loop, function or class)."""
    for stmt in body:
        if isinstance(stmt, ast.Break):
            return True
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.iter_child_nodes(stmt):
            blocks = [child.body] if isinstance(child, (ast.ExceptHandler, ast.match_case)) else []
            for block in blocks:
                if _python_breaks(block):
                    return True
        for name in ("body", "orelse", "finalbody"):
            block = getattr(stmt, name, None)
            if isinstance(block, list) and _python_breaks(block):
                return True
    return False


def _python_if_chain(node: ast.If) -> IfChain:
    def branch(keyword: str, test: ast.If) -> Branch:
        # Header: from "if"/"elif" to the end of the condition; body: its statements.
        return Branch(keyword, ast.unparse(test.test), test.lineno, _python_flow(test.body),
                      span=_py_span(test, test.test), body_span=_py_span(test.body[0], test.body[-1]))

    branches = [branch("if", node)]
    orelse = node.orelse
    # "elif" is an If alone in the previous If's else.
    while len(orelse) == 1 and isinstance(orelse[0], ast.If):
        node = orelse[0]
        branches.append(branch("elif", node))
        orelse = node.orelse
    if orelse:
        # ast keeps no position for the "else:" line itself, so its body stands in.
        body = _py_span(orelse[0], orelse[-1])
        branches.append(Branch("else", "", orelse[0].lineno, _python_flow(orelse), span=body, body_span=body))
    return IfChain(branches)


_BUILTIN_TYPES = frozenset({
    "int", "float", "complex", "str", "bytes", "bytearray", "bool", "list", "dict", "set",
    "frozenset", "tuple", "range", "object", "type", "memoryview",
})


def _python_value_type(value: ast.expr) -> str:
    """The type a value obviously has, or "" when it isn't obvious. Only
    clear cases: literals, displays and comprehensions, f-strings,
    comparisons and not, and calls to a class (a builtin type, or a
    capitalised name like QLabel or QtWidgets.QLabel)."""
    if isinstance(value, ast.Constant):
        return "None" if value.value is None else type(value.value).__name__
    if isinstance(value, ast.JoinedStr):
        return "str"
    for node_types, name in (((ast.List, ast.ListComp), "list"), ((ast.Dict, ast.DictComp), "dict"),
                             ((ast.Set, ast.SetComp), "set"), ((ast.Tuple,), "tuple"),
                             ((ast.Compare,), "bool")):
        if isinstance(value, node_types):
            return name
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
        return "bool"
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in _BUILTIN_TYPES or name[:1].isupper():
            return name
    return ""


# --- C / C++ ------------------------------------------------------------------

# Words that can sit right before "(" without being a function name.
_NOT_NAMES = frozenset({
    "if", "for", "while", "switch", "return", "sizeof", "alignof", "decltype", "typeof",
    "__attribute__", "__declspec", "__asm", "asm", "defined", "catch", "noexcept",
    "void", "int", "char", "short", "long", "float", "double", "unsigned", "signed",
    "static_assert", "_Static_assert", "operator", "throw", "new", "delete",
})
# Words that can start a statement without it being a declaration.
_NOT_TYPES = frozenset({
    "return", "else", "goto", "case", "default", "break", "continue", "delete", "throw",
    "sizeof", "typedef", "using", "do", "if", "while", "switch", "new", "co_return", "co_yield",
})
_DECL_PREFIX = re.compile(
    r"^(?:(?:const|volatile|static|register|extern|inline|unsigned|signed|long|short|struct|enum|union|auto|constexpr)\s+)*"
    r"([A-Za-z_]\w*)(?:\s*::\s*\w+)*(?:\s*<[^;{}]*?>)?(?=[\s*&])"
)
_IDENT_BEFORE = re.compile(r"(~?[A-Za-z_]\w*(?:\s*::\s*~?[A-Za-z_]\w*)*)\s*$")
_CLASS_HEADER = re.compile(r"^\s*(?:template\s*<.*>\s*)?(class|struct|union)\s+(?:\w+\s+)*?([A-Za-z_]\w*)?\s*(?:final\s*)?(?::[^{]*)?$", re.S)
_TRANSPARENT_HEADER = re.compile(r"^\s*(extern\s*|namespace(\s+[\w:]+)?\s*)$")
_FUNC_PTR_NAME = re.compile(r"\(\s*[*&]\s*([A-Za-z_]\w*)\s*\)")
_LAST_IDENT = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$")


def _blank_non_code(text: str, keep_strings: bool = False) -> str:
    """Comments, string/char literals and preprocessor lines become spaces,
    keeping every newline so positions still map to the same line. With
    keep_strings, literals stay (for showing code, such as a condition)."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    at_line_start = True
    while i < n:
        c = text[i]
        if at_line_start and c in " \t":
            i += 1
            continue
        if at_line_start and c == "#":
            # Preprocessor line, plus any backslash-continued lines.
            j = i
            while j < n:
                end = text.find("\n", j)
                if end == -1:
                    end = n
                if text[j:end].rstrip("\r").endswith("\\"):
                    j = end + 1
                    continue
                break
            blank(i, end)
            i = end
            continue
        at_line_start = c == "\n"
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            blank(i, end)
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            blank(i, end)
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            if not keep_strings:
                blank(i, min(j + 1, n))
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _split_top_level(text: str, start: int) -> list[tuple[str, int]]:
    """Splits text on commas not inside (), [], {} or <>; returns each piece
    with its absolute offset (start + offset in text)."""
    pieces, depth, begin = [], 0, 0
    for i, c in enumerate(text):
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            # Clamped: a comparison like "a > b" closes nothing.
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            pieces.append((text[begin:i], start + begin))
            begin = i + 1
    pieces.append((text[begin:], start + begin))
    return pieces


def _declarator_name(piece: str) -> tuple[str, int] | None:
    """The variable name in one declarator ("*p", "buf[4]", "x = 3",
    "(*cb)(int)"), with its offset in piece, or None."""
    m = _FUNC_PTR_NAME.search(piece)
    if m:
        return m.group(1), m.start(1)
    before_init = re.split(r"=|\{", piece, maxsplit=1)[0]
    m = _LAST_IDENT.search(before_init.rstrip())
    if m:
        return m.group(1), m.start(1)
    return None


def _signature(header: str, start: int, class_name: str | None = None) -> tuple[str, int, int, int] | None:
    """If header (a statement's text before its "{" or ";") is a function
    signature, returns (name, absolute offset of the name, absolute offsets
    of the parameter list's "(" and ")"), else None. class_name is the
    enclosing C++ class, whose constructor has no return type."""
    if not header.strip() or header.lstrip().startswith(("typedef", "using")):
        return None
    depth = 0
    for i, c in enumerate(header):
        if c == "(":
            if depth == 0:
                m = _IDENT_BEFORE.search(header, 0, i)
                if m:
                    name = re.sub(r"\s+", "", m.group(1))
                    base = name.rsplit("::", 1)[-1].lstrip("~")
                    prefix = header[:m.start()]
                    # "=" or an open "[" before it means this is a call inside an
                    # initialiser or array size, not a signature.
                    inside_brackets = prefix.count("[") > prefix.count("]")
                    if base not in _NOT_NAMES and "=" not in prefix and not inside_brackets:
                        # Needs a return type before it, except constructors and
                        # destructors: qualified (Foo::Foo), ~Foo, or Foo inside class Foo.
                        if prefix.strip() or "::" in name or name.startswith("~") or name == class_name:
                            close = _matching_paren(header, i)
                            return name, start + m.start(), start + i, start + close
                        return None
            depth += 1
        elif c == ")":
            depth -= 1
    return None


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for j in range(open_index, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(text)


def _c_type(declaration: str, name: str, name_offset: int) -> str:
    """The type part of one declaration: its text with the name taken out
    and any initialiser dropped, tidied to single spaces ("const char *line"
    -> "const char *", "char tx[MAX]" -> "char[MAX]", "void (*cb)(int)" ->
    "void (*)(int)")."""
    return _c_tidy(declaration[:name_offset] + declaration[name_offset + len(name):])


def _c_tidy(declaration: str) -> str:
    """One declaration without its initial value, tidied to single spaces:
    "static  const char *  verdict = x" -> "static const char *verdict"."""
    text = re.split(r"=|\{", declaration, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([\[\)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\*\s+(?=[*\w(])", "*", text)
    return text


def _py_declaration(name: str, type_: str) -> str:
    return f"{name}: {type_}" if type_ else name


def _c_parameters(code: str, open_paren: int, close_paren: int) -> list[tuple[str, int, str, str]]:
    params = []
    for piece, offset in _split_top_level(code[open_paren + 1:close_paren], open_paren + 1):
        stripped = piece.strip()
        if not stripped or stripped in ("void", "..."):
            continue
        # A parameter needs a type before its name; "int" alone (unnamed) has none.
        if len(re.findall(r"[A-Za-z_]\w*", re.sub(r"\[.*?\]|=.*", "", stripped))) < 2 and "(" not in stripped:
            continue
        found = _declarator_name(piece)
        if found:
            params.append((found[0], offset + found[1], _c_type(piece, *found), _c_tidy(piece)))
    return params


def _c_locals(code: str, body_start: int, body_end: int) -> list[tuple[str, int, str, str]]:
    """Declared variables in a function body, any block depth. Each statement
    (text between ; { and }) is checked for "type name [= ...], name2 ...";
    "for (int i = 0" is included."""
    out = []
    begin = body_start
    for i in range(body_start, body_end + 1):
        if i == body_end or code[i] in ";{}":
            stmt = code[begin:i]
            lead = len(stmt) - len(stmt.lstrip())
            text = stmt.lstrip()
            offset = begin + lead
            m = re.match(r"for\s*\(\s*", text)
            if m:
                text, offset = text[m.end():], offset + m.end()
            decl = _DECL_PREFIX.match(text)
            if decl and decl.group(1) not in _NOT_TYPES and not text.startswith(decl.group(0) + "("):
                rest_start = decl.end()
                for piece, piece_offset in _split_top_level(text[rest_start:], offset + rest_start):
                    name_part = piece.lstrip(" \t\n*&")
                    # Each declarator starts with its name (or "(*name)" for a
                    # function pointer); "x = y" or "i < n" don't, so aren't
                    # declarations. "type name(" is a call or prototype.
                    if not re.match(r"[A-Za-z_(]", name_part):
                        break
                    if re.match(r"[A-Za-z_]\w*\s*\(", name_part) and not _FUNC_PTR_NAME.search(piece):
                        break
                    found = _declarator_name(piece)
                    if found and found[0] not in _NOT_TYPES:
                        # "uint8_t a, *p" -> each declarator gets the shared base
                        # type plus its own "*" or "[...]".
                        own = text[:rest_start] + " " + piece
                        type_ = _c_type(own, found[0], rest_start + 1 + found[1])
                        out.append((found[0], piece_offset + found[1], type_, _c_tidy(own)))
            begin = i + 1
    return out


class _CFlowParser:
    """Reads a C/C++ function body into the flow tree of analysis/logic.py.

    Works on two copies of the source with the same character positions:
    `code` (comments and strings blanked) for structure, so a brace or
    keyword inside a string can't confuse it, and `show` (comments blanked,
    strings kept) for the condition and return text people see. Loops,
    switch and plain blocks are see-through for now: the ifs and returns
    inside are kept, the loop itself isn't drawn."""

    _OPEN_CLOSE = {"(": ")", "[": "]", "{": "}"}

    def __init__(self, code: str, show: str, line_of) -> None:
        self.code, self.show, self.line_of = code, show, line_of
        # One entry per loop/switch being read, innermost last: True once a
        # break that ends it has been seen.
        self._breaks: list[bool] = []

    def _always_true(self, word: str, paren: int, close: int) -> bool:
        """Whether a loop header never lets the loop end: while(1),
        while(true), for(;;) and the like."""
        inside = re.sub(r"\s+", "", self.code[paren + 1:close])
        if word == "for":
            return re.fullmatch(r"[^;]*;(1|true|TRUE)?;[^;]*", inside) is not None
        return inside in ("1", "true", "TRUE", "1U", "!0")

    def _skip_space(self, i: int, end: int) -> int:
        while i < end and self.code[i].isspace():
            i += 1
        return i

    def _word_at(self, i: int) -> str | None:
        if i > 0 and (self.code[i - 1].isalnum() or self.code[i - 1] == "_"):
            return None
        m = re.match(r"[A-Za-z_]\w*", self.code[i:i + 40])
        return m.group(0) if m else None

    def _match(self, open_index: int) -> int:
        """Index of the bracket closing the one at open_index."""
        opener = self.code[open_index]
        closer = self._OPEN_CLOSE[opener]
        depth = 0
        for j in range(open_index, len(self.code)):
            if self.code[j] == opener:
                depth += 1
            elif self.code[j] == closer:
                depth -= 1
                if depth == 0:
                    return j
        return len(self.code) - 1

    def _statement_end(self, i: int, end: int) -> int:
        """Index of the ";" ending the plain statement at i (or of a "}" at
        its level, when the ";" is missing)."""
        depth = 0
        for j in range(i, end):
            c = self.code[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    return j
                depth -= 1
            elif c == ";" and depth == 0:
                return j
        return end

    def _text(self, a: int, b: int) -> str:
        return re.sub(r"\s+", " ", self.show[a:b]).strip()

    def block(self, start: int, end: int, context: str = "") -> list:
        """The flow of the statements from start to end. context marks
        returns met directly in here (not inside an if): "in loop" or "in
        switch", as they only sometimes run."""
        nodes: list = []
        i = self._skip_space(start, end)
        while i < end:
            found, i = self._statement(i, end, context)
            nodes += found
            i = self._skip_space(i, end)
        return nodes

    def _statement(self, i: int, end: int, context: str = "") -> tuple[list, int]:
        c = self.code[i]
        if c == "{":
            close = self._match(i)
            return self.block(i + 1, close, context), close + 1
        if c == ";":
            return [], i + 1
        word = self._word_at(i)
        if word == "if":
            return self._if_chain(i, end)
        if word == "return":
            semi = self._statement_end(i, end)
            value = self._text(i + len("return"), semi)
            return [Exit(value or END, self.line_of(i), context)], semi + 1
        if word == "break":
            if self._breaks:
                self._breaks[-1] = True  # the innermost loop or switch ends
            return [], self._statement_end(i, end) + 1
        if word in ("for", "while", "switch"):
            paren = self._skip_space(i + len(word), end)
            if paren < end and self.code[paren] == "(":
                close = self._match(paren)
                body_start = self._skip_space(close + 1, end)
                inner = "in switch" if word == "switch" else "in loop"
                self._breaks.append(False)
                body, after = self._statement(body_start, end, inner) if body_start < end else ([], end)
                broke = self._breaks.pop()
                if word != "switch" and not broke and self._always_true(word, paren, close):
                    body = [*body, Forever(self.line_of(i))]
                return body, after
        if word == "do":
            self._breaks.append(False)
            body, after = self._statement(self._skip_space(i + 2, end), end, "in loop")
            broke = self._breaks.pop()
            j = self._skip_space(after, end)
            if self._word_at(j) == "while":
                paren = self._skip_space(j + len("while"), end)
                if paren < end and self.code[paren] == "(":
                    close = self._match(paren)
                    if not broke and self._always_true("while", paren, close):
                        body = [*body, Forever(self.line_of(i))]
                    j = self._skip_space(close + 1, end)
                    if j < end and self.code[j] == ";":
                        j += 1
            return body, j
        if word in ("case", "default"):
            colon = self.code.find(":", i)
            return [], (colon + 1 if 0 <= colon < end else end)
        if word == "else":  # stray else (its if was consumed): skip the word
            return [], i + len("else")
        return [], self._statement_end(i, end) + 1

    def _if_chain(self, i: int, end: int) -> tuple[list, int]:
        branches: list[Branch] = []
        keyword = "if"
        while True:
            paren = self._skip_space(i + 2, end)
            if paren >= end or self.code[paren] != "(":
                return [IfChain(branches)] if branches else [], self._statement_end(i, end) + 1
            close = self._match(paren)
            body_start = self._skip_space(close + 1, end)
            body, after = self._statement(body_start, end) if body_start < end else ([], end)
            branches.append(Branch(keyword, self._text(paren + 1, close), self.line_of(i), body))
            k = self._skip_space(after, end)
            if k < end and self._word_at(k) == "else":
                m = self._skip_space(k + len("else"), end)
                if m < end and self._word_at(m) == "if":
                    i, keyword = m, "else if"
                    continue
                body, after = self._statement(m, end) if m < end else ([], end)
                branches.append(Branch("else", "", self.line_of(k), body))
            return [IfChain(branches)], after


def _c_functions(text: str) -> list[FunctionInfo]:
    code = _blank_non_code(text)
    flow_parser = _CFlowParser(code, _blank_non_code(text, keep_strings=True),
                               lambda pos: code.count("\n", 0, pos) + 1)

    def line_of(pos: int) -> int:
        return code.count("\n", 0, pos) + 1

    found: list[FunctionInfo] = []
    # Each open brace is ("transparent",) for extern "C" / namespace (keep
    # looking inside), ("class", name) (look inside; signatures there are
    # methods), ("body", function, start) for a function body, or ("other",)
    # for anything else (an initialiser, or a block inside a body).
    stack: list[tuple] = []
    stmt_start = 0

    def scanning() -> bool:
        return all(entry[0] in ("transparent", "class") for entry in stack)

    def owner() -> str | None:
        for entry in reversed(stack):
            if entry[0] == "class":
                return entry[1]
        return None

    def record(header_start: int, header_end: int, has_body: bool) -> FunctionInfo | None:
        cls = owner()
        sig = _signature(code[header_start:header_end], header_start, cls)
        if sig is None:
            return None
        name, pos, open_paren, close_paren = sig
        if cls:
            name = f"{cls}::{name}"
        kind = METHOD if cls or "::" in name else FUNCTION
        info = FunctionInfo(name, kind, line_of(pos), has_body=has_body)
        if has_body:
            info.variables = [VariableInfo(n, PARAMETER, line_of(p), t, d)
                              for n, p, t, d in _c_parameters(code, open_paren, close_paren)]
        found.append(info)
        return info

    for i, c in enumerate(code):
        if c == "{":
            if scanning():
                header = code[stmt_start:i]
                cls = _CLASS_HEADER.match(header)
                if _TRANSPARENT_HEADER.match(header):
                    stack.append(("transparent",))
                elif cls and "(" not in header:
                    stack.append(("class", cls.group(2)))
                else:
                    info = record(stmt_start, i, has_body=True)
                    stack.append(("body", info, i + 1) if info else ("other",))
            else:
                stack.append(("other",))
            stmt_start = i + 1
        elif c == "}":
            if stack:
                entry = stack.pop()
                if entry[0] == "body":
                    info, body_start = entry[1], entry[2]
                    info.flow = flow_parser.block(body_start, i)
                    info.end_line = line_of(i)
                    names = {v.name for v in info.variables}
                    for name, pos, type_, decl in _c_locals(code, body_start, i):
                        if name not in names:
                            names.add(name)
                            info.variables.append(VariableInfo(name, LOCAL, line_of(pos), type_, decl))
            stmt_start = i + 1
        elif c == ";" and scanning():
            record(stmt_start, i, has_body=False)
            stmt_start = i + 1
        elif c == ":" and scanning() and owner() and re.fullmatch(r"\s*(public|private|protected)\s*", code[stmt_start:i]):
            stmt_start = i + 1  # C++ access label inside a class
    return found
