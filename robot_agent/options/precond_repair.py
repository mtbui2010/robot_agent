"""Option ``precond_repair`` (default off): a plan that lacks a step its next skill needs gets that step, before the gate sees the plan.

The planner writes a hand-over without the Pick that fills the hand, a Pick with a full hand, a Say from across the room. The certified gate
rejects such a plan and the run gives up. The pack declares what each skill needs as DATA, and this option splices the missing Find / Pick,
put-down or MoveTo, from a replay of the plan on the belief now. Nothing about a skill is in the code.

Pack data ``planner.options.precond_repair.needs``::

    {"rules": {<Action>: {"holding": bool, "hand_free": bool, "near": bool}}, "source": "<label>"}      # or "assumed": true instead of source

* ``holding``: the hand must hold an object. Missing: the Find / Pick of the one goal object no step picks (none or several = nothing, no guess).
* ``hand_free``: the hand must be empty. Missing: the held object is put down at the surface an ``on`` goal names for it (no such goal = nothing: a container may be closed).
* ``near``: the robot must be at the step's object (an ENV key). Missing: a MoveTo it.
* ``achieves`` (``"on"`` | ``"in"`` | ``"hand"``): this skill ends a goal of that kind from the held object (a put-down on a surface, a put-in a
  container, a hand-over). End of plan: when the replay ends holding the object of a goal of that kind that no step released, the
  skill is appended (``on`` / ``in``: after a MoveTo the goal's place, an ENV key, when the robot is not there; ``hand``: a goal with ``hand``). No such
  rule = nothing. Two goals for the held object with different kinds: the first goal's.

Roles used (``planner.action_roles``): ``move``, ``find``, ``pick``, ``put_down``; a role not declared = that repair is skipped (fail open). No
rules, no label, an action not in the rules, a plan that already meets the rule = the plan is returned unchanged. Hook ``on_plan``.
Keys: ``enabled`` (legacy ``precond_repair``), ``needs`` (dict, from the manifest). Lab flag ``precond_repair``: ``{action, need, index}``;
a MoveTo for the ``near`` need has its own flag ``precond_repair.near`` (same fields), so a pack that declares ``near`` apart can tell its effect.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key

NEEDS = ("hand_free", "holding", "near")
KINDS = ("on", "in", "hand")


def rules_of(needs) -> dict:
    """``{action: {need: True}}`` from the declared data; {} when it has no label (source or assumed) or is malformed."""
    if not isinstance(needs, dict) or not (isinstance(needs.get("source"), str) and needs["source"].strip() or needs.get("assumed") is True):
        return {}
    rules = needs.get("rules")
    if not isinstance(rules, dict):
        return {}
    out = {a: {n: True for n in NEEDS if r.get(n) is True} for a, r in rules.items() if isinstance(a, str) and isinstance(r, dict)}
    for a, r in rules.items():
        if a in out and r.get("achieves") in KINDS:
            out[a]["achieves"] = r["achieves"]
    return out


SPEC = OptionSpec(
    name="precond_repair",
    keys=(Key("enabled", "bool", False, aliases=("precond_repair",)), Key("needs", "dict", {}, sources=("manifest",))),
    hooks=("on_plan",),
    order=85,
    enabled="enabled",
    doc="A plan that lacks a step the next skill needs (a held object, a free hand, being next to the object) gets it, from the pack's declared skill needs.",
)


def lab_flag(need) -> str:
    """The lab flag of a splice: the option's name, ``<name>.near`` for the ``near`` need."""
    return f"{SPEC.name}.{need}" if need == "near" else SPEC.name


class _State:
    """The belief replayed over the plan: where the robot is, what it holds."""

    def __init__(self, world, roles, releases):
        self.at, self.hold, self.prev_at = world.arrived, world.holding, world.arrived
        self.roles, self.releases = roles, releases

    def apply(self, st) -> None:
        a, o = st.get("action"), st.get("object")
        if a == self.roles["move"]:
            self.prev_at, self.at = self.at, o
        elif a == self.roles["pick"]:
            self.hold = o
        elif a == self.roles["put_down"] or a in self.releases:
            self.hold = None


