"""Option ``plan_release`` (default off): the held object's own release goes before the Close of the place its ``in`` goal names.

What: the plan is replayed on the belief with one simulated hand. At a Close of the place C that the held object X's ``in`` goal names, when X's
NEXT release (the first release step before another Pick) is not at C and the plan does not pick X again later (a staging put-down), the pack's
``achieves: in`` skill goes before the Close (after a MoveTo C when the robot is elsewhere). The release the splice leaves with an empty hand is
repaired too: the one goal object of its place that no step picks gets ``Find / Pick`` before it (before the MoveTo that leads there), so that
goal keeps its delivery; the one fetch of it the plan left AFTER the release, with nothing released after it, is brought forward the same way; when
every goal object of that place is picked and a later step releases at that place again, the empty release is removed; else it stays (no guess).
Why (failure class plan_missing_step): the planner writes "Pick X, Close C, MoveTo T, Place T" for "X in C, Y on T". ``plan_complete`` splices only
when NO later release exists (a release of any object blocks it), so the gate kept a plan its operators could not repair. A Place into the goal
container is NOT swapped to the put-in skill here: the gate's ``op_action`` / ``op_putin`` do that, and ``op_putin`` also opens a closed container.
How to switch it off: key ``enabled`` (legacy ``plan_release``), off by default. With no labelled ``planner.options.plan_release.needs`` (no rule
that ``achieves: in``), no ``pick`` / ``close`` role, or a robot with two hands or more (a release does not say which hand empties), the plan is
returned unchanged (fail open).

Pack data ``planner.options.plan_release.needs``::

    {"rules": {<Action>: {"achieves": "in" | "on" | "hand"}}, "source": "<label>"}      # or "assumed": true instead of source

A declared action, the ``put_down`` role and the robot's hand-over action are releases in the replay; no skill name is in the code. A goal's object
is the plan's object in the same or other words (``exec_integrity.lookup_object``: 'lid' / 'jar lid'; a plural is not matched). Roles read:
``move``, ``find``, ``pick``, ``close``, ``put_down`` (no ``find``: no fetch). Hook ``on_plan`` at order 87, after ``precond_repair`` (85) and
``plan_complete`` (86): a Close they already completed holds nothing and is left alone, so no step is added twice. Lab flag ``plan_release``:
``{object, place, fix}`` (fix: ``splice``, ``pick_step``, ``removed``), once per distinct edit in a run (the gate scores many candidates).
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data
from robot_agent.core.planning.exec_integrity import lookup_object

KINDS = ("in", "on", "hand")
FETCHED = ("in", "on")


def rules_of(needs) -> dict:
    """``{action: kind}`` from the declared data; {} when it has no label (source or assumed) or is malformed."""
    if not pack_data.labelled(needs):
        return {}
    rules = needs.get("rules")
    if not isinstance(rules, dict):
        return {}
    return {a: r["achieves"] for a, r in rules.items() if isinstance(a, str) and isinstance(r, dict) and r.get("achieves") in KINDS}


def _key_for(env, place):
    """The ENV key a goal's place names: the key itself, or the one key with that head; None when none or several."""
    if place in env:
        return place
    hits = [k for k in env if k.split("@")[0].strip() == place]
    return hits[0] if len(hits) == 1 else None


SPEC = OptionSpec(
    name="plan_release",
    keys=(Key("enabled", "bool", False, aliases=("plan_release",)), Key("needs", "dict", {}, sources=("manifest",))),
    hooks=("on_start", "on_plan"),
    order=87,
    enabled="enabled",
    doc="The held object's own release goes before the Close of its in-goal place even when the plan releases other objects later; the release it empties gets its fetch.",
)


class _Pass:
    """What one pass reads: the declared put-in skill and releases, the roles, the ENV places, the goals, the belief."""

    def __init__(self, svc, rules, run):
        self.role = {r: svc.role(r) for r in ("move", "find", "pick", "close", "put_down")}
        self.put_in = next((a for a, k in rules.items() if k == "in"), None)
        self.releases = (set(rules) | {self.role["put_down"], svc.hand_action()}) - {None}
        self.env = getattr(run.places, "env", {}) or {}
        self.goals = [g for g in run.goals or [] if isinstance(g, dict)]
        self.world = run.world

    def key(self, place):
        return _key_for(self.env, place) if isinstance(place, str) and place else None

    def in_goal(self, obj):
        """The ENV key of the place the ``in`` goal of ``obj`` names (the goal's object in the same or other words), or None."""
        g = lookup_object({g.get("object"): g for g in self.goals if g.get("type") == "in" and g.get("object")}, obj)
        return self.key(g.get("to")) if g else None

    def step(self, st, at, hold):
        """(at, hold) after ``st`` on the one-hand replay."""
        a, o = st.get("action"), st.get("object")
        if self.role["move"] and a == self.role["move"]:
            return o, hold
        if a == self.role["pick"]:
            return at, o
        return (at, None) if a in self.releases else (at, hold)

    def next_release(self, plan, start):
        """Index of the first release at or after ``start`` that comes before any Pick, or None."""
        for j in range(start, len(plan)):
            a = plan[j].get("action")
            if a == self.role["pick"]:
                return None
            if a in self.releases:
                return j
        return None


