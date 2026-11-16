"""A function's logic as paths from its inputs to its outputs.

The parsers in functions.py reduce a function body to a small flow tree:
if-chains (each branch with its condition and its own nested flow) and exits
(return statements). Calls and assignments ride along as Actions - the black
boxes a card draws beside its logic - but plain statements and the loops and
switches the ifs sit in are left out, so what remains is "which conditions
lead to which return, and what gets done along the way".

logic_rows() flattens that tree into the rows a card draws: one row per
branch, indented by nesting, with the return value the branch exits through
(if it does). A branch that doesn't return is a detour: its row has no exit
and the flow carries on after it. Actions aren't logic rows - read them
straight off the flow (actions() gathers them) and place them by line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The output a function has when it returns no value: a bare "return", or
# running off the end of its body.
END = "end"


@dataclass(frozen=True)
class Span:
    """Where something is in the source: 1-based lines, 0-based character
    columns (characters, not bytes, so it lines up with the code view), end
    exclusive. Ties a card's rows to the exact code they came from."""
    line: int
    col: int
    end_line: int
    end_col: int

    def contains(self, line: int, col: int) -> bool:
        start, end, here = (self.line, self.col), (self.end_line, self.end_col), (line, col)
        return start <= here <= end

    @property
    def size(self) -> tuple[int, int]:
        """For picking the innermost of nested spans: (lines, columns)."""
        return (self.end_line - self.line, self.end_col - self.col)


@dataclass
class Exit:
    value: str  # the returned expression as written, or END
    line: int
    # Where a return sits when it's inside a block that only sometimes runs
    # it but isn't drawn yet ("in loop", "in except ValueError", "in
    # switch"); "" when it's at its branch's (or the function's) own level.
    context: str = ""
    span: Span | None = None  # the return statement


@dataclass
class Forever:
    """A loop that never ends - while(1), for(;;), while True - with no break
    of its own. Nothing after it runs, and the function can't run off its
    end, so there's no implicit END exit."""
    line: int
    span: Span | None = None  # the loop's header


@dataclass
class Branch:
    keyword: str    # "if", "else if" / "elif", or "else"
    condition: str  # as written, whitespace tidied; "" for else
    line: int
    body: list = field(default_factory=list)  # list[IfChain | Exit]
    span: Span | None = None       # the header: "if (cond)", "else if (cond)", "else"
    body_span: Span | None = None  # what runs when it's taken


@dataclass
class IfChain:
    branches: list[Branch]


@dataclass(frozen=True)
class Action:
    """A call or assignment - a statement that does something on the way to
    an exit, drawn as its own row (a black box for a call). One of these is a
    bare call (no target), an assignment (x = ..., x += ..., x++), or a
    declaration whose initial value is a call (uint16_t crc = calc(buf);)."""
    text: str      # the statement as written (no trailing ;)
    target: str    # what's assigned, as written ("" for a bare call)
    call: str      # the called function ("" when the value isn't a call)
    args: tuple[str, ...]  # the call's arguments, each as written
    # The value the target ends up with, as an expression compute_expression
    # can take: the = right-hand side, "x + (2)" for x += 2, "x + 1" for x++.
    # "" for a bare call (the call itself is in call/args).
    expression: str
    line: int
    span: Span | None = None  # the whole statement


def actions(flow: list) -> list[Action]:
    """Every Action in the flow, branches included, in source order."""
    found: list[Action] = []
    for node in flow:
        if isinstance(node, Action):
            found.append(node)
        elif isinstance(node, IfChain):
            for branch in node.branches:
                found += actions(branch.body)
    return sorted(found, key=lambda a: a.line)


@dataclass(frozen=True)
class LogicRow:
    indent: int        # nesting depth, 0 = the function's own level
    keyword: str       # "if", "else if", "elif", "else", or ALWAYS / OTHERWISE
    condition: str
    line: int
    exit: str | None   # the output this row leads to, or None for a detour
    span: Span | None = None       # its code: the branch header, or the return
    body_span: Span | None = None  # a branch's body (clicking inside it finds the row)
    exit_span: Span | None = None  # the return a branch exits through

    @property
    def is_branch(self) -> bool:
        """An if / else-if / else row, rather than an always / otherwise /
        "in loop" exit row."""
        return self.keyword in BRANCH_KEYWORDS


BRANCH_KEYWORDS = frozenset({"if", "elif", "else if", "else"})

# Keywords for exit rows that aren't a branch: a return (or the end of the
# function) reached by every path ("always"), or by the paths that didn't
# already exit ("otherwise").
ALWAYS = "always"
OTHERWISE = "otherwise"
# Keyword for the row where a never-ending loop stops the flow (no exit).
FOREVER = "runs forever"


def terminates(nodes: list) -> bool:
    """True when every path through nodes ends in a return - so the code
    after them (or the end of the function) can't be reached."""
    nodes = [n for n in nodes if not isinstance(n, Action)]  # doing isn't exiting
    if not nodes:
        return False
    last = nodes[-1]
    if isinstance(last, Forever):
        return True
    if isinstance(last, Exit):
        return not last.context  # a return in a loop or handler may not run
    return any(b.keyword == "else" for b in last.branches) and all(terminates(b.body) for b in last.branches)


def logic_rows(flow: list, end_line: int, end_span: Span | None = None) -> list[LogicRow]:
    """The rows a card draws for a function's flow (see the module doc).
    end_line (and end_span, the closing brace) are for the implicit END exit."""
    rows: list[LogicRow] = []

    def fallback_keyword() -> str:
        return OTHERWISE if any(r.exit for r in rows) else ALWAYS

    def walk(nodes: list, indent: int) -> None:
        for node in nodes:
            if isinstance(node, Action):
                continue  # drawn as its own black-box row, not a logic row
            if isinstance(node, IfChain):
                for branch in node.branches:
                    exit_node = next((n for n in branch.body if isinstance(n, Exit)), None)
                    rows.append(LogicRow(indent, branch.keyword, branch.condition, branch.line,
                                         exit_node.value if exit_node else None, branch.span,
                                         branch.body_span, exit_node.span if exit_node else None))
                    walk([n for n in branch.body if isinstance(n, IfChain)], indent + 1)
            elif isinstance(node, Forever):
                if indent == 0:
                    rows.append(LogicRow(0, FOREVER, "", node.line, None, node.span))
                    return  # nothing after a never-ending loop runs
            elif indent == 0 and node.context:
                rows.append(LogicRow(0, node.context, "", node.line, node.value, node.span,
                                     exit_span=node.span))
            elif indent == 0:
                rows.append(LogicRow(0, fallback_keyword(), "", node.line, node.value, node.span,
                                     exit_span=node.span))
                return  # anything after a top-level return can't run

    walk(flow, 0)
    if not terminates(flow):
        rows.append(LogicRow(0, fallback_keyword(), "", end_line, END, end_span, exit_span=end_span))
    return rows


def outputs(rows: list[LogicRow]) -> list[str]:
    """The distinct exits, in the order rows first reach them."""
    seen: list[str] = []
    for row in rows:
        if row.exit is not None and row.exit not in seen:
            seen.append(row.exit)
    return seen
