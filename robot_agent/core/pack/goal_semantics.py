"""The goal-reach rules, chosen by kind: the loop's questions about its goals, answered by each goal kind's rules object.

What: ``reaches`` (do these steps reach every goal), ``missing`` (one text per goal the steps leave unmet, for the planner)
and ``holds`` (does every goal hold in the belief) ask, for each goal, the rules object that its kind names in the robot's
pack (``goal_kinds.<kind>.rules``: a ``goal.rules`` name of the pack registry); ``phrase`` (Step S08) asks the same object
for the read-back phrase of one goal. ``simulate``, ``reached_by`` and
``releases`` are what the loop's seams (``AssistLoop._simulate_goals``, ``_on_reached``, ``_releases``) delegate to.
Why: the loop names no goal kind and no action (docs/proposals/generalize_core_design.md, "Goal kinds", Step S05); the
standard pack (``robot_agent/packs/household``) holds the household rules, one object per kind.
How: a goal whose kind has no rules object (no pack of the chain declares the kind, or the name is not registered) is
reached and holds vacuously and adds no text: the result of the code before Step S05. A rules object gets a ``GoalCtx``:
the loop (or None), whether the goal is the last one, the steps' simulation (computed ONCE per question through the loop's
seam, so a test that patches the seam still reaches every kind) and ``reached_by`` (the loop's other seam).
Off: a robot whose manifest has no ``pack:`` section gets the standard pack, which reproduces the old if-chains
(tests/test_goal_semantics_diff.py); a schema-2 pack that removes a kind, or names other rules for it, changes only its own
goals. There is no switch beyond the pack data: an empty ``goal_kinds`` section makes every goal vacuous.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, NamedTuple, Optional

from .loader import Pack, default_pack

NAMESPACE = "goal.rules"


class Belief(NamedTuple):
    """What ``holds`` reads: the world state, object -> where the run put it, the steps done, where the run started."""
    world: Any
    placed: dict
    done: Any = ()
    at: Optional[str] = None


class Seams(NamedTuple):
    """The loop's entry points a rules object goes through (class attributes of the loop, so that a test can patch them)."""
    simulate: Callable        # (steps, world) -> the pack's simulation of the steps (opaque here)
    reached_by: Callable      # (goal, got) -> bool: is the goal reached by that end position


class GoalCtx:
    """What a rules object may ask about one goal: ``loop`` (None = a check without a run), ``last`` (the goal is the last
    of its list), ``sim`` (the steps' simulation; None for ``holds``), ``reached_by(goal, got)``, ``handed`` (the objects
    the run handed over)."""
    __slots__ = ("loop", "sim", "_goal", "_goals", "_seams")

    def __init__(self, loop, goal, goals, sim=None, seams: Optional[Seams] = None):
        self.loop, self.sim, self._goal, self._goals, self._seams = loop, sim, goal, goals, seams

    @property
    def last(self) -> bool:
        return self._goal is self._goals[-1]

    def reached_by(self, goal, got) -> bool:
        return self._seams.reached_by(goal, got)

    @property
    def handed(self) -> set:
        return getattr(self.loop, "_handed", set())


@functools.lru_cache(maxsize=1)
def _default() -> Pack:
    return default_pack()


def pack_of(loop) -> Pack:
    """The robot's resolved pack (``RobotView.pack``); the standard pack for a loop with no robot view, or when the pack of
    a schema-1 manifest cannot be resolved now (it is read lazily; its goal kinds are the standard ones anyway)."""
    robot = getattr(loop, "robot", None) if loop is not None else None
    try:
        pack = getattr(robot, "pack", None) if robot is not None else None
    except Exception:
        pack = None
    return pack if isinstance(pack, Pack) else _default()


