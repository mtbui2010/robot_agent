"""The "done" of a declared goal kind that names no rules callable (Step S29): its ``holds`` list and its ``phrase`` key.

What: a schema-2 pack may declare a goal kind with no ``rules`` (a registered ``goal.rules`` object) but with ``holds``: a
list of predicates over the goal's slots (``"placed(?object, ?to)"``, ``"fastened(?object)"``). For such a kind:
``holds`` is True when every predicate of the list is True in the belief (the world, its fact store and the run's ``placed``
map); "cannot tell" (None) is NOT holding. ``unmet`` lists the predicates a plan refutes: the list is evaluated on a copy of
the belief after the steps (the action model with the run's declared effects, ``apply``); here "cannot tell" is "not
refuted", because a predicate that reads an observation cannot be decided from a plan (only the end check decides).
``phrase`` is the read-back phrase from the kind's ``phrase`` key (the pack's ``phrases`` table, slots ``object``,
``place`` = the goal's ``to``, ``source`` = its ``from``, and any other field of the goal by name).
Why: a robot pack with a kind of its own (PEX ``install``) had no way to say when it is done; the core read such a goal as
vacuously done (docs/proposals/generalize_core_design.md, "Goal kinds", "What done means, per case").
How: pure functions (data in, decision out); robot_agent/core/pack/goal_semantics.py asks them for a kind it finds no
rules object for. The predicates are read with the evaluators of core/pack/action_rules.py (one reading of a predicate).
Off: a kind with ``rules`` never comes here, and no shipped pack declares a kind without ``rules``: a robot that declares
no such kind runs as before (an unknown kind stays vacuously done). The schema refuses a kind with neither ``rules`` nor
``holds`` (robot_agent/contract/pack.py).
"""

from __future__ import annotations

from typing import Callable, Optional

from robot_agent.contract.pack import pred_parts

from . import action_rules
from .predicates import View

TABLE = "goal_kinds"
PLACE_SLOTS = {"place": "to", "source": "from"}       # a phrase's slot name -> the goal field it reads (others: same name)


def declared(pack, kind) -> Optional[dict]:
    """The entry of goal kind ``kind`` when ``pack`` declares it with a ``holds`` list and no ``rules``; else None."""
    e = pack.entry(TABLE, kind) if pack is not None and isinstance(kind, str) else None
    return e if isinstance(e, dict) and not e.get("rules") and e.get("holds") else None


def bound(goal: dict) -> dict:
    """{variable -> value} a predicate of the list reads: every text field of the goal (``?object``, ``?to``, ``?from``)."""
    return {k: v for k, v in goal.items() if isinstance(v, str) and k != "type"}


def _rules(pack) -> action_rules.Rules:
    return action_rules.Rules(predicates=dict(pack.table("predicates")), pack=pack)


def values(pack, decl: dict, goal: dict, world, placed) -> list:
    """[(predicate text, True / False / None)] of the kind's ``holds`` list in the belief ``world`` + ``placed``."""
    rules, b, view = _rules(pack), bound(goal), View(world, placed if placed is not None else {})
    return [(t, action_rules.holds(rules, t, b, view)) for t in decl.get("holds") or ()]


def holds(pack, decl: dict, goal: dict, world, placed) -> bool:
    """Does the goal hold in the belief? Every predicate of the list True; one that cannot be told is not holding."""
    return all(v is True for _t, v in values(pack, decl, goal, world, placed))


def after(steps, world, placed, released, apply: Callable) -> tuple:
    """(world, placed) after ``steps``: each step applied by ``apply(step, world)`` (a copy each time, the action model with
    the declared effects); a robot's release action (``released``) empties the hand; an object that leaves the hand is placed
    where the step names, else where the robot was. ``world`` and ``placed`` are not changed."""
    w, out = world, dict(placed or {})
    for st in steps:
        held, at = getattr(w, "holding", None), getattr(w, "arrived", None)
        w = apply(st, w)
        if held and st.get("action") in released:
            w.holding = None
        if held and getattr(w, "holding", None) is None:
            out[held] = st.get("object") or at
    return w, out


def shown(text: str, b: dict) -> str:
    """A predicate of the list with the goal's values in it: ``placed(?object, ?to)`` -> ``placed(bracket, jig)``."""
    neg, name, args = pred_parts(text)
    vals = ", ".join(str(b.get(a[1:], a)) if a.startswith("?") else a for a in args)
    return f"{'not ' if neg else ''}{name}({vals})"


def unmet(pack, decl: dict, goal: dict, belief_after: tuple) -> list:
    """One planner line per predicate of the list that is False in ``belief_after`` = (world, placed) from ``after``."""
    b, which = bound(goal), " ".join(str(x) for x in (goal.get("type"), goal.get("object")) if x)
    return [f"Make {shown(t, b)} true ({which})" for t, v in values(pack, decl, goal, *belief_after) if v is False]


def phrase_entry(pack, kind) -> Optional[str]:
    """The ``phrase`` key of goal kind ``kind`` when ``pack`` declares it without ``rules``; else None."""
    e = pack.entry(TABLE, kind) if pack is not None and isinstance(kind, str) else None
    return e.get("phrase") if isinstance(e, dict) and not e.get("rules") and e.get("phrase") else None


def phrase(pack, key: str, goal: dict, nice: Callable):
    """The read-back phrase of ``goal`` from the phrase ``key``: a ``LocalText`` (the robot's words say it in the user's
    language); its English text is the pack's ``en`` text of the key, or "<kind> the <object>" when it has none or a slot
    of the text is not filled."""
    from robot_agent.core.planning import speech               # the engine's phrase type (imports this package first)
    entry = pack.entry("phrases", key) if pack is not None else None
    entry = entry if isinstance(entry, dict) else {}
    obj = goal.get("object") or ""
    slots, text_slots = {}, {}
    for name in entry.get("slots") or ():
        if name == "object":           # the object as the person named it (F4.8), as the household phrases do
            raw = goal.get("said") or goal.get("name_local") or obj
        else:
            raw = goal.get(PLACE_SLOTS.get(name, name))
        slots[name] = raw if raw is not None else ""
        text_slots[name] = obj if name == "object" else nice(slots[name]) if name in PLACE_SLOTS else slots[name]
    try:
        text = str(entry["en"]).format_map(text_slots)
    except (KeyError, IndexError, ValueError):
        text = f"{goal.get('type')} the {nice(obj)}"
    return speech.LocalText(key, slots, text=text)


__all__ = ["TABLE", "declared", "bound", "values", "holds", "after", "shown", "unmet", "phrase_entry", "phrase"]
