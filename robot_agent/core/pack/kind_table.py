"""The goal-kind table as declared data: what a kind needs, its understand-prompt line, when it is understood, how it is
planned, whether the goal validator reads it, and its sub-goal phrase.

What: ``GoalKind`` is one kind; ``table(entries)`` turns a pack's ``goal_kinds`` section into {name: GoalKind} in the declared
order; ``is_default(entries)`` says whether a section is the standard chain's own (the shipped files, read without importing
a callable); ``render(template, goal)`` says a goal with a kind's ``subgoal`` template.
Why: the closed table of core/planning/goals.py (GOAL_KINDS, MANIPULATION, TEMPLATE_KINDS, the sub-goal if-chain) is pack
data now (docs/proposals/generalize_core_design.md, "Goal kinds", Step S07), so a robot pack can declare its own kinds.
Template: ``{slot}`` is the goal's value as Python prints it (an absent slot prints None, as the old code did); ``{slot:SEP}``
joins a list value with SEP; ``[A|B]`` keeps the first alternative whose guards ``{?slot}`` (a true value; prints nothing)
all hold, and nothing when none does. Groups do not nest; SEP holds no ``|``, ``[``, ``]``, ``{`` or ``}``.
Off: a kind with no ``subgoal`` is said by ``goals.describe``; ``conflicts: none`` takes a kind out of the goal validator.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from robot_agent.contract import pack as schema

from . import loader
from .phrases import default_chain

TABLE = "goal_kinds"
FIELDS = ("prompt", "understood", "planned_by", "conflicts", "subgoal")     # the text fields a GoalKind takes from an entry
_GROUP = re.compile(r"\[([^\[\]]*)\]")
_SLOT = re.compile(r"\{(\?)?(\w+)(?::([^{}\[\]|]*))?\}")


@dataclass(frozen=True)
class GoalKind:
    """One goal kind. ``needs``: the actions it needs (a tuple inside = any one of them); ``prompt``: its line in the
    understand prompt ('' = described in the base prompt); the other fields mean what the pack schema says
    (robot_agent/contract/pack.py) and default to the schema's defaults."""
    name: str
    needs: tuple
    prompt: str = ""
    understood: str = schema.default(TABLE, "understood")
    planned_by: str = schema.default(TABLE, "planned_by")
    conflicts: str = schema.default(TABLE, "conflicts")
    subgoal: str = ""


def kind(name: str, entry) -> GoalKind:
    """The GoalKind of one ``goal_kinds`` entry (a list in ``needs`` becomes a tuple); a field it leaves out takes the default."""
    e = entry if isinstance(entry, dict) else {}
    needs = tuple(tuple(n) if isinstance(n, (list, tuple)) else n for n in e.get("needs") or ())
    return GoalKind(name, needs, **{f: e[f] for f in FIELDS if isinstance(e.get(f), str)})


def table(entries) -> dict:
    """{name: GoalKind} of a ``goal_kinds`` section, in its order."""
    return {n: kind(n, e) for n, e in (entries or {}).items()}


@functools.lru_cache(maxsize=None)
def _default(root=None) -> dict:
    return loader.merge(list(default_chain(root)))[0].get(TABLE, {})


def default_entries(root=None) -> dict:
    """The ``goal_kinds`` section of the standard chain as the shipped files declare it (a new dict each call)."""
    return dict(_default(root))


def is_default(entries, root=None) -> bool:
    """Is ``entries`` the standard chain's own ``goal_kinds`` section (same kinds, order and fields)?"""
    d = _default(root)
    return entries == d and list(entries) == list(d)


def render(template: str, goal: dict) -> str:
    """``template`` (a kind's ``subgoal``) said for ``goal``; see the module's docstring for the three forms."""
    def choose(m) -> str:
        for alt in m.group(1).split("|"):
            if all(goal.get(n) for guard, n, _sep in _SLOT.findall(alt) if guard):
                return alt
        return ""

    def say(m) -> str:
        guard, n, sep = m.groups()
        if guard:
            return ""
        v = goal.get(n)
        return sep.join(v) if sep is not None else f"{v}"
    return _SLOT.sub(say, _GROUP.sub(choose, template))


__all__ = ["TABLE", "FIELDS", "GoalKind", "kind", "table", "default_entries", "is_default", "render"]
