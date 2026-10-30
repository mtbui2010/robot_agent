"""Steps of a plan the action model proves do nothing, removed before the gate checks it (no LLM call).

A 7-8B planner repeats itself: "Find plate, Pick plate, Find plate, Pick plate" (the second pair with
the plate already in the hand), the whole task again after its goals are reached, a "MoveTo kitchen"
(a room, not a place) on the way to "MoveTo drawer@kitchen". The gate reported each of them as a
critical finding ("already done (the plate is in the hand); delete this step", "the goals are done
after step k; remove the steps after it"), the planner's sub-goal repair was asked to delete them, the
model did not, and the run ended "I could not make a plan that passes my checks" (T2 S02, M05, M07,
H02, M04 with qwen3-vl / qwen2.5). SAGE's own flatten already drops a step whose effect is already true
(MoveTo where the robot is, Find of what was just found); this goes on from there, planner-neutral:

* ``Find X`` / ``Pick X`` while X is in the hand;
* ``MoveTo P`` where the robot already is; ``Open C`` of an open C; ``Close C`` of a closed C;
* a ``MoveTo`` to a room (an ungroundable ``@room`` waypoint) right before another ``MoveTo``;
* every step after the first point where the goals are reached (``reaches``).

Each removal is proven on the belief by the one action model (``action_model.check`` / ``apply``); a
step with any other finding is kept for the gate and the planner. Nothing else is changed or added.
"""

from __future__ import annotations

from typing import Callable

from . import action_model

_NOOP = {"find_held", "already_there", "already_open", "close_not_opened"}
_PICK_HELD = {"already_holding", "no_find_before_pick", "pick_not_found"}


def _waypoint(st: dict, nxt) -> bool:
    """A MoveTo to a room (``@room``, not a place) just before another MoveTo."""
    o = str(st.get("object") or "")
    return (st.get("action") == "MoveTo" and (o.startswith("@") or not st.get("grounded", True))
            and nxt is not None and nxt.get("action") == "MoveTo")


def _noop(st: dict, w, ctx: dict) -> bool:
    codes = {v.code for v in action_model.check(st, w, **ctx)}
    if st.get("action") == "Pick":
        return bool(w.holding) and w.holding == st.get("object") and codes <= _PICK_HELD
    return bool(codes) and codes <= _NOOP and (st.get("action") != "Open" or "already_open" in codes)


def tidy(steps: list, world, goals: list, reaches: Callable, ctx: dict) -> tuple:
    """(steps without the provable no-ops, [notes]). ``world``: the belief the plan starts from (not
    changed); ``reaches(steps, world, goals)``: do these steps reach the goals; ``ctx``: the
    ``action_model.check`` keywords (capabilities, actions, openable, containers)."""
    w, out, notes = world.copy(), [], []
    for i, st in enumerate(steps):
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if _waypoint(st, nxt) or _noop(st, w, ctx):
            notes.append(f"step {i + 1} '{st.get('action')} {st.get('object')}' does nothing here: removed")
            continue
        out.append(st)
        action_model.apply(st, w, inplace=True)
    if goals and out:
        k = next((k for k in range(1, len(out)) if reaches(out[:k], world, goals)), None)
        if k is not None:
            notes.append(f"the goals are reached after {k} steps: the {len(out) - k} steps after them removed")
            out = out[:k]
    return out, notes


__all__ = ["tidy"]