def rules_of(pack: Pack, kind) -> Any:
    """The rules object of goal kind ``kind`` in ``pack``; None when the pack does not declare the kind or its name is not
    registered (the caller then treats the goal as vacuous)."""
    entry = pack.entry("goal_kinds", kind) if isinstance(kind, str) else None
    return pack.named(NAMESPACE, entry.get("rules")) if isinstance(entry, dict) else None


def shared(pack: Pack, method: str) -> Optional[Callable]:
    """The method ``method`` of the first rules object of ``pack`` (in kind order) that has it: a helper all kinds of a pack
    share (the standard pack's ``simulate`` and ``reached_by``). None when no rules object has one."""
    for kind in pack.table("goal_kinds"):
        fn = getattr(rules_of(pack, kind), method, None)
        if callable(fn):
            return fn
    return None


def releases(loop) -> set:
    """The robot's own actions that release the held object (``RobotView.release_actions``: a hand-over); empty for None."""
    robot = getattr(loop, "robot", None) if loop is not None else None
    try:
        return set(robot.release_actions()) if robot is not None else set()
    except Exception:
        return set()


def simulate(loop, steps, world, released: set):
    """The pack's simulation of ``steps`` from ``world`` (``released``: the robot's release actions); None when no rules
    object of the pack simulates."""
    fn = shared(pack_of(loop), "simulate")
    return fn(steps, world, released) if fn is not None else None


def reached_by(loop, goal, got) -> bool:
    """Is ``goal`` reached by the end position ``got``? Asked of the goal kind's rules object, else of the pack's shared
    one; False when the pack has none."""
    pack = pack_of(loop)
    fn = getattr(rules_of(pack, goal.get("type")), "reached_by", None) or shared(pack, "reached_by")
    return fn(goal, got, getattr(loop, "_env_places", None)) if fn is not None else False


def reaches(loop, steps, world, goals, seams: Seams) -> bool:
    """Do ``steps`` from ``world`` reach every goal? A goal of a kind with no rules is reached; no goal and no step: False."""
    pack, sim = pack_of(loop), seams.simulate(steps, world)
    for g in goals:
        rules = rules_of(pack, g["type"])
        if rules is not None and not rules.reached(g, steps, world, GoalCtx(loop, g, goals, sim, seams)):
            return False
    return bool(goals) or bool(steps)


def missing(loop, steps, world, goals, seams: Seams) -> list:
    """One text per goal that ``steps`` from ``world`` leave unmet, in goal order (the planner reads them)."""
    pack, sim, out = pack_of(loop), seams.simulate(steps, world), []
    for g in goals:
        rules = rules_of(pack, g["type"])
        text = rules.missing(g, steps, world, GoalCtx(loop, g, goals, sim, seams)) if rules is not None else None
        if text is not None:
            out.append(text)
    return out


def phrase(goal, places, nice, pack: Optional[Pack] = None):
    """The read-back phrase of ``goal``: the ``phrase(goal, places, nice)`` of its kind's rules object (a ``LocalText``, or a
    plain text). A kind with no rules object, or a rules object with no ``phrase``, reads "<kind> the <object>" in plain
    text, as an unknown kind always did. ``pack``: the robot's pack; None = the standard pack (the read-back phrase is
    asked through static methods of the loop, which have no robot)."""
    kind = goal["type"]
    fn = getattr(rules_of(pack if pack is not None else _default(), kind), "phrase", None)
    return fn(goal, places, nice) if callable(fn) else f"{kind} the {nice(goal.get('object') or '')}"


def holds(loop, goals, belief: Belief) -> bool:
    """Does every goal hold in ``belief``? A goal of a kind with no rules holds."""
    pack = pack_of(loop)
    for g in goals:
        rules = rules_of(pack, g["type"])
        if rules is not None and not rules.holds(g, belief, GoalCtx(loop, g, goals)):
            return False
    return True


__all__ = ["Belief", "Seams", "GoalCtx", "pack_of", "rules_of", "shared", "releases", "simulate", "reached_by", "reaches",
           "missing", "holds", "phrase"]
