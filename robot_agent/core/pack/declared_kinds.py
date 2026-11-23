"""When is a goal of a declared kind with no ``rules`` callable done (Step S29): its ``holds`` list and its ``phrase`` key.

What: a schema-2 pack may declare a goal kind with no ``rules`` (a registered ``goal.rules`` object) but with ``holds``: a list
of predicates over the goal's slots (``"placed(?object, ?to)"``, ``"fastened(?object)"``). For such a kind:
- ``holds``: every predicate of the list is True in the belief (the world, its fact store and the run's ``placed`` map);
  "cannot tell" (None) is NOT holding.
- ``refuted`` (reached by a plan): the list on a copy of the belief after the steps (``after``: the action model with the
  run's declared effects); here "cannot tell" is "not refuted", because a predicate that reads an observation cannot be
  decided from a plan: only the end check decides.
- ``unmet``: one planner line per predicate of the list that the steps refute.
- ``phrase``: the read-back phrase from the kind's ``phrase`` key (the pack's ``phrases`` table; slots ``object``, ``place`` =
  the goal's ``to``, ``source`` = its ``from``, any other slot = the goal field of that name).
Why: a robot pack with a kind of its own (the PEX ``install``) had no way to say when it is done; the core read such a goal
as vacuously done (docs/proposals/generalize_core_design.md, "Goal kinds", "What done means, per case").
How: pure functions (data in, decision out); robot_agent/core/pack/goal_semantics.py asks them for a kind that has no rules
object. A predicate is read with core/pack/action_rules.py ``holds`` (the one reading of a declared predicate); one whose
``scopes`` do not include ``goal`` cannot be told. The load already refuses a kind with neither ``rules`` nor ``holds``, and
``unknown_is: failure`` with no ``observe`` (robot_agent/contract/pack.py).
Off: a kind with ``rules`` never comes here and no shipped pack declares a kind without ``rules``, so a robot that declares
no such kind runs as before (a kind no pack declares stays vacuously done). Remove the kind's ``holds`` to switch it off
(the load then asks for ``rules``).
"""

from __future__ import annotations

from typing import Callable, Optional

from robot_agent.contract.pack import pred_parts

from . import action_rules
from .predicates import View

TABLE = "goal_kinds"
SCOPE = "goal"                                        # the predicate scope a holds list may use
PLACE_SLOTS = {"place": "to", "source": "from"}       # a phrase slot -> the goal field it reads (any other slot: same name)


def declared(pack, kind) -> Optional[dict]:
    """The entry of goal kind ``kind`` when ``pack`` declares it with a ``holds`` list and no ``rules``; else None."""
    e = pack.entry(TABLE, kind) if pack is not None and isinstance(kind, str) else None
    return e if isinstance(e, dict) and not e.get("rules") and e.get("holds") else None


def bound(goal: dict) -> dict:
    """{variable -> value} a predicate of the list reads: every text field of the goal (``?object``, ``?to``, ``?from``)."""
    return {k: v for k, v in goal.items() if isinstance(v, str) and k != "type"}


def _value(pack, text: str, b: dict, view) -> Optional[bool]:
    """One predicate of the list in ``view``: True / False, None when it cannot be told (not in the goal scope, not
    declared, an unbound slot, an evaluator that cannot tell)."""
    decl = pack.table("predicates").get(pred_parts(text)[1])
    if not isinstance(decl, dict) or SCOPE not in (decl.get("scopes") or ()):
        return None
    return action_rules.holds(action_rules.Rules(predicates=dict(pack.table("predicates")), pack=pack), text, b, view)


def values(pack, decl: dict, goal: dict, world, placed) -> list:
    """[(predicate text, True / False / None)] of the kind's ``holds`` list in the belief ``world`` + ``placed``."""
    b, view = bound(goal), View(world, placed if placed is not None else {})
    return [(t, _value(pack, t, b, view)) for t in decl.get("holds") or ()]


def holds(pack, decl: dict, goal: dict, world, placed) -> bool:
    """Does the goal hold in the belief? Every predicate of the list True; one that cannot be told is not holding."""
    return all(v is True for _t, v in values(pack, decl, goal, world, placed))


def after(steps, world, released, apply: Callable) -> tuple:
    """(world, placed) after ``steps`` from ``world``: each step applied by ``apply(step, world, released)`` (a copy each
    time: the action model with the declared effects; ``released``: the robot's release actions). ``placed`` starts empty,
    as the standard kinds' simulation does (the steps themselves must put an object); an object that leaves the hand is
    placed where the step names, else where the robot was. ``world`` is not changed."""
    w, placed = world, {}
    for st in steps:
        held, at = getattr(w, "holding", None), getattr(w, "arrived", None)
        w = apply(st, w, released)
        if held and getattr(w, "holding", None) is None:
            placed[held] = st.get("object") or at
    return w, placed


def shown(text: str, b: dict) -> str:
    """A predicate of the list with the goal's values in it: ``placed(?object, ?to)`` -> ``placed(bracket, jig)``."""
    neg, name, args = pred_parts(text)
    vals = ", ".join(str(b.get(a[1:], a)) if a.startswith("?") else a for a in args)
    return f"{'not ' if neg else ''}{name}({vals})"


def unmet(pack, decl: dict, goal: dict, belief_after: tuple) -> list:
    """One planner line per predicate of the list that is False in ``belief_after`` = (world, placed) from ``after``."""
    b, which = bound(goal), " ".join(str(x) for x in (goal.get("type"), goal.get("object")) if x)
    return [f"Make {shown(t, b)} true ({which})" for t, v in values(pack, decl, goal, *belief_after) if v is False]


def phrase_key(pack, kind) -> Optional[str]:
    """The ``phrase`` key of goal kind ``kind`` when ``pack`` declares it without ``rules``; else None."""
    e = pack.entry(TABLE, kind) if pack is not None and isinstance(kind, str) else None
    return e.get("phrase") if isinstance(e, dict) and not e.get("rules") and e.get("phrase") else None


def _slots(entry: dict, goal: dict, nice: Callable) -> tuple:
    """(the phrase's slots as the person named them, the slots of its English text)."""
    obj, slots, text_slots = goal.get("object") or "", {}, {}
    for name in entry.get("slots") or ():
        if name == "object":           # the object as the person named it (F4.8), as the household phrases do
            raw = goal.get("said") or goal.get("name_local") or obj
        else:
            raw = goal.get(PLACE_SLOTS.get(name, name))
        slots[name] = raw if raw is not None else ""
        text_slots[name] = obj if name == "object" else nice(slots[name]) if name in PLACE_SLOTS else slots[name]
    return slots, text_slots


def phrase(pack, key: str, goal: dict, nice: Callable):
    """The read-back phrase of ``goal`` from the phrase ``key``: a ``LocalText`` (the robot's words say it in the user's
    language); its English text is the pack's ``en`` text of the key, or "<kind> the <object>" when it has none or a slot
    of the text is not filled."""
    from robot_agent.core.planning import speech               # the engine's phrase type (it imports this package first)
    entry = pack.entry("phrases", key) if pack is not None else None
    entry = entry if isinstance(entry, dict) else {}
    slots, text_slots = _slots(entry, goal, nice)
    try:
        text = str(entry["en"]).format_map(text_slots)
    except (KeyError, IndexError, ValueError):
        text = f"{goal.get('type')} the {nice(goal.get('object') or '')}"
    return speech.LocalText(key, slots, text=text)


__all__ = ["TABLE", "SCOPE", "declared", "bound", "values", "holds", "after", "shown", "unmet", "phrase_key", "phrase"]