def _key_for(env, place):
    """The ENV key a goal's place names: the key itself, or the one key with that head; None when none or several."""
    if place in env:
        return place
    hits = [k for k in env if k.split("@")[0].strip() == place]
    return hits[0] if len(hits) == 1 else None


class PrecondRepair(Option):
    def on_plan(self, steps, run):
        """``steps`` with the missing steps spliced in; None when nothing is missing or nothing is declared."""
        rules = rules_of(self.cfg.get("needs") or resolve_key(SPEC, SPEC.key("needs"), {}, {}, getattr(self.svc.robot.manifest, "planner", None)))
        roles = {r: self.svc.role(r) for r in ("move", "find", "pick", "put_down")}
        if not rules or not steps or not (any(s.get("action") in rules for s in steps) or any(r.get("achieves") for r in rules.values())):
            return None
        rel = getattr(self.svc.robot.model_capabilities(), "release_actions", ()) if hasattr(self.svc.robot, "model_capabilities") else ()
        state, out = _State(run.world, roles, set(rel) | {self.svc.hand_action()} - {None} | {a for a, r in rules.items() if r.get("achieves")}), []
        for i, st in enumerate(steps):
            for need, add in self._missing(st, rules.get(st.get("action"), {}), state, steps, run, roles, out):
                out.extend(add)
                self.svc.lab(lab_flag(need), action=st.get("action"), need=need, index=i)
            out.append(st)
            state.apply(st)
        out.extend(self._finish(state, rules, run, roles, out))
        return out if len(out) != len(steps) else None

    def _finish(self, state, rules, run, roles, out) -> list:
        """End of plan: the held object of a goal no step released gets the pack's skill that achieves the goal (see ``achieves``)."""
        by_kind = {r["achieves"]: a for a, r in rules.items() if r.get("achieves")}
        env = getattr(run.places, "env", {}) or {}
        goals = [g for g in run.goals or [] if isinstance(g, dict) and g.get("object") == state.hold and g.get("type") in ("on", "in")]
        if state.hold is None or not goals:
            return []
        g = goals[0]
        kind = "hand" if g.get("hand") else g["type"]
        act, key = by_kind.get(kind), _key_for(env, g.get("to"))
        if not act or (kind != "hand" and not (key and roles["move"])):
            return []
        add = ([{"action": roles["move"], "object": key}] if kind != "hand" and state.at != key else []) + [{"action": act, "object": key or g.get("to")}]
        self.svc.lab(SPEC.name, action=act, need="achieves", index=len(out))
        return add

    def _missing(self, st, need, state, plan, run, roles, out):
        """(need, steps) pairs to put before ``st``; the replay state is advanced by each. A repair whose roles are not declared is skipped."""
        env = getattr(run.places, "env", {}) or {}
        goals = [g for g in run.goals or [] if isinstance(g, dict)]
        if need.get("hand_free") and state.hold is not None and roles["put_down"] and roles["move"]:
            g = next((g for g in goals if g.get("object") == state.hold and g.get("type") == "on" and not g.get("hand")), None)
            key = _key_for(env, g.get("to")) if g else None
            if key and state.hold != st.get("object"):
                add = ([] if state.at == key else [{"action": roles["move"], "object": key}]) + [{"action": roles["put_down"], "object": key}]
                for s in add:
                    state.apply(s)
                yield "hand_free", add
        if need.get("holding") and state.hold is None and roles["find"] and roles["pick"]:
            picked = {s.get("object") for s in plan if s.get("action") == roles["pick"]}
            cand = {g.get("object") for g in goals if g.get("type") in ("on", "in") and g.get("object")} - picked
            if len(cand) == 1:
                obj = cand.pop()
                add = [{"action": roles["find"], "object": obj}, {"action": roles["pick"], "object": obj}]
                back = out.pop() if out and roles["move"] and out[-1].get("action") == roles["move"] and out[-1].get("object") == st.get("object") else None
                if back:
                    state.at = state.prev_at
                for s in add:
                    state.apply(s)
                yield "holding", add + ([back] if back else [])
                if back:
                    state.apply(back)
        if need.get("near") and roles["move"] and st.get("object") in env and state.at != st.get("object"):
            add = [{"action": roles["move"], "object": st["object"]}]
            state.apply(add[0])
            yield "near", add


register(SPEC, PrecondRepair)
