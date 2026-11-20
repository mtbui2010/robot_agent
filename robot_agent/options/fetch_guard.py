"""Option ``fetch_guard`` (default ON): the place an object must end up is never taken for the place where it is now.

What: an ``on`` / ``in`` goal whose object has no source (no ``from``, no ``from_any``, no trusted sighting in the object memory):
(1) ``rank_unknown``: the engine's ranked search gives it a first place to look before planning, never its destination (service ``rank_first``);
(2) ``plan_finding``: a plan that drives to that goal's destination and Finds or Picks the object there gets a gate finding (hook
``on_plan_findings``), so the gate's repair and its one refine ask the planner for another plan;
(3) ``absent_find``: a failed Find whose reason gives no code at all (a look that reports 'not done' and no text) did not see the object: ABSENT
(service ``failure_code``), so the search and then the question run, not a replan of the same plan (a camera that cannot look is caught before the Find as
PERCEPTION_UNAVAILABLE).
Why: with no source the planner looked for the object at its destination, a place nobody said it was; every Find there missed.
Switch off: ``fetch_guard.enabled=0`` (``$ROBOT_AGENT_FETCH_GUARD``); one part: ``fetch_guard.rank_unknown=0`` / ``fetch_guard.plan_finding=0`` / ``fetch_guard.absent_find=0``.
Event: lab flag ``fetch_guard``: ``{part: rank_unknown, object}`` or ``{part: plan_finding, finding}``. Generic: no object, place or word of a language.
"""

from __future__ import annotations

from robot_agent.contract import FailureCode
from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import action_model

SPEC = OptionSpec(
    name="fetch_guard",
    keys=(Key("enabled", "bool", True, aliases=("fetch_guard",)),
          Key("rank_unknown", "bool", True),
          Key("plan_finding", "bool", True),
          Key("absent_find", "bool", True)),
    hooks=("on_plan_findings",),
    services=("rank_first", "failure_code"),
    order=32,
    enabled="enabled",
    doc="An object with no known source is never looked for at its destination: ranked first place before planning, a gate finding otherwise.",
)

_FETCH = ("on", "in")
_LOOKS = ("Find", "Pick")


def unknown_sources(goals, trusted) -> dict:
    """{object: destination} of the ``on`` / ``in`` goals with no ``from``, no ``from_any`` and no trusted sighting (``trusted(obj)``). Pure."""
    return {g["object"]: g["to"] for g in goals if g.get("type") in _FETCH and g.get("object") and g.get("to")
            and not g.get("from") and not g.get("from_any") and not trusted(g["object"])}


def fetch_findings(dest: dict, visits) -> list:
    """The findings of a plan whose first Find / Pick of an object with no source (``dest``: object -> destination) is made at its
    destination reached by a plan MoveTo. ``visits``: (index, action, object, place the plan drove to or None) per step. Pure."""
    out, seen = [], set()
    for i, a, o, at in visits:
        if a not in _LOOKS or o not in dest or o in seen:
            continue
        seen.add(o)
        if at and at == dest[o]:
            out.append(f"step {i + 1} '{a} {o}': {at} is where the {o} must end up, not where it is (its place is unknown): "
                       f"look for the {o} at another place first")
    return out


def visits(world, steps) -> list:
    """(index, action, object, the place the robot stands at by the plan's own MoveTo, or None) per step, on the engine's action model."""
    w, at, out = world.copy(), None, []
    for i, st in enumerate(steps):
        out.append((i, st.get("action", ""), st.get("object") or "", at))
        action_model.apply(st, w, inplace=True)
        if st.get("action") == "MoveTo":
            at = w.arrived
    return out


class FetchGuard(Option):
    def _trusted(self, run):
        return lambda o: bool(self.svc.remembered(run, o))

    def rank_first(self, run, obj):
        """Ask the engine's ranking before planning for ``obj`` (no source, no trusted sighting): True, else None (the engine's own order)."""
        if not self.cfg.get("rank_unknown") or self.svc.remembered(run, obj):
            return None
        self.svc.lab("fetch_guard", part="rank_unknown", object=obj)
        return True

    def failure_code(self, run, action, code):
        """Service: the code of a failed step. A Find with the code UNKNOWN (no reason the engine knows) is ABSENT; else None (the engine's code)."""
        if not self.cfg.get("absent_find") or action != "Find" or code is not FailureCode.UNKNOWN:
            return None
        self.svc.lab("fetch_guard", part="absent_find")
        return FailureCode.ABSENT

    def on_plan_findings(self, bad, run, steps):
        if not self.cfg.get("plan_finding") or not steps:
            return None
        dest = unknown_sources(getattr(run, "goals", None) or [], self._trusted(run))
        new = fetch_findings(dest, visits(run.world, steps)) if dest else []
        if not new:
            return None
        noted = run.__dict__.setdefault("opt", {}).setdefault("fetch_guard", set())
        for f in new:
            if f not in noted:
                noted.add(f)
                self.svc.lab("fetch_guard", part="plan_finding", finding=f)
        return list(bad) + new


register(SPEC, FetchGuard)
