"""Option ``plan_complete`` (default off): a plan that stops short of a step its goal needs is completed, and the completing step goes where it can hold.

Three plan classes the reader's plans fall into, all repaired from DECLARED pack data (no skill name is in the code):

* A plan picks an object and then closes or opens the place the goal puts it in, with no release between ("pick X, close C" for the goal
  "X in C"). The skill the pack declares as ``achieves: in`` is spliced in before the Close (after the Open), after a MoveTo the place when the
  robot is elsewhere. A release of the object later in the plan means nothing is missing.
* The end-of-plan steps another repair appended (a delivery after the final go-home / end-state steps, so the home goal no longer holds and a hand
  goal is handed over at the dock) are moved in front of the final go-home block (the steps ``planner.end_state`` names: a MoveTo one of its places, its
  action). A hand-over of a hand goal whose rule declares ``near`` gets a MoveTo the person first when the robot is not there.
* A speech step whose listener is elsewhere: the rule of the speech role declares ``near``; the step goes after a MoveTo the goal's listener, and a
  speech step that names the listener instead of the message is rebuilt with the goal's message (the one say goal, or the goal whose listener it names).

Pack data ``planner.options.plan_complete.needs``::

    {"rules": {<Action>: {"near": bool, "achieves": "in" | "hand"}}, "source": "<label>"}      # or "assumed": true instead of source

Roles read (``planner.action_roles``): ``move``, ``open``, ``close``, ``say``, ``pick``, ``put_down``; a role not declared = that repair is skipped. No rules,
no label, no goal of the kind, a plan that already meets the rules = the plan is returned unchanged (fail open). Hook ``on_plan``, after ``precond_repair``.
Keys: ``enabled`` (legacy ``plan_complete``), ``needs`` (dict, from the manifest). Lab flag ``plan_complete``: ``{action, need, index}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key

KINDS = ("in", "hand")


def rules_of(needs) -> dict:
    """``{action: {"near": bool, "achieves": kind}}`` from the declared data; {} when it has no label (source or assumed) or is malformed."""
    if not isinstance(needs, dict) or not (isinstance(needs.get("source"), str) and needs["source"].strip() or needs.get("assumed") is True):
        return {}
    rules = needs.get("rules")
    if not isinstance(rules, dict):
        return {}
    out = {}
    for a, r in rules.items():
        if isinstance(a, str) and isinstance(r, dict):
            out[a] = {"near": r.get("near") is True, **({"achieves": r["achieves"]} if r.get("achieves") in KINDS else {})}
    return out


def _end_state(planner) -> tuple:
    """(places, action) of the pack's ``planner.end_state``; ((), None) when it declares none."""
    v = (planner or {}).get("end_state") if isinstance(planner, dict) else None
    if not isinstance(v, dict) or not isinstance(v.get("action"), str) or not isinstance(v.get("places"), (list, tuple)):
        return (), None
    return tuple(p.strip() for p in v["places"] if isinstance(p, str) and p.strip()), v["action"].strip() or None


def _key_for(env, place):
    """The ENV key a goal's place names: the key itself, or the one key with that head; None when none or several."""
    if place in env:
        return place
    hits = [k for k in env if k.split("@")[0].strip() == place]
    return hits[0] if len(hits) == 1 else None


SPEC = OptionSpec(
    name="plan_complete",
    keys=(Key("enabled", "bool", False, aliases=("plan_complete",)), Key("needs", "dict", {}, sources=("manifest",))),
    hooks=("on_plan",),
    order=86,
    enabled="enabled",
    doc="A plan that stops short of the step its goal needs (a release before a Close, a delivery after the way home, a speech from across the room) is completed from the pack's declared rules.",
)


class _Ctx:
    """What one repair pass reads: the declared rules, the roles, the world's ENV places and the goals."""

    def __init__(self, opt, rules, run):
        svc = opt.svc
        self.rules, self.run = rules, run
        self.role = {r: svc.role(r) for r in ("move", "open", "close", "say", "pick", "put_down")}
        self.env = getattr(run.places, "env", {}) or {}
        self.goals = [g for g in run.goals or [] if isinstance(g, dict)]
        self.by_kind = {r["achieves"]: a for a, r in rules.items() if r.get("achieves")}
        self.releases = set(self.by_kind.values()) | ({self.role["put_down"]} - {None})
        self.end_places, self.end_action = _end_state(getattr(svc.robot.manifest, "planner", None))

    def is_home(self, st) -> bool:
        """Is this step part of the final go-home block (the end-state action, a MoveTo one of the end-state places)?"""
        a, o = st.get("action"), str(st.get("object") or "")
        if self.end_action and a == self.end_action:
            return True
        return bool(self.role["move"] and a == self.role["move"] and self.end_places and (o in self.end_places or o.split("@")[0].strip() in self.end_places))


