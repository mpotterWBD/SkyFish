"""Runs a function top to bottom with the card values, carrying state.

The evaluator answers one condition at a time; this walks the whole flow the
way the hardware would run it: locals pick up their initial values, each
Action runs (used = strlen(dataCSV) feeds the next if), each if goes the way
its condition says, and the walk ends at the return it reaches. The result is
a Trace: a Step per flow node (keyed by its Span) saying what happened there,
the output reached, and every name's final value - what the cards animate.

Honesty rules. Anything marked "in loop" / "in switch" runs zero or many
times, not once - its step is unknown and whatever it assigns is forgotten
(poisoned), and a return there makes the final output uncertain even when a
later return is sure to be reached otherwise. An if that can't be decided
leaves both ways possible: its bodies run as "could happen" (assignments
poison, returns are maybes), and the walk carries on where it's still sure.
A call with no contract ("?" on the card) loses its pointer arguments -
the callee could have written anything behind them.

No Qt here, so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from skyfish.analysis import stdlib_c
from skyfish.analysis.evaluate import compute_expression, evaluate_condition
from skyfish.analysis.functions import LOCAL, FunctionInfo
from skyfish.analysis.logic import END, Action, Exit, Forever, IfChain, Span

# Step statuses - what happened at one flow node on this run.
RAN = "ran"              # an Action that happened, its values computed
TAKEN = "taken"          # the branch the flow went down / the exit it left by
NOT_TAKEN = "not taken"  # a branch whose condition came out false
SKIPPED = "skipped"      # never reached (after the exit, or in an untaken branch)
UNKNOWN = "unknown"      # couldn't be told: an undecidable if, a "?" call,
#                          anything in a loop, or anything only maybe reached


@dataclass
class Step:
    """What happened at one Action, branch header or Exit."""
    status: str
    returned: object = None  # a call's return value, or the value an Exit returns
    known: bool = False      # whether returned is real (None can be a real NULL)
    changes: dict[str, object] = field(default_factory=dict)  # {"used": 6, "tx": "T=125\r"}
    unresolved: tuple[str, ...] = ()  # why it's unknown, as written ("used", "HAL_GetTick()")
    unknown_call: bool = False        # a call with no contract - the "?" box


@dataclass
class Trace:
    """One run of a function with one set of input values."""
    steps: dict[Span, Step]   # keyed by Action.span, Branch.span, Exit.span -
    #                           and the function's end_span for the implicit END
    output: str | None        # the card output reached (Exit.value or END); None if uncertain
    state: dict[str, object]  # every name whose final value is known


def simulate(function: FunctionInfo, inputs: Mapping[str, object],
             defines: Mapping[str, object] | None = None) -> Trace:
    """Run function with the value cards' inputs (and the file's #defines
    underneath them - an input the operator typed wins)."""
    walk = _Walk({**(defines or {}), **inputs})
    walk.seed(function)
    walk.run(function.flow, _LIVE)
    return walk.trace(function)


# Internal walk modes: definitely running, only possibly running (an
# undecidable if's body, or after a maybe-return), definitely not running.
_LIVE, _UNSURE, _DEAD = "live", "unsure", "dead"

# A name whose value the walk lost track of. Kept in state so a later honest
# assignment can resurrect it, filtered out of everything public.
_POISON = object()


class _Walk:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.steps: dict[Span, Step] = {}
        self.exited: str | None = None   # the Exit.value the walk surely left by
        self.maybe_exited = False        # a return that only might have happened
        self.forever = False             # a never-ending loop was reached

    # -- state ----------------------------------------------------------------

    def seed(self, function: FunctionInfo) -> None:
        """Give locals their initial values, in declaration order, so
        "int used = 0;" counts even though it isn't an Action row."""
        for variable in sorted(function.variables, key=lambda v: v.line):
            if variable.kind != LOCAL or not variable.initial or variable.name in self.state:
                continue
            result = compute_expression(variable.initial, self.known())
            if result.known:
                self.state[variable.name] = result.value

    def known(self) -> dict:
        return {k: v for k, v in self.state.items() if v is not _POISON}

    def _record(self, span: Span | None, step: Step) -> None:
        if span is not None:
            self.steps[span] = step

    # -- the walk -------------------------------------------------------------

    def run(self, nodes: list, mode: str) -> str:
        """Walk nodes in order; returns the mode the flow leaves in (a
        maybe-return downgrades live to unsure)."""
        for node in nodes:
            if self.exited is not None or self.forever:
                mode = _DEAD
            if isinstance(node, Action):
                self._action(node, mode)
            elif isinstance(node, IfChain):
                mode = self._chain(node, mode)
            elif isinstance(node, Exit):
                mode = self._exit(node, mode)
            elif isinstance(node, Forever):
                self._record(node.span, Step(_STATUS_HERE[mode]))
                if mode == _LIVE:
                    self.forever = True
        return mode

    def _action(self, action: Action, mode: str) -> None:
        if mode == _DEAD:
            self._record(action.span, Step(SKIPPED))
            return
        sure = mode == _LIVE and not action.context  # a loop's body runs 0..n times
        step = Step(RAN if sure else UNKNOWN)
        if action.call:
            self._call(action, step, sure)
        if action.target:
            self._assign(action, step, sure)
        if step.unresolved or step.unknown_call:
            step.status = UNKNOWN
        self._record(action.span, step)

    def _call(self, action: Action, step: Step, sure: bool) -> None:
        """Fill the step in from the call's contract - and be honest about a
        call with none: it could have rewritten whatever its pointers reach."""
        if not stdlib_c.knows(action.call):
            step.unknown_call = True
            step.unresolved = (action.expression or action.text,)
            for argument in action.args:
                if argument in self.state and isinstance(self.known().get(argument), (str, bytes)):
                    self.state[argument] = _POISON  # the callee may have written into it
            return
        values, blockers = [], []
        for argument in action.args:
            result = compute_expression(argument, self.known())
            values.append(result.value if result.known else stdlib_c.UNKNOWN)
            blockers.append(() if result.known else (result.unresolved or (argument,)))
        effect = stdlib_c.call_effect(action.call, values)
        if effect.value is stdlib_c.UNKNOWN:
            # Blame the argument the contract actually needed (snprintf never
            # reads its buffer), or everything unknown when it can't say.
            if effect.blocked is not None and effect.blocked < len(blockers) \
                    and blockers[effect.blocked]:
                step.unresolved = tuple(dict.fromkeys(blockers[effect.blocked]))
            else:
                flat = [name for argument in blockers for name in argument]
                step.unresolved = tuple(dict.fromkeys(flat)) or (action.expression or action.text,)
            return
        step.returned, step.known = effect.value, True
        for index, contents in effect.changes:
            written = action.args[index]
            step.changes[written] = contents
            if written.isidentifier():
                self.state[written] = contents if sure else _POISON

    def _assign(self, action: Action, step: Step, sure: bool) -> None:
        result = compute_expression(action.expression, self.known())
        if sure and result.known:
            self.state[action.target] = result.value
            step.changes[action.target] = result.value
            return
        if action.target.isidentifier():
            self.state[action.target] = _POISON  # maybe assigned, or to who-knows-what
        if not result.known and not step.unresolved:
            step.unresolved = result.unresolved or (action.expression,)

    def _chain(self, chain: IfChain, mode: str) -> str:
        taken = False    # an earlier branch surely ran
        unsure = False   # an earlier branch couldn't be decided
        could_return = self.maybe_exited
        for branch in chain.branches:
            if mode == _DEAD or taken:
                self._record(branch.span, Step(SKIPPED))
                self.run(branch.body, _DEAD)
                continue
            if branch.keyword == "else":
                goes = None if unsure else True
                unresolved: tuple[str, ...] = ()
            else:
                result = evaluate_condition(branch.condition, self.known())
                # True only helps if every earlier undecided branch was false.
                goes = None if (unsure and result.value) else result.value
                unresolved = result.unresolved
            if goes is True:
                taken = True
                self._record(branch.span, Step(TAKEN if mode == _LIVE else UNKNOWN))
                mode = self.run(branch.body, mode)
            elif goes is False:
                self._record(branch.span, Step(NOT_TAKEN))
                self.run(branch.body, _DEAD)
            else:
                unsure = True
                self._record(branch.span, Step(UNKNOWN, unresolved=unresolved))
                self.run(branch.body, _UNSURE)
        if mode == _LIVE and self.maybe_exited and not could_return:
            mode = _UNSURE  # a branch that may have run may have returned
        return mode

    def _exit(self, node: Exit, mode: str) -> str:
        if mode == _DEAD:
            self._record(node.span, Step(SKIPPED))
            return mode
        step = Step(TAKEN)
        if node.value != END:
            result = compute_expression(node.value, self.known())
            step.returned, step.known = result.value, result.known
            if not result.known:
                step.unresolved = result.unresolved or (node.value,)
        if mode == _LIVE and not node.context:
            self.exited = node.value  # the walk surely ends here
        else:
            step.status = UNKNOWN  # in a loop, or on a path that only might run
            self.maybe_exited = True
            if mode == _LIVE:
                mode = _UNSURE  # if it returned, nothing after here ran
        self._record(node.span, step)
        return mode

    # -- the result -----------------------------------------------------------

    def trace(self, function: FunctionInfo) -> Trace:
        if self.exited is not None:
            output = None if self.maybe_exited else self.exited
        elif self.forever or self.maybe_exited:
            output = None  # never ends, or may have left through a maybe-return
        else:
            output = END
            self._record(function.end_span, Step(TAKEN, known=False))
        return Trace(self.steps, output, self.known())


_STATUS_HERE = {_LIVE: TAKEN, _UNSURE: UNKNOWN, _DEAD: SKIPPED}
