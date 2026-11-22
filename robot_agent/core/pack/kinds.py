"""The goal-kind table of a pack, as the engine reads it: what each kind needs, its understand-prompt line, when the model may
use it, how it is planned, whether the goal validator looks at it, and its sub-goal phrase.

What: ``GoalKind`` (one kind), ``from_entry`` (a pack entry -> GoalKind; an absent field takes the schema's default),
``standard()`` (the kinds of the standard pack chain, read from the shipped files; no callable is imported), ``table(pack,
view)`` (the kinds of a robot's resolved pack, in the pack's order) and ``render(template, slots)`` (a sub-goal phrase).
Why: the kinds were a closed table in core/planning/goals.py (GOAL_KINDS, MANIPULATION, TEMPLATE_KINDS and the if-chain of
subgoal_text); a robot pack declares its own now (docs/proposals/generalize_core_design.md, "Goal kinds", Step S07).
How: ``goals.GOAL_KINDS`` is the standard table, built once from the standard pack's ``goal_kinds.yaml``; ``table`` gives a
pack entry that equals the standard entry as the GoalKind of that view, so a test or tool that patches ``goals.GOAL_KINDS``
still reaches a run, and builds any other entry from the pack.
Off: a robot with no ``pack:`` section gets the standard kinds, which equal the old table (tests/test_pack_goal_kinds.py); a
schema-2 pack changes only its own kinds (``remove``, a replaced entry, a new kind); ``conflicts: none`` takes one kind out of
the goal validator.

Sub-goal template (the field ``subgoal``): ``{name}`` is a slot of the goal; ``[...]`` is kept only when every slot named
inside it is non-empty; ``A || B`` are alternatives, the first usable one is taken; an alternative that starts with
``?<name>: `` is usable only when that slot is non-empty. The slots themselves are the caller's (``goals.subgoal_text``).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Mapping

from robot_agent.contract import pack as schema

from . import loader

TABLE = "goal_kinds"
_ALT = " || "
_GUARD = re.compile(r"\?(\w+): ")
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")
_SLOT = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class GoalKind:
    """One goal kind. The defaults are the schema's defaults (contract/pack.py DEFAULTS), so a kind built with a name and its
    needs only means what a pack entry with only ``needs`` means."""
    name: str
    needs: tuple                    # the actions the kind needs; a tuple inside = any one of them
    prompt: str = ""                # the understand prompt's line for it ('' = described in the base prompt)
    understood: str = "capable"     # always: in the model's enum even when the robot lacks the actions; capable: only then
    planned_by: str = "planner"     # planner / template (fixed steps, no model) / tree
    conflicts: str = "default"      # default: the goal validator looks at the kind; none: it does not
    subgoal: str = ""               # the sub-goal phrase template ('' = the goal's describe() form)


def from_entry(name: str, entry) -> GoalKind:
    """The GoalKind of the pack entry ``entry`` (a mapping as declared): ``needs`` lists become tuples."""
    e = entry if isinstance(entry, Mapping) else {}
    get = lambda k: e[k] if e.get(k) is not None else schema.default(TABLE, k)       # noqa: E731
    needs = tuple(tuple(n) if isinstance(n, list) else n for n in e.get("needs") or ())
    return GoalKind(name, needs, e.get("prompt") or "", get("understood"), get("planned_by"), get("conflicts"),
                    e.get("subgoal") or "")


@functools.lru_cache(maxsize=None)
def standard_entries(root=None) -> dict:
    """{name: entry as declared} of the standard chain (the root pack, then the schema-1 base), merged; read once."""
    base = loader.index(root).get("schema_1_base")
    chain = loader.chain_of(loader.shipped(base, root), root) if base else []
    return loader.merge(chain)[0][TABLE]


def standard(root=None) -> dict:
    """{name: GoalKind} of the standard chain, in file order (a new dict each call)."""
    return {n: from_entry(n, e) for n, e in standard_entries(root).items()}


def table(pack, view: Mapping) -> dict:
    """{name: GoalKind} of the resolved ``pack``, in its order. An entry equal to the standard one is ``view[name]`` (the
    engine's standard table, which tests patch); any other entry is built from the pack."""
    std = standard_entries()
    return {n: view[n] if n in view and e == std.get(n) else from_entry(n, e) for n, e in pack.table(TABLE).items()}


def render(template: str, slots: Mapping) -> str:
    """The text of ``template`` with ``slots`` (a mapping; a slot is looked up only when the template names it). ``{name}``
    is formatted as Python formats the value. No usable alternative: ''."""
    for alt in template.split(_ALT):
        guard = _GUARD.match(alt)
        if guard and not slots[guard.group(1)]:
            continue
        body = alt[guard.end():] if guard else alt
        body = _OPTIONAL.sub(lambda m: m.group(1) if all(slots[n] for n in _SLOT.findall(m.group(1))) else "", body)
        return body.format_map(slots)
    return ""


__all__ = ["TABLE", "GoalKind", "from_entry", "standard_entries", "standard", "table", "render"]