class PlanComplete(Option):
    def on_plan(self, steps, run):
        """``steps`` completed; None when nothing is missing or nothing is declared."""
        rules = rules_of(self.cfg.get("needs") or resolve_key(SPEC, SPEC.key("needs"), {}, {}, getattr(self.svc.robot.manifest, "planner", None)))
        if not rules or not steps:
            return None
        cx = _Ctx(self, rules, run)
        out = self._walk(self._before_home(list(steps), cx, run), cx, run)
        return out if out != list(steps) else None

    def _before_home(self, plan, cx, run) -> list:
        """The steps after the last go-home step (moves and releases only) go in front of the go-home block."""
        idx = [i for i, s in enumerate(plan) if cx.is_home(s)]
        if not idx or idx[-1] == len(plan) - 1:
            return plan
        j = idx[-1]
        after = plan[j + 1:]
        if not all(s.get("action") in cx.releases or s.get("action") == cx.role["move"] for s in after):
            return plan
        s = j
        while s > 0 and cx.is_home(plan[s - 1]):
            s -= 1
        at = next((p.get("object") for p in reversed(plan[:s]) if p.get("action") == cx.role["move"]), run.world.arrived)
        while after and after[0].get("action") == cx.role["move"] and after[0].get("object") == at:
            after = after[1:]
        self.svc.lab(SPEC.name, action=after[0].get("action") if after else None, need="before_home", index=s)
        return plan[:s] + after + plan[s:j + 1]

    def _walk(self, plan, cx, run) -> list:
        """Replay the plan on the belief and splice the missing release, approach and message."""
        at, hold, out = run.world.arrived, run.world.holding, []
        for i, st in enumerate(plan):
            a, o = st.get("action"), st.get("object")
            before, after = self._missing(st, at, hold, plan[i + 1:], cx), []
            if a in (cx.role["open"], cx.role["close"]) and hold and not before:
                before, after = self._release(st, at, hold, plan[i + 1:], cx)
            for s in before + [st] + after:
                at, hold = self._apply(s, at, hold, cx)
            out.extend(before + [dict(st, **self._rebuilt(st, cx))] + after)
            if before or after:
                self.svc.lab(SPEC.name, action=(before + after)[0].get("action"), need="walk", index=i)
        return out

    def _apply(self, st, at, hold, cx):
        a, o = st.get("action"), st.get("object")
        if a == cx.role["move"]:
            return o, hold
        if cx.role["pick"] and a == cx.role["pick"]:
            return at, o
        if a in cx.releases:
            return at, None
        return at, hold

    def _goal_of(self, hold, kinds, cx):
        return next((g for g in cx.goals if g.get("object") == hold and g.get("type") in kinds), None)

    def _release(self, st, at, hold, later, cx):
        """(before, after) for an Open / Close of the place the held object's ``in`` goal names, when nothing later releases it."""
        act = cx.by_kind.get("in")
        g = self._goal_of(hold, ("in",), cx)
        key = _key_for(cx.env, g.get("to")) if g else None
        if not act or not key or key != _key_for(cx.env, st.get("object")) or any(s.get("action") in cx.releases for s in later):
            return [], []
        add = ([{"action": cx.role["move"], "object": key}] if cx.role["move"] and at != key else []) + [{"action": act, "object": key}]
        return (add, []) if st.get("action") == cx.role["close"] else ([], add)

    def _missing(self, st, at, hold, later, cx) -> list:
        """The MoveTo before a hand-over (to the goal's person) or a speech (to the goal's listener) the robot is not next to."""
        rule, move = cx.rules.get(st.get("action"), {}), cx.role["move"]
        if not move or not rule.get("near"):
            return []
        if rule.get("achieves") == "hand":
            g = self._goal_of(hold, ("on",), cx)
            key = _key_for(cx.env, g.get("to")) if g and g.get("hand") else None
        elif cx.role["say"] and st.get("action") == cx.role["say"]:
            g = self._say_goal(st, cx)
            key = _key_for(cx.env, g.get("to") or g.get("person")) if g else None
        else:
            return []
        return [{"action": move, "object": key}] if key and at != key else []

    def _say_goal(self, st, cx):
        """The say goal this speech step belongs to: the one whose message it says or whose listener it names; the only one when it names a place."""
        says = [g for g in cx.goals if g.get("type") == "say"]
        o = str(st.get("object") or "").strip().lower()
        hits = [g for g in says if str(g.get("object") or "").strip().lower() == o or _key_for(cx.env, g.get("to") or g.get("person")) == _key_for(cx.env, st.get("object"))]
        return hits[0] if len(hits) == 1 else (says[0] if len(says) == 1 and st.get("object") in cx.env else None)

    def _rebuilt(self, st, cx) -> dict:
        """A speech step that names the listener instead of the message says the goal's message."""
        if not (cx.role["say"] and st.get("action") == cx.role["say"] and cx.rules.get(st["action"], {}).get("near") and st.get("object") in cx.env):
            return {}
        g = self._say_goal(st, cx)
        return {"object": g["object"]} if g and g.get("object") and g["object"] != st.get("object") else {}


register(SPEC, PlanComplete)
