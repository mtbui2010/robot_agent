"""The goal-kind table as pack data: what the goal functions read about each kind, and the sub-goal template.

What: ``fields(entry)`` turns one declared kind of a pack's ``goal_kinds`` section into the fields ``goals.GoalKind`` holds:
``needs`` (an action, or a list of which any one will do), ``prompt`` (the kind's line in the understand prompt),
``understood`` (``always``: in the understand schema even when the robot lacks the actions, so a request is understood and
then refused; ``capable``: only when the robot has them), ``planned_by`` (``planner`` or ``template``: fixed steps, no model),
``conflicts`` (``default``: the goal validator looks at the kind; ``none``: it does not) and ``subgoal`` (the sub-goal phrase
a planner that takes ``subgoals`` expands). ``default_table()`` is the table of a robot that declares no pack of its own;
``is_default(pack)`` tells whether a resolved pack has that table. ``render(template, slots)`` fills a sub-goal template.
Why: the kinds were a closed constant of core/planning/goals.py; a robot pack can now declare its own kinds
(docs/proposals/generalize_core_design.md, "Goal kinds", Step S07). The standard pack's file
(robot_agent/packs/household/goal_kinds.yaml) holds the old values, character for character (tests/test_pack_goal_kinds.py).
How: pure readers of declared data; the default table is read from the shipped files once (no callable is imported).
Sub-goal template: alternatives separated by ``|``; the first one whose guards ``{name?}`` all hold (the slot is not empty)
is used, the guards removed and the other ``{name}`` slots filled (``str.format_map``). No alternative holds: None.
Off: a pack that declares no ``needs`` / ``prompt`` / ``subgoal`` for a kind gives it none (an empty need list, no prompt line,
the goal's plain description as its sub-goal); a field it does not give takes the schema's default.
"""

from __future__ import annotations

import functools
import re
from typing import Mapping, Optional

from robot_agent.contract import pack as schema

from . import loader
from .phrases import default_chain

TABLE = "goal_kinds"
GUARD = re.compile(r"\{(\w+)\?\}")              # {name?}: the alternative holds only when slot `name` is not empty


def _need(n):
    return tuple(n) if isinstance(n, list) else n


def fields(entry) -> dict:
    """One declared kind -> {needs, prompt, understood, planned_by, conflicts, subgoal}: ``needs`` as a tuple (an any-of list
    as a tuple inside), an absent prompt / subgoal as '', an absent flag as the schema's default for it."""
    e = entry if isinstance(entry, dict) else {}
    flag = lambda k: e[k] if k in e else schema.default(TABLE, k)       # noqa: E731
    return {"needs": tuple(_need(n) for n in e.get("needs") or ()), "prompt": e.get("prompt") or "",
            "understood": flag("understood"), "planned_by": flag("planned_by"), "conflicts": flag("conflicts"),
            "subgoal": e.get("subgoal") or ""}


@functools.lru_cache(maxsize=None)
def default_table(root=None) -> dict:
    """{kind: entry as declared} of the packs a robot with no pack of its own gets (the root pack, then the schema-1 base),
    merged as the loader merges a chain. Read once; callers must not change it."""
    return loader.merge(list(default_chain(root)))[0][TABLE]


def is_default(pack) -> bool:
    """True for no pack, or a resolved pack whose goal-kind table is the default one or empty (every robot without its own
    kinds: a pack that declares no goal kind at all, such as a schema-2 pack that only adds questions, is not a robot that
    understands nothing)."""
    return pack is None or not pack.table(TABLE) or pack.table(TABLE) == default_table()


def render(template: str, slots: Mapping) -> Optional[str]:
    """The first alternative of ``template`` whose guards hold, with its slots filled; None when no alternative holds."""
    for alt in str(template).split("|"):
        if all(slots.get(name) for name in GUARD.findall(alt)):
            return GUARD.sub("", alt).format_map(slots)
    return None


__all__ = ["TABLE", "GUARD", "fields", "default_table", "is_default", "render"]