class PlanRelease(Option):
    def on_start(self, run) -> None:
        self._told = set()

    def on_plan(self, steps, run):
        """``steps`` with the held object's release before the Close of its goal place; None when nothing changes or nothing is declared."""
        rules = rules_of(pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "needs"))
        if not steps or "in" not in rules.values() or self._hands() != 1:
            return None
        px = _Pass(self.svc, rules, run)
        if not (px.role["pick"] and px.role["close"]):
            return None
        plan = list(steps)
        for _ in range(len(steps)):
            hit = self._close_hit(plan, px)
            if hit is None:
                break
            plan = self._splice(plan, hit, px)
        return plan if plan != list(steps) else None

    def _hands(self) -> int:
        robot = self.svc.robot
        caps = robot.model_capabilities() if hasattr(robot, "model_capabilities") else None
        n = getattr(caps, "hands", 1)
        return len(n) if isinstance(n, (list, tuple)) else int(n or 1)

    def _close_hit(self, plan, px):
        """(index, object, place, at) of the first Close of the held object's ``in`` place whose next release is elsewhere; None."""
        at, hold = px.world.arrived, px.world.holding
        for i, st in enumerate(plan):
            key = px.key(st.get("object")) if st.get("action") == px.role["close"] and hold else None
            if key and px.in_goal(hold) == key and self._loose(plan, i + 1, hold, key, px):
                return i, hold, key, at
            at, hold = px.step(st, at, hold)
        return None

    def _loose(self, plan, start, obj, key, px) -> bool:
        """True when ``obj``'s next release is not at ``key`` and the plan does not pick ``obj`` again later (staging)."""
        if any(s.get("action") == px.role["pick"] and s.get("object") == obj for s in plan[start:]):
            return False
        j = px.next_release(plan, start)
        return j is None or px.key(plan[j].get("object")) != key

    def _splice(self, plan, hit, px) -> list:
        """The put-in before the Close, then the repair of the release it left with an empty hand."""
        i, obj, key, at = hit
        move = px.role["move"]
        add = ([{"action": move, "object": key}] if move and at != key else []) + [{"action": px.put_in, "object": key}]
        new = plan[:i] + add + plan[i:]
        j = px.next_release(new, i + len(add) + 1)
        new, fix = self._orphan(new, j, px) if j is not None else (new, "splice")
        self._tell(obj, key, fix)
        return new

    def _orphan(self, plan, j, px):
        """(plan, fix) for the release at ``j`` the splice emptied: fetch the one unpicked goal object of its place, bring forward the one
        fetch of it the plan left after the release with nothing released after it, or drop the release when the place is served later."""
        q = px.key(plan[j].get("object"))
        picked = {s.get("object"): True for s in plan if s.get("action") == px.role["pick"] and s.get("object")}
        gs = [g for g in px.goals if g.get("type") in FETCHED and not g.get("hand") and g.get("object") and q and px.key(g.get("to")) == q]
        free = [g["object"] for g in gs if not lookup_object(picked, g["object"])]
        k = j - 1 if j and plan[j - 1].get("action") == px.role["move"] and px.key(plan[j - 1].get("object")) == q else j
        if len(free) == 1 and px.role["find"]:
            fetch = [{"action": px.role["find"], "object": free[0]}, {"action": px.role["pick"], "object": free[0]}]
            return plan[:k] + fetch + plan[k:], "pick_step"
        late = self._late_fetch(plan, j, gs, px) if len(gs) == 1 and not free else None
        if late:
            a, p = late
            rest = plan[:a] + plan[p + 1:]
            return rest[:k] + plan[a:p + 1] + rest[k:], "pick_step"
        if gs and not free and any(s.get("action") in px.releases and px.key(s.get("object")) == q for s in plan[j + 1:]):
            return plan[:j] + plan[j + 1:], "removed"
        return plan, "splice"

    def _late_fetch(self, plan, j, gs, px):
        """(start, pick index) of the only Pick of the place's one goal object when it comes after ``j`` and nothing is released after it
        (the plan ends holding it); the start takes the Find of the same object just before. None otherwise."""
        one = {gs[0]["object"]: True}
        picks = [i for i, s in enumerate(plan) if s.get("action") == px.role["pick"] and lookup_object(one, s.get("object"))]
        if len(picks) != 1 or picks[0] <= j or px.next_release(plan, picks[0] + 1) is not None:
            return None
        p = picks[0]
        a = p - 1 if plan[p - 1].get("action") == px.role["find"] and plan[p - 1].get("object") == plan[p].get("object") else p
        return a, p

    def _tell(self, obj, key, fix) -> None:
        """One lab event per distinct edit in a run (the gate scores many candidate plans through this hook)."""
        told = self.__dict__.setdefault("_told", set())
        if (obj, key, fix) not in told:
            told.add((obj, key, fix))
            self.svc.lab(SPEC.name, object=obj, place=key, fix=fix)


register(SPEC, PlanRelease)
